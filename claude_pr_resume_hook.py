#!/usr/bin/env python3
"""
Claude Code PostToolUse hook: after a session opens, edits, comments on or
reviews a pull request, make sure the PR description ends with a line that lets
you resume the Claude Code session that touched it.

Reads the Claude Code hook event JSON from stdin, and if it was one of those
calls - `gh pr create|edit|comment|review`, or the GitHub MCP server's matching
tools - appends (or replaces) a trailing footer:

    ---

    <details data-generator="caseycs/claude-pr-resume-hook" data-updated="2026-09-24T09:28:00+02:00">
    <summary>AI session - your-github-login (session name), 24 September 2026 09:28 CEST, Opus5.5/high</summary>

    ```
    cd ~/path/to/worktree; claude -r <session_id>
    ```

    </details>

The directory and session come straight from the hook event, so the footer
always points at the session that produced the PR. Paths under $HOME are
written tilde-relative so the footer never publishes a local username.

Footers are keyed on the authenticated GitHub login plus the session, one block
per session: a PR touched by several people, or by one person from several
sessions, carries a block each, and every run adds or updates only its own. The
summary records the session's name, when it last touched the PR and the
model/effort it ran on, all read from the session transcript. Blocks are kept in
time order, oldest first, and blocks lost to a description edit are restored
from GitHub's edit history - see docs/adr/0009.

Run with no arguments to act as the hook. Run `install` / `uninstall` to
register or remove the hook in a Claude Code settings file.
"""
import argparse
import copy
import datetime
import getpass
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

