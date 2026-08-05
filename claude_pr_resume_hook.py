#!/usr/bin/env python3
"""
Claude Code PostToolUse hook: after `gh pr create` / `gh pr edit`, make sure
the PR description ends with a line that lets you resume the Claude Code
session that produced it.

Reads the Claude Code hook event JSON from stdin, and if it was a `gh pr
create`/`gh pr edit` Bash call, appends (or replaces) a trailing footer:

    ---

    Resume Claude session by `you`:
    ```
    cd ~/path/to/worktree; claude -r <session_id>
    ```

The directory and session come straight from the hook event, so the footer
always points at the session that produced the PR. Paths under $HOME are
written tilde-relative so the footer never publishes a username.

Run with no arguments to act as the hook. Run `install` / `uninstall` to
register or remove the hook in a Claude Code settings file.
"""
import argparse
import copy
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

GH_PR_COMMAND_RE = re.compile(r"\bgh\s+pr\s+(create|edit)\b")
PR_URL_RE = re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")

# The heading line of a footer, in any of its hand-edited guises. Tolerates the
# older wording ("Resume session:") so footers written by earlier versions are
# replaced rather than stacked.
_HEADING = r"[*_]{0,2}Resume\s+(?:Claude\s+)?session\b[^\n]*"
_RULE = r"(?:-{3,}|\*{3,}|_{3,})[ \t]*"

# The current footer shape: an optional thematic break, a heading line, and a
# fenced code block holding the command. Matched wherever it sits in the body.
# Deliberately loose - see docs/adr/0003.
FOOTER_BLOCK_RE = re.compile(
    r"(?:^|\n)\s*"
    r"(?:" + _RULE + r"\s*)?"
    r"[ \t>]*" + _HEADING + r"\n[ \t>]*"
    r"(`{3,}|~{3,})[^\n]*\n"
    r".*?"
    r"\n[ \t>]*\1[ \t]*",
    re.DOTALL | re.IGNORECASE,
)
# Any remaining footer heading, on its own line: an older single-line footer, or
# a block whose fenced command was deleted by hand.
FOOTER_LINE_RE = re.compile(r"^[ \t>]*" + _HEADING + r"$\n?", re.MULTILINE | re.IGNORECASE)
# An older single-line footer glued onto the end of a line, which is what you got
# if the blank line separating it from the body was deleted too. Matched only in
# its exact well-formed shape, so this never swallows prose that mentions it.
INLINE_FOOTER_RE = re.compile(
    r"[ \t]*[*_]{0,2}Resume\s+(?:Claude\s+)?session[^\n`]*`cd [^`\n]*; claude -r [^`\n]*`[*_]{0,2}",
    re.IGNORECASE,
)
# Characters safe to leave bare in a shell word. Everything else gets a
# backslash, which keeps `~` expanding and `/` readable.
SHELL_UNSAFE_RE = re.compile(r"([^\w@%+=:,./-])")
API_ROOT = "https://api.github.com"

CONSOLE_SCRIPT = "claude-pr-resume-hook"
TOOL_SOURCE = "git+https://github.com/caseycs/claude-pr-resume-hook"
# Bash subcommands the hook fires on, turned into Claude Code `if` filters.
# `if` is an optimisation, not a guarantee: it fails open when Claude Code
# cannot parse the command, so run_hook() re-checks with GH_PR_COMMAND_RE.
MATCHED_COMMANDS = ("gh pr create", "gh pr edit")
# A hook entry whose command mentions any of these belongs to us, and is
# reconciled on install rather than duplicated.
OURS_MARKERS = ("claude-pr-resume-hook", "claude_pr_resume_hook", "append_resume_footer")


# --- the footer --------------------------------------------------------------


def shell_escape(text):
    """Backslash-escape shell metacharacters, leaving `/` and word chars bare."""
    return SHELL_UNSAFE_RE.sub(r"\\\1", text)


def display_cwd(cwd):
    """Render a directory for the footer, tilde-relative when under $HOME.

    Keeps the username out of PR descriptions, which are often public.
    """
    home = str(Path.home()).rstrip("/")
    if home and cwd == home:
        return "~"
    if home and cwd.startswith(home + "/"):
        return "~/" + shell_escape(cwd[len(home) + 1:])
    return shell_escape(cwd)


def normalize(body):
    """GitHub hands back PR bodies with CRLF line endings; we write LF."""
    return (body or "").replace("\r\n", "\n")


def local_user():
    """The local account name, used to say whose session the footer points at."""
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def footer_for(cwd, session_id, user=None):
    command = f"cd {display_cwd(cwd)}; claude -r {shell_escape(session_id)}"
    return f"Resume Claude session by `{user or local_user()}`:\n```\n{command}\n```"


def strip_footers(body):
    """Remove every footer, in any shape this tool has ever written or a human
    has since edited it into."""
    without_blocks = FOOTER_BLOCK_RE.sub("\n", body)
    without_headings = FOOTER_LINE_RE.sub("", without_blocks)
    return INLINE_FOOTER_RE.sub("", without_headings)


