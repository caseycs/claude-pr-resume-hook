#!/usr/bin/env python3
"""
Claude Code PostToolUse hook: after `gh pr create` / `gh pr edit`, make sure
the PR description ends with a line that lets you resume the Claude Code
session that produced it.

Reads the Claude Code hook event JSON from stdin, and if it was a `gh pr
create`/`gh pr edit` Bash call, appends (or replaces) a trailing footer:

    Resume session: `cd <cwd>; claude -r <session_id>`

<cwd> and <session_id> come straight from the hook event, so the footer
always points at the session and directory that produced the PR.
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

GH_PR_COMMAND_RE = re.compile(r"\bgh\s+pr\s+(create|edit)\b")
PR_URL_RE = re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
FOOTER_RE = re.compile(r"\n*Resume session: `cd .*?; claude -r .*?`\s*\Z", re.DOTALL)
API_ROOT = "https://api.github.com"


def get_token():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:
        out = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def api_request(method, path, token, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{API_ROOT}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def build_body(body, cwd, session_id):
    stripped = FOOTER_RE.sub("", body or "").rstrip()
    footer = f"Resume session: `cd {cwd}; claude -r {session_id}`"
    if stripped:
        return f"{stripped}\n\n{footer}\n"
    return f"{footer}\n"


def main():
    event = json.load(sys.stdin)

    if event.get("tool_name") != "Bash":
        return

    command = event.get("tool_input", {}).get("command", "")
    if not GH_PR_COMMAND_RE.search(command):
        return

    stdout = event.get("tool_response", {}).get("stdout") or ""
    match = PR_URL_RE.search(stdout)
    if not match:
        # gh didn't print a PR URL (e.g. the command failed) - nothing to do.
        return
    owner, repo, number = match.group(1), match.group(2), match.group(3)

    cwd = event.get("cwd")
    session_id = event.get("session_id")
    if not cwd or not session_id:
        return

    token = get_token()
    if not token:
        print("claude-pr-resume-hook: no GitHub token available (gh auth token failed)", file=sys.stderr)
        return

    pr_path = f"/repos/{owner}/{repo}/pulls/{number}"
    try:
        pr = api_request("GET", pr_path, token)
    except urllib.error.HTTPError as e:
        print(f"claude-pr-resume-hook: failed to fetch PR body ({e})", file=sys.stderr)
        return

    current_body = pr.get("body") or ""
    new_body = build_body(current_body, cwd, session_id)
    if new_body == current_body:
        return

    try:
        api_request("PATCH", pr_path, token, {"body": new_body})
    except urllib.error.HTTPError as e:
        print(f"claude-pr-resume-hook: failed to update PR body ({e})", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"claude-pr-resume-hook: unexpected error: {e}", file=sys.stderr)