GH_PR_COMMAND_RE = re.compile(r"\bgh\s+pr\s+(create|edit|comment|review)\b")
GH_ISSUE_COMMAND_RE = re.compile(r"\bgh\s+issue\s+(create|edit|comment)\b")
PR_URL_RE = re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
# A PR or an issue. `gh issue comment` accepts a PR number too, and then prints
# a /pull/ URL, so the issue routes take whichever the URL says.
ISSUE_OR_PR_URL_RE = re.compile(r"https://github\.com/([^/\s\"]+)/([^/\s\"]+)/(pull|issues)/(\d+)")
# The REST collection each URL kind lives under.
REST_KIND = {"pull": "pulls", "issues": "issues"}

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
# The current footer: a collapsed <details> block whose summary names whose
# session it is, optionally followed by the session name, when and on what
# model. One per user and session - see docs/adr/0006 and 0008. Logins never
# contain commas or parentheses, so the login ends at the first of either.
FOOTER_DETAILS_RE = re.compile(
    r"[ \t]*<details(?P<attrs>(?:[ \t][^>\n]*)?)>[ \t]*\n"
    r"[ \t]*<summary>[ \t]*AI session[ \t]*-[ \t]*(?P<user>[^<\n,(]*?)[ \t]*"
    r"(?:[,(][^<\n]*)?</summary>"
    r".*?"
    r"</details>[ \t]*",
    re.DOTALL | re.IGNORECASE,
)
# Every block this tool writes names it, so it can tell its own blocks from a
# look-alike written by something else. Blocks from before 0.4 carry no
# generator at all and still count as ours.
FOOTER_GENERATOR = "caseycs/claude-pr-resume-hook"
GENERATOR_ATTR_RE = re.compile(r'\bdata-generator="([^"]*)"', re.IGNORECASE)
# When a footer was last written, machine-readable, for ordering blocks. Blocks
# from before it existed fall back to the date in their summary.
FOOTER_UPDATED_RE = re.compile(r'<details[^>\n]*\bdata-updated="([^"]+)"', re.IGNORECASE)
SUMMARY_DATE_RE = re.compile(r"<summary>[^<\n]*?, (\d{1,2} [A-Za-z]+ \d{4} \d{2}:\d{2})")
# A `gh pr|issue edit` that rewrites the description, rather than only the title or labels.
GH_BODY_FLAG_RE = re.compile(r"(?:^|\s)(?:-b|--body|-F|--body-file)(?=[\s=]|$)")
# Only an edit this recent can be the one that just ran; see previous_body().
EDIT_WINDOW = datetime.timedelta(minutes=10)
PREVIOUS_BODY_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issueOrPullRequest(number: $number) {
      ... on Issue { userContentEdits(last: 2) { nodes { editedAt diff } } }
      ... on PullRequest { userContentEdits(last: 2) { nodes { editedAt diff } } }
    }
  }
}
"""
# The session a footer resumes, read back out of its command.
FOOTER_SESSION_RE = re.compile(r"claude -r (\S+)")
# The date suffix on a pinned model id, e.g. the 20251001 in claude-haiku-4-5-20251001.
MODEL_DATE_RE = re.compile(r"^\d{8}$")
# Transcript entries worth parsing; everything else is skipped unparsed.
TRANSCRIPT_MARKERS = (b'"assistant"', b'"custom-title"', b'"ai-title"')
# Long session names are cut so the summary stays one readable line.
SESSION_NAME_MAX = 60
# A thematic break left dangling once the footers below it are lifted out.
TRAILING_RULE_RE = re.compile(r"\n+[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*\s*\Z")
# Characters safe to leave bare in a shell word. Everything else gets a
# backslash, which keeps `~` expanding and `/` readable.
SHELL_UNSAFE_RE = re.compile(r"([^\w@%+=:,./-])")
API_ROOT = "https://api.github.com"

CONSOLE_SCRIPT = "claude-pr-resume-hook"
TOOL_SOURCE = "git+https://github.com/caseycs/claude-pr-resume-hook"
# Bash subcommands the hook fires on, turned into Claude Code `if` filters.
# `if` is an optimisation, not a guarantee: it fails open when Claude Code
# cannot parse the command, so run_hook() re-checks with GH_PR_COMMAND_RE and
# GH_ISSUE_COMMAND_RE.
MATCHED_COMMANDS = (
    "gh pr create",
    "gh pr edit",
    "gh pr comment",
    "gh pr review",
    "gh issue create",
    "gh issue edit",
    "gh issue comment",
)
# `gh pr review` flags that take a value, so their value is not the PR selector.
REVIEW_VALUE_FLAGS = ("-b", "--body", "-F", "--body-file", "-R", "--repo")
# Where a shell command ends and the next begins, as shlex splits them.
SHELL_OPERATORS = frozenset({"&&", "||", ";", "|", "&", ";;", "(", ")"})
# The GitHub MCP server's tools that write to a PR or an issue: open or edit
# it, comment on it, review it, reply in a review thread. `install` pins the
# server key, since
# it cannot know how yours is configured; the regex below stays tolerant so a
# hand-widened matcher (a renamed server, or a plugin-bundled one named
# `mcp__plugin_<plugin>_<server>__…`) still works.
MCP_SERVER = "github"
MCP_TOOLS = (
    "create_pull_request",
    "update_pull_request",
    "add_issue_comment",
    "pull_request_review_write",
    "add_reply_to_pull_request_comment",
    "issue_write",
)
MCP_MATCHER = "mcp__{}__({})".format(MCP_SERVER, "|".join(MCP_TOOLS))
MCP_TOOL_RE = re.compile(r"^mcp__.+__(?P<tool>{})$".format("|".join(MCP_TOOLS)))

# Every settings entry install maintains: which tool event to match, how to
# narrow it, and what to call it when reporting.
HOOK_TARGETS = tuple(
    {"matcher": "Bash", "if": f"Bash({command}*)", "label": command}
    for command in MATCHED_COMMANDS
) + ({"matcher": MCP_MATCHER, "if": None, "label": "github mcp tools"},)
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
    """The local account name. Only a fallback - see github_login()."""
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def footer_user(token):
    """Whose session a footer belongs to: the authenticated GitHub login.

    This is the identity the footer is keyed on, so it has to be the *token's*
    user rather than the PR author - editing someone else's PR must update your
    own block, not theirs. Falls back to the local account name if the call
    fails, which can produce a second block when the two names differ.
    """
    try:
        login = api_request("GET", "/user", token).get("login")
    except Exception as e:
        print(f"{CONSOLE_SCRIPT}: could not read GitHub login ({e})", file=sys.stderr)
        login = None
    if login:
        return login
    fallback = local_user()
    print(
        f"{CONSOLE_SCRIPT}: falling back to local username {fallback!r}; "
        "a footer under your GitHub login may already exist",
        file=sys.stderr,
    )
    return fallback


def now():
    """The local time, with its zone. A function so tests can pin it."""
    return datetime.datetime.now().astimezone()


def model_label(model_id):
    """A short model name: claude-fable-5-5 -> Fable5.5, claude-haiku-4-5-20251001 -> Haiku4.5."""
    bare = re.sub(r"\[[^\]]*\]$", "", model_id.strip())
    parts = [p for p in bare.split("-") if p and not MODEL_DATE_RE.match(p)]
    if parts and parts[0].lower() == "claude":
        parts = parts[1:]
    names = [p for p in parts if not p.isdigit()]
    numbers = [p for p in parts if p.isdigit()]
    if not names:
        return bare
    return "".join(n.capitalize() for n in names) + ".".join(numbers)


def read_transcript(transcript_path):
    """(model, effort, name) for a session, each None when the transcript lacks it.

    Hook events carry none of these, but the transcript does: every assistant
    entry records its model and effort, and titles are appended as they change -
    `custom-title` from /rename, `ai-title` generated by Claude Code. The latest
    of each wins; a name you chose beats a generated one. The PR was written by
    the turn in progress, so the last assistant entry is the one that counts.
    """
    model = effort = custom_title = ai_title = None
    if not transcript_path:
        return None, None, None
    try:
        with open(transcript_path, "rb") as f:
            for line in f:
                # Titles can sit anywhere in a long transcript, so read it all,
                # but only parse the few lines that can matter.
                if not any(marker in line for marker in TRANSCRIPT_MARKERS):
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                kind = entry.get("type")
                if kind == "custom-title":
                    custom_title = entry.get("customTitle") or custom_title
                elif kind == "ai-title":
                    ai_title = entry.get("aiTitle") or ai_title
                elif kind == "assistant":
                    message = entry.get("message")
                    turn_model = message.get("model") if isinstance(message, dict) else None
                    # Claude Code writes locally generated messages under a placeholder model.
                    if not isinstance(turn_model, str) or not turn_model or turn_model.startswith("<"):
                        continue
                    turn_effort = entry.get("effort")
                    model = turn_model
                    effort = turn_effort if isinstance(turn_effort, str) and turn_effort else None
    except OSError:
        return None, None, None
    name = custom_title if isinstance(custom_title, str) else ai_title
    return model, effort, name if isinstance(name, str) else None


def session_name(name):
    """A session name made safe for one line of a <summary>."""
    text = " ".join((name or "").split())
    if len(text) > SESSION_NAME_MAX:
        text = text[: SESSION_NAME_MAX - 1].rstrip() + "…"
    return html.escape(text, quote=False)


def session_stamp(when=None, model=None, effort=None, name=None):
    """The summary suffix: ` (name), 24 September 2026 09:28 CEST, Opus5.5/high`."""
    named = session_name(name)
    parts = []
    if when is not None:
        parts.append(f"{when.day} {when:%B %Y %H:%M %Z}".strip())
    runs_on = "/".join(x for x in (model and model_label(model), effort) if x)
    if runs_on:
        parts.append(runs_on)
    return (f" ({named})" if named else "") + "".join(f", {part}" for part in parts)


def footer_for(cwd, session_id, user, stamp="", updated=None):
    command = f"cd {display_cwd(cwd)}; claude -r {shell_escape(session_id)}"
    attrs = f' data-generator="{FOOTER_GENERATOR}"'
    if updated:
        attrs += f' data-updated="{updated.isoformat(timespec="seconds")}"'

    return (
        f"<details{attrs}>\n"
        f"<summary>AI session - {user}{stamp}</summary>\n"
        "\n"
        f"```\n{command}\n```\n"
        "\n"
        "</details>"
    )


def strip_legacy_footers(body):
    """Remove footers written before the <details> format.

    Those schemes only ever kept one footer per PR, so whoever wrote it, it is
    superseded by the per-user block this run is about to write.
    """
    without_blocks = FOOTER_BLOCK_RE.sub("\n", body)
    without_headings = FOOTER_LINE_RE.sub("", without_blocks)
    return INLINE_FOOTER_RE.sub("", without_headings)


def split_footers(body):
    """Separate a body into (prose, [(user, session, footer_text), ...]).

    Footers are lifted out in the order they appear. A look-alike block naming
    a different generator isn't ours, and stays in the prose untouched.
    """
    footers = []

    def lift(match):
        generator = GENERATOR_ATTR_RE.search(match.group("attrs"))
        if generator and generator.group(1) != FOOTER_GENERATOR:
            return match.group(0)
        session = FOOTER_SESSION_RE.search(match.group(0))
        footers.append((
            match.group("user").strip(),
            session.group(1) if session else None,
            match.group(0).strip(),
        ))
        return "\n"

    prose = FOOTER_DETAILS_RE.sub(lift, body)
    prose = TRAILING_RULE_RE.sub("", prose)
    return prose.rstrip(), footers


def same_user(a, b):
    """GitHub logins are case-insensitive."""
    return a.strip().lower() == b.strip().lower()


def footer_time(text):
    """When a footer was last written, as a sortable number, or None if unknown."""
    stamped = FOOTER_UPDATED_RE.search(text)
    if stamped:
        try:
            return datetime.datetime.fromisoformat(stamped.group(1)).timestamp()
        except ValueError:
            pass
    # Written before data-updated existed: the summary's date, zone ignored, is
    # close enough to order it among the others.
    dated = SUMMARY_DATE_RE.search(text)
    if dated:
        try:
            when = datetime.datetime.strptime(dated.group(1), "%d %B %Y %H:%M")
        except ValueError:
            return None
        return when.replace(tzinfo=datetime.timezone.utc).timestamp()
    return None


def footer_key(who, session):
    return who.strip().lower(), session


def build_body(body, cwd, session_id, user, stamp="", updated=None, previous=None):
    """The body with this session's footer written in.

    `previous` is the body as it was before the edit that triggered this run, if
    known: footers it had that the edit dropped are put back.
    """
    prose, footers = split_footers(strip_legacy_footers(normalize(body)))

    if previous:
        present = {footer_key(who, s) for who, s, _ in footers}
        _, before = split_footers(normalize(previous))
        footers += [f for f in before if footer_key(f[0], f[1]) not in present]

    ours = footer_for(cwd, session_id, user, stamp, updated)
    session = shell_escape(session_id)

    def is_ours(who, their_session):
        return same_user(who, user) and their_session == session

    # Rewrite this session's block; another session - yours or anyone else's -
    # keeps its own, and a new session is added.
    blocks = [ours if is_ours(who, s) else text for who, s, text in footers]
    if not any(is_ours(who, s) for who, s, _ in footers):
        blocks.append(ours)

    # Oldest first. The sort is stable, so blocks with no known time stay put,
    # ahead of every dated one.
    blocks.sort(key=lambda text: (footer_time(text) is not None, footer_time(text) or 0))

    blocks = "\n\n".join(blocks)
    if prose:
        return f"{prose}\n\n---\n\n{blocks}\n"
    return f"{blocks}\n"


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


def pr_from_bash(event):
    """(owner, repo, number) for a `gh pr create|edit|comment|review` call, or None."""
    command = event.get("tool_input", {}).get("command", "")
    found = GH_PR_COMMAND_RE.search(command)
    if not found:
        return None
    response = event.get("tool_response")
    stdout = response.get("stdout") if isinstance(response, dict) else None
    # create and edit print the PR URL, comment prints the comment's URL - which
    # is the PR URL plus an anchor. No URL means the command failed, or was `--web`.
    match = PR_URL_RE.search(stdout or "")
    if match:
        return match.groups()
    if found.group(1) == "review":
        # gh pr review prints nothing when stdout isn't a terminal, which under
        # Claude Code it never is, so ask gh which PR the command meant.
        return pr_from_review_command(command[found.start():], event.get("cwd"))
    return None


def review_args(command):
    """The arguments of the `gh pr review` that starts `command`, up to the next shell operator."""
    for text in (command, command.split("\n", 1)[0]):
        try:
            lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            tokens = list(lexer)
            break
        except ValueError:
            # Unbalanced quoting, typically a heredoc body; the selector and
            # --repo are on the first line in practice.
            continue
    else:
        return []
    args = []
    for token in tokens[3:]:
        if token in SHELL_OPERATORS:
            break
        args.append(token)
    return args


def pr_from_review_command(command, cwd):
    """(owner, repo, number) for the PR a `gh pr review` command named, via `gh pr view`."""
    selector = repo = None
    args = iter(review_args(command))
    for arg in args:
        if arg in ("-R", "--repo"):
            repo = next(args, None)
        elif arg.startswith("--repo="):
            repo = arg.split("=", 1)[1]
        elif arg in REVIEW_VALUE_FLAGS:
            next(args, None)
        elif arg.startswith("-"):
            continue
        elif selector is None:
            selector = arg
    view = ["gh", "pr", "view"]
    if selector:
        view.append(selector)
    if repo:
        view += ["--repo", repo]
    view += ["--json", "url", "--jq", ".url"]
    try:
        out = subprocess.run(
            view, cwd=cwd or None, capture_output=True, text=True, timeout=15, check=True
        )
    except Exception:
        return None
    match = PR_URL_RE.search(out.stdout)
    return match.groups() if match else None


def pr_from_input(tool_input, number_key="pullNumber"):
    """(owner, repo, number) from an MCP tool's named input fields, or None."""
    if not isinstance(tool_input, dict):
        return None
    owner, repo, number = (
        tool_input.get("owner"),
        tool_input.get("repo"),
        tool_input.get(number_key),
    )
    if not owner or not repo or not isinstance(number, (int, float)):
        return None
    return str(owner), str(repo), str(int(number))