def build_body(body, cwd, session_id):
    stripped = strip_footers(normalize(body)).rstrip()
    footer = footer_for(cwd, session_id)
    if stripped:
        return f"{stripped}\n\n---\n\n{footer}\n"
    return f"{footer}\n"


# --- hook mode ---------------------------------------------------------------


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


def run_hook():
    event = json.load(sys.stdin)

    if event.get("tool_name") != "Bash":
        return 0

    command = event.get("tool_input", {}).get("command", "")
    if not GH_PR_COMMAND_RE.search(command):
        return 0

    stdout = event.get("tool_response", {}).get("stdout") or ""
    match = PR_URL_RE.search(stdout)
    if not match:
        # gh didn't print a PR URL (e.g. the command failed) - nothing to do.
        return 0
    owner, repo, number = match.group(1), match.group(2), match.group(3)

    cwd = event.get("cwd")
    session_id = event.get("session_id")
    if not cwd or not session_id:
        return 0

    token = get_token()
    if not token:
        print(f"{CONSOLE_SCRIPT}: no GitHub token available (gh auth token failed)", file=sys.stderr)
        return 0

    pr_path = f"/repos/{owner}/{repo}/pulls/{number}"
    try:
        pr = api_request("GET", pr_path, token)
    except urllib.error.HTTPError as e:
        print(f"{CONSOLE_SCRIPT}: failed to fetch PR body ({e})", file=sys.stderr)
        return 0

    # Compare against the normalized body, so a body that already carries the
    # right footer never triggers a pointless PATCH over CRLF differences alone.
    current_body = normalize(pr.get("body"))
    new_body = build_body(current_body, cwd, session_id)
    if new_body == current_body:
        return 0

    try:
        api_request("PATCH", pr_path, token, {"body": new_body})
    except urllib.error.HTTPError as e:
        print(f"{CONSOLE_SCRIPT}: failed to update PR body ({e})", file=sys.stderr)
    return 0


# --- settings files ----------------------------------------------------------


def user_config_dir():
    """Claude Code's own config dir: $CLAUDE_CONFIG_DIR if set, else ~/.claude."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude"


def settings_path(scope):
    if scope == "user":
        return user_config_dir() / "settings.json"
    # Project scopes are relative to the repo, so CLAUDE_CONFIG_DIR doesn't apply.
    if scope == "project":
        return Path.cwd() / ".claude" / "settings.json"
    return Path.cwd() / ".claude" / "settings.local.json"


def load_settings(path):
    if not path.exists():
        return {}
    try:
        settings = json.loads(path.read_text() or "{}")
    except json.JSONDecodeError as e:
        raise SystemExit(f"{CONSOLE_SCRIPT}: {path} is not valid JSON ({e})")
    if not isinstance(settings, dict):
        raise SystemExit(f"{CONSOLE_SCRIPT}: {path} does not contain a JSON object")
    return settings


def post_tool_use_groups(settings, create=False):
    """The PostToolUse matcher groups, without inventing keys unless asked."""
    hooks = settings.get("hooks")
    if hooks is None:
        if not create:
            return []
        hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SystemExit(f"{CONSOLE_SCRIPT}: `hooks` in settings is not an object")

    groups = hooks.get("PostToolUse")
    if groups is None:
        if not create:
            return []
        groups = hooks.setdefault("PostToolUse", [])
    if not isinstance(groups, list):
        raise SystemExit(f"{CONSOLE_SCRIPT}: `hooks.PostToolUse` in settings is not an array")
    return groups


def is_ours(entry):
    if not isinstance(entry, dict):
        return False
    command = entry.get("command") or ""
    return any(marker in command for marker in OURS_MARKERS)


def take_our_entries(groups):
    """Remove and return every entry of ours, pruning groups left empty."""
    taken = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        kept = [h for h in group["hooks"] if not is_ours(h)]
        taken.extend(h for h in group["hooks"] if is_ours(h))
        group["hooks"] = kept
    groups[:] = [g for g in groups if not isinstance(g, dict) or g.get("hooks")]
    return taken


def prune_empty(settings):
    """Drop hook containers we emptied, so uninstall leaves no residue."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    if isinstance(hooks.get("PostToolUse"), list) and not hooks["PostToolUse"]:
        del hooks["PostToolUse"]
    if not hooks:
        del settings["hooks"]


def desired_entries(command):
    return [
        {"type": "command", "if": f"Bash({c}*)", "command": command}
        for c in MATCHED_COMMANDS
    ]


def add_entries(groups, entries):
    """Append to the existing Bash matcher group, or create one."""
    for group in groups:
        if (
            isinstance(group, dict)
            and group.get("matcher") == "Bash"
            and isinstance(group.get("hooks"), list)
        ):
            group["hooks"].extend(entries)
            return
    groups.append({"matcher": "Bash", "hooks": entries})


# --- verbose reporting -------------------------------------------------------

_LABEL_WIDTH = 20


def report(label, value, indent=0):
    text = "  " * indent + label + " "
    print(text + "." * max(3, _LABEL_WIDTH - len(text)) + " " + str(value))


