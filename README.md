# claude-pr-resume-hook

A [Claude Code](https://code.claude.com) `PostToolUse` hook. Every time Claude
runs `gh pr create` or `gh pr edit` in a session, it makes sure the resulting
PR description ends with a line that lets you jump straight back into that
session later:

```
Resume session: `cd ~/github/my-repo; claude -r 85b3ce92-2e7b-432b-bf68-8ca769a1ad8a`
```

The directory and session id come from the hook event itself, so the footer
always points at the exact worktree and session that produced — or last
touched — the PR. Paths under `$HOME` are written tilde-relative, so the
footer never publishes your username.

## Install

Two commands. Requires [uv](https://docs.astral.sh/uv/) and an authenticated
`gh` (`gh auth status`).

```bash
uv tool install git+https://github.com/caseycs/claude-pr-resume-hook
claude-pr-resume-hook install
```

The first installs the tool; the second registers the hook in your user
settings — `$CLAUDE_CONFIG_DIR/settings.json`, or `~/.claude/settings.json`
when that variable isn't set. Restart Claude Code afterwards.

Upgrade with `uv tool upgrade claude-pr-resume-hook`. If the shim ever moves,
re-run `claude-pr-resume-hook install` to repoint the settings at it.

### install and uninstall

`install` is a reconciler, not an appender. It adds what's missing, rewrites
stale paths, removes entries from older installations, prunes matcher groups
it empties, reports every action, and writes **nothing at all** when settings
already match:

```
$ claude-pr-resume-hook install
uv tool ............ found  /Users/you/.local/bin/claude-pr-resume-hook
settings ........... /Users/you/.claude/settings.json
  gh pr create ..... up to date
  gh pr edit ....... stale path, updating
                       was  python3 /old/checkout/append_resume_footer.py
                       now  /Users/you/.local/bin/claude-pr-resume-hook
backup ............. settings.json.bak
result ............. updated - restart Claude Code

$ claude-pr-resume-hook install
result ............. already up to date, nothing written
```

`uninstall` is the mirror image: it removes this tool's entries from the
chosen scope, leaves everything else untouched, and prints the
`uv tool uninstall` line rather than running it.

Both commands take:

| Flag | Meaning |
| --- | --- |
| `--scope user` | user settings — `$CLAUDE_CONFIG_DIR` or `~/.claude` (default) |
| `--scope project` | `./.claude/settings.json` — checked in, applies to one repo |
| `--scope local` | `./.claude/settings.local.json` — untracked, applies to one repo |
| `--dry-run` | report what would change and print the resulting JSON, writing nothing |

`install` refuses to write settings pointing at a shim it cannot find, and
tells you the `uv tool install` line to run. Existing settings are backed up
to `settings.json.bak` before any real change; unrelated keys and hooks are
preserved.

<details>
<summary>Installing by hand, or without uv</summary>

The script has no third-party dependencies, so a plain checkout works too —
put this under `hooks` in the settings file of your choice:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "if": "Bash(gh pr create*)",
            "command": "python3 /path/to/claude_pr_resume_hook.py"
          },
          {
            "type": "command",
            "if": "Bash(gh pr edit*)",
            "command": "python3 /path/to/claude_pr_resume_hook.py"
          }
        ]
      }
    ]
  }
}
```

`install` and `uninstall` recognise hand-written entries like these and
reconcile them, so you can switch to the managed setup later without cleaning
up first.

</details>

## How it works

The hook reads the Claude Code hook event JSON from stdin:

1. Ignores anything that isn't a `Bash` call running `gh pr create` or
   `gh pr edit`.
2. Extracts the PR's `owner/repo/number` from the PR URL that `gh` prints to
   stdout on success. If `gh` didn't print a URL — the command failed, or was
   `--web` — it does nothing.
3. Fetches the current PR body and takes the session's `cwd`/`session_id`
   from the hook event, authenticating to the GitHub REST API with a token
   from `$GH_TOKEN`/`$GITHUB_TOKEN`, falling back to `gh auth token`.
4. Strips any previous footer and appends a fresh one, so the body always
   ends with exactly one.
5. If the body would be unchanged, skips the `PATCH` entirely.

The hook never blocks the tool call (`PostToolUse` can't block anyway) and
logs failures to stderr rather than raising, so a broken token or a network
hiccup never interrupts the session.

The `if` filters keep Claude Code from spawning the hook for most Bash calls,
but they are an optimisation rather than a guarantee: `if` matching **fails
open**, so when Claude Code cannot parse a compound command the hook runs
anyway. Correctness comes from the hook re-checking the command itself.

See [`docs/adr/`](./docs/adr) for why the hook talks to the REST API directly
instead of using `gh pr edit`, why settings name the shim by absolute path,
and why footer matching is looser than it looks. [`CONTEXT.md`](./CONTEXT.md)
defines the vocabulary.

### Footer handling

The footer is rewritten to stay correct rather than accumulating:

| Existing body | Result |
| --- | --- |
| no footer | footer appended |
| footer deleted by hand | footer appended again |
| footer from a different session or worktree | replaced in place |
| footer reworded, emphasised, quoted, or moved up the body | replaced with a clean one at the end |
| blank line before the footer deleted (one newline or both) | spacing restored |
| several stacked footers | collapsed to one |
| footer already correct | body left byte-for-byte alone, no API write |

Directories are rendered `~/…` when under `$HOME`, `~` when they *are* `$HOME`,
and absolute otherwise. Shell metacharacters get backslash-escaped rather than
quoted — `cd ~/my\ repo` — so the tilde still expands. CRLF line endings, which
the GitHub API returns, are normalized before comparison, so an unchanged body
never produces a spurious write.

## Requirements

- **Python 3.8+**, standard library only. `pytest` is needed just for the tests.
- **uv** for the recommended install path; not required if you register the
  script directly with `python3`.
- **`gh` CLI**, authenticated, unless you set `$GH_TOKEN`/`$GITHUB_TOKEN`
  yourself. Only `gh auth token` is ever invoked.
- **A token with `repo` scope** (or the fine-grained equivalent) on any repo
  whose PRs the hook will touch — it needs `GET` and `PATCH` on
  `/repos/{owner}/{repo}/pulls/{number}`.
- **Network access** to `api.github.com`.
- **A Claude Code version supporting `PostToolUse` hooks** and the `if` field
  on hook entries. `$CLAUDE_CONFIG_DIR` is honoured for user-scope installs.

## Development

```bash
git clone git@github.com:caseycs/claude-pr-resume-hook.git
cd claude-pr-resume-hook
uv run pytest                        # everything
uv run pytest -m 'not integration'   # skip the tests that install a real tool
```

To make your own hook run the working tree:

```bash
uv tool install --force -e .
claude-pr-resume-hook install
```

The suite covers the footer rules, the hook's behaviour on realistic Claude
Code events (with the GitHub API stubbed — no network, no real PRs), and
install/uninstall against isolated settings files. The `integration`-marked
tests in `tests/test_integration.py` run `uv tool install` for real, redirected
into a temp directory via `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR`, then execute the
exact command string `install` writes.

## Testing against a real PR

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"gh pr create --fill"},"tool_response":{"stdout":"https://github.com/owner/repo/pull/123\n"},"cwd":"/path/to/worktree","session_id":"test-session"}' \
  | claude-pr-resume-hook
```

This makes a real `GET`/`PATCH` against `owner/repo#123` if the URL is real and
your token has access — use a scratch repo and PR.

## License

MIT — see [LICENSE](./LICENSE).