def pr_from_mcp(event, tool):
    """(owner, repo, number) for a GitHub MCP PR write, or None."""
    tool_input = event.get("tool_input")
    # Serialize the whole response rather than reaching into it: how Claude Code
    # nests MCP content is undocumented, and this works for a text block, a list
    # of blocks, a bare string or a dict alike.
    try:
        blob = json.dumps(event.get("tool_response"))
    except (TypeError, ValueError):
        blob = ""

    if tool == "pull_request_review_write":
        # Returns only a status sentence; the input names the PR. Throwing away a
        # pending review leaves nothing on the PR to point back from.
        if isinstance(tool_input, dict) and tool_input.get("method") == "delete_pending":
            return None
        return pr_from_input(tool_input)

    if tool == "add_reply_to_pull_request_comment":
        target = pr_from_input(tool_input)
        if target:
            return target
        match = re.search(r"https://github\.com/([^/\s\"]+)/([^/\s\"]+)/pull/(\d+)#discussion_r", blob)
        return match.groups() if match else None

    # create/update_pull_request return JSON like
    # {"id": "...", "url": "https://github.com/owner/repo/pull/1"}. Note `id` is
    # GitHub's database id, not the PR number, so the URL is the only usable source.
    match = PR_URL_RE.search(blob)
    if match:
        return match.groups()

    # update_pull_request names its target in the input, so fall back to those
    # fields if a future server version stops returning the URL. Read them by
    # name only - never regex tool_input, because a PR body legitimately contains
    # other PRs' URLs ("closes .../pull/5") and we would patch the wrong one.
    if tool == "update_pull_request":
        return pr_from_input(tool_input)
    return None