def detail(label, value, indent=3):
    print("  " * indent + f"{label}  {value}")


# --- install / uninstall -----------------------------------------------------


def find_shim():
    """Absolute path to the installed console script, or None."""
    found = shutil.which(CONSOLE_SCRIPT)
    return str(Path(found).resolve()) if found else None


def require_shim():
    shim = find_shim()
    if shim:
        return shim
    lines = [
        f"{CONSOLE_SCRIPT}: not found on PATH - install the tool first:",
        f"    uv tool install {TOOL_SOURCE}",
    ]
    if not shutil.which("uv"):
        lines.append("  (uv itself is missing too: https://docs.astral.sh/uv/)")
    raise SystemExit("\n".join(lines))


def write_settings(path, settings, changed, backup_note="backup"):
    """Persist settings, backing up first. No-ops are the caller's business."""
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(path.read_text())
        report(backup_note, backup.name)
    else:
        report(backup_note, "not needed (new file)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")
    return changed


def cmd_install(args):
    shim = require_shim()
    report("uv tool", f"found  {shim}")

    path = settings_path(args.scope)
    report("settings", path)

    settings = load_settings(path)
    original = copy.deepcopy(settings)

    groups = post_tool_use_groups(settings, create=True)
    existing = take_our_entries(groups)
    by_filter = {e.get("if"): e for e in existing if isinstance(e, dict)}

    for entry in desired_entries(shim):
        pattern = entry["if"]
        label = pattern[len("Bash(") : -len("*)")]
        previous = by_filter.pop(pattern, None)
        if previous is None:
            report(label, "adding", indent=1)
        elif previous.get("command") == shim:
            report(label, "up to date", indent=1)
        else:
            report(label, "stale path, updating", indent=1)
            detail("was", previous.get("command"))
            detail("now", shim)

    if by_filter:
        report("legacy entries", f"{len(by_filter)} removed", indent=1)

    add_entries(groups, desired_entries(shim))
    prune_empty(settings)

    if settings == original:
        report("result", "already up to date, nothing written")
        return 0
    if args.dry_run:
        report("result", "dry run, nothing written")
        print(json.dumps(settings, indent=2))
        return 0

    write_settings(path, settings, changed=True)
    report("result", "updated - restart Claude Code")
    return 0


def cmd_uninstall(args):
    path = settings_path(args.scope)
    report("settings", path)

    if not path.exists():
        report("result", "no settings file, nothing to do")
        return 0

    settings = load_settings(path)
    original = copy.deepcopy(settings)

    groups = post_tool_use_groups(settings)
    before_groups = len(groups)
    removed = take_our_entries(groups)
    for entry in removed:
        pattern = entry.get("if") or ""
        label = pattern[len("Bash(") : -len("*)")] if pattern.startswith("Bash(") else "entry"
        report(label, "removed", indent=1)
    dropped = before_groups - len(groups)
    if dropped:
        report("matcher groups", f"{dropped} emptied, dropped", indent=1)
    prune_empty(settings)

    if settings == original:
        report("result", "not installed, nothing written")
        return 0
    if args.dry_run:
        report("result", "dry run, nothing written")
        print(json.dumps(settings, indent=2))
        return 0

    write_settings(path, settings, changed=True)
    report("result", "removed - restart Claude Code")
    if find_shim():
        print(f"\nThe tool itself is still installed. To remove it too:\n    uv tool uninstall {CONSOLE_SCRIPT}")
    return 0


# --- entry point -------------------------------------------------------------


def add_scope_flags(parser):
    parser.add_argument(
        "--scope",
        choices=("user", "project", "local"),
        default="user",
        help="which settings file to act on: user ($CLAUDE_CONFIG_DIR or ~/.claude, "
        "the default), project (./.claude/settings.json), or local "
        "(./.claude/settings.local.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change and print the resulting JSON, without writing",
    )


def build_parser():
    parser = argparse.ArgumentParser(
        prog=CONSOLE_SCRIPT,
        description=(
            "Claude Code PostToolUse hook that appends a resume-session footer to "
            "gh PR descriptions. With no arguments, reads a hook event on stdin."
        ),
    )
    sub = parser.add_subparsers(dest="subcommand")

    install = sub.add_parser("install", help="register the hook in a Claude Code settings file")
    add_scope_flags(install)
    install.set_defaults(func=cmd_install)

    uninstall = sub.add_parser("uninstall", help="remove the hook from a Claude Code settings file")
    add_scope_flags(uninstall)
    uninstall.set_defaults(func=cmd_uninstall)

    hook = sub.add_parser("hook", help="run the hook explicitly (reads the event on stdin)")
    hook.set_defaults(func=lambda args: run_hook())

    return parser


def main():
    argv = sys.argv[1:]
    if not argv:
        # Hook mode: never let an error escape and disrupt the session.
        try:
            return run_hook()
        except Exception as e:
            print(f"{CONSOLE_SCRIPT}: unexpected error: {e}", file=sys.stderr)
            return 0

    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