def issue_from_bash(event):
    """(owner, repo, number, kind) for a `gh issue create|edit|comment` call, or None."""
    command = event.get("tool_input", {}).get("command", "")
    if not GH_ISSUE_COMMAND_RE.search(command):
        return None
    response = event.get("tool_response")
    stdout = response.get("stdout") if isinstance(response, dict) else None
    # All three print the issue URL - comment with a #issuecomment anchor. No URL
    # means the command failed, or was `--web`.
    match = ISSUE_OR_PR_URL_RE.search(stdout or "")
    if not match:
        return None
    owner, repo, kind, number = match.groups()
    return owner, repo, number, REST_KIND[kind]


def target_from_bash(event):
    """(owner, repo, number, kind) for any Bash route, or None."""
    pr = pr_from_bash(event)
    if pr:
        return pr + ("pulls",)
    return issue_from_bash(event)


def target_from_mcp(event, tool):
    """(owner, repo, number, kind) for a GitHub MCP write to a PR or issue, or None."""
    tool_input = event.get("tool_input")
    try:
        blob = json.dumps(event.get("tool_response"))
    except (TypeError, ValueError):
        blob = ""

    if tool == "add_issue_comment":
        # Used on PRs and issues alike; the comment URL says which it landed on.
        # Checked against the input rather than searched for, since an older
        # server echoes the comment body, which may link elsewhere.
        target = pr_from_input(tool_input, "issue_number")
        if not target:
            return None
        owner, repo, number = target
        landed = re.search(
            r"https://github\.com/{}/{}/(pull|issues)/{}#issuecomment-".format(
                re.escape(owner), re.escape(repo), number
            ),
            blob,
            re.IGNORECASE,
        )
        return target + (REST_KIND[landed.group(1)],) if landed else None

    if tool == "issue_write":
        if not isinstance(tool_input, dict):
            return None
        if tool_input.get("method") == "update":
            target = pr_from_input(tool_input, "issue_number")
            return target + ("issues",) if target else None
        # create returns {"id": ..., "url": ".../issues/N"}; the id is not the
        # number. Anchored on the input's repo, like the comment above.
        owner, repo = tool_input.get("owner"), tool_input.get("repo")
        if not owner or not repo:
            return None
        created = re.search(
            r"https://github\.com/({})/({})/issues/(\d+)".format(
                re.escape(str(owner)), re.escape(str(repo))
            ),
            blob,
            re.IGNORECASE,
        )
        return created.groups() + ("issues",) if created else None

    pr = pr_from_mcp(event, tool)
    return pr + ("pulls",) if pr else None


def edits_body(event):
    """Whether the call rewrote the description, and so may have dropped footers."""
    tool_name = event.get("tool_name") or ""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    if tool_name == "Bash":
        command = tool_input.get("command") or ""
        for command_re in (GH_PR_COMMAND_RE, GH_ISSUE_COMMAND_RE):
            found = command_re.search(command)
            if found and found.group(1) == "edit" and GH_BODY_FLAG_RE.search(command[found.end():]):
                return True
        return False
    mcp_tool = MCP_TOOL_RE.match(tool_name)
    if not mcp_tool or "body" not in tool_input:
        return False
    tool = mcp_tool.group("tool")
    return tool == "update_pull_request" or (
        tool == "issue_write" and tool_input.get("method") == "update"
    )


def parse_github_time(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))


def previous_body(owner, repo, number, token, current_body):
    """The description as it was before the edit that produced `current_body`, or None.

    GitHub keeps every revision of a description; each edit's `diff` is in fact
    the full text after that edit. Only trusted when the newest revision is the
    current body and was written moments ago - otherwise the edit that just ran
    didn't touch the description, and the one before it may be a person's
    deliberate removal, which must stay removed.
    """
    try:
        data = api_request("POST", "/graphql", token, {
            "query": PREVIOUS_BODY_QUERY,
            "variables": {"owner": owner, "repo": repo, "number": int(number)},
        })
        nodes = data["data"]["repository"]["issueOrPullRequest"]["userContentEdits"]["nodes"]
    except Exception as e:
        print(f"{CONSOLE_SCRIPT}: could not read the description's edit history ({e})", file=sys.stderr)
        return None
    try:
        nodes = sorted(
            (n for n in nodes if n and n.get("editedAt") and isinstance(n.get("diff"), str)),
            key=lambda n: parse_github_time(n["editedAt"]),
            reverse=True,
        )
        if len(nodes) < 2:
            return None
        latest, before = nodes[0], nodes[1]
        if normalize(latest["diff"]).strip() != current_body.strip():
            return None
        if now() - parse_github_time(latest["editedAt"]) > EDIT_WINDOW:
            return None
    except (TypeError, ValueError, KeyError):
        return None
    return normalize(before["diff"])


def run_hook():
    event = json.load(sys.stdin)

    tool_name = event.get("tool_name") or ""
    mcp_tool = MCP_TOOL_RE.match(tool_name)
    if tool_name == "Bash":
        target = target_from_bash(event)
    elif mcp_tool:
        target = target_from_mcp(event, mcp_tool.group("tool"))
    else:
        return 0

    if not target:
        return 0
    owner, repo, number, kind = target

    cwd = event.get("cwd")
    session_id = event.get("session_id")
    if not cwd or not session_id:
        return 0

    token = get_token()
    if not token:
        print(f"{CONSOLE_SCRIPT}: no GitHub token available (gh auth token failed)", file=sys.stderr)
        return 0

    # PRs and issues differ only in where their description lives.
    path = f"/repos/{owner}/{repo}/{kind}/{number}"
    try:
        item = api_request("GET", path, token)
    except urllib.error.HTTPError as e:
        print(f"{CONSOLE_SCRIPT}: failed to fetch the description ({e})", file=sys.stderr)
        return 0

    # Compare against the normalized body, so a body that already carries the
    # right footer never triggers a pointless PATCH over CRLF differences alone.
    current_body = normalize(item.get("body"))
    # A rewritten description may have dropped other sessions' footers - Claude
    # usually writes a new body from scratch - so recover them from the revision
    # before. Our own block is rewritten regardless.
    previous = previous_body(owner, repo, number, token, current_body) if edits_body(event) else None
    model, effort, name = read_transcript(event.get("transcript_path"))
    when = now()
    stamp = session_stamp(when, model, effort, name)
    new_body = build_body(
        current_body, cwd, session_id, footer_user(token), stamp, updated=when, previous=previous
    )
    if new_body == current_body:
        return 0

    try:
        api_request("PATCH", path, token, {"body": new_body})
    except urllib.error.HTTPError as e:
        print(f"{CONSOLE_SCRIPT}: failed to update the description ({e})", file=sys.stderr)
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
    """Remove every entry of ours, pruning groups left empty.

    Returns (matcher, entry) pairs - the matcher matters now that entries live in
    more than one group, and is lost once the entry is detached.
    """
    taken = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        kept = [h for h in group["hooks"] if not is_ours(h)]
        taken.extend((group.get("matcher"), h) for h in group["hooks"] if is_ours(h))
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
    """(matcher, entry) pairs for every hook target."""
    pairs = []
    for target in HOOK_TARGETS:
        entry = {"type": "command"}
        if target["if"]:
            entry["if"] = target["if"]
        entry["command"] = command
        pairs.append((target["matcher"], entry))
    return pairs


def target_label(matcher, filter_):
    for target in HOOK_TARGETS:
        if target["matcher"] == matcher and target["if"] == filter_:
            return target["label"]
    return None


def add_entries(groups, pairs):
    """Add each entry under its matcher, joining an existing group when there is one."""
    for matcher, entry in pairs:
        for group in groups:
            if (
                isinstance(group, dict)
                and group.get("matcher") == matcher
                and isinstance(group.get("hooks"), list)
            ):
                group["hooks"].append(entry)
                break
        else:
            groups.append({"matcher": matcher, "hooks": [entry]})


# --- verbose reporting -------------------------------------------------------

# Wide enough that the longest label ("  gh issue comment ") still gets
# its three dots, so every line's value starts in the same column.
_LABEL_WIDTH = 30


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
    by_key = {
        (matcher, entry.get("if")): entry
        for matcher, entry in existing
        if isinstance(entry, dict)
    }

    for target in HOOK_TARGETS:
        key = (target["matcher"], target["if"])
        previous = by_key.pop(key, None)
        if previous is None:
            report(target["label"], "adding", indent=1)
        elif previous.get("command") == shim:
            report(target["label"], "up to date", indent=1)
        else:
            report(target["label"], "stale path, updating", indent=1)
            detail("was", previous.get("command"))
            detail("now", shim)

    if by_key:
        report("legacy entries", f"{len(by_key)} removed", indent=1)

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
    for matcher, entry in removed:
        filter_ = entry.get("if") if isinstance(entry, dict) else None
        label = target_label(matcher, filter_)
        if label is None:
            # An entry from an older install, or a hand-edited one.
            label = filter_[len("Bash(") : -len("*)")] if (filter_ or "").startswith("Bash(") else "entry"
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
            "the descriptions of PRs a session opens, edits, comments on or reviews. "
            "With no arguments, reads a hook event on stdin."
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
