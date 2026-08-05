# Requirements

No third-party Python packages — the script only uses the standard library
(`json`, `os`, `re`, `subprocess`, `sys`, `urllib`).

## Runtime

- **Python 3.8+** (developed/tested against 3.12).
- **`gh` CLI**, installed and authenticated:
  - `gh auth status` must succeed.
  - `gh auth token` must be able to print a valid token — the hook uses this
    to call the GitHub REST API. It never runs `gh pr view`/`gh pr edit` for
    reading or writing the PR body.
  - Alternatively, set `$GH_TOKEN` or `$GITHUB_TOKEN` in the environment and
    the hook will use that instead of shelling out to `gh`.
- **Token scope**: `repo` scope (or fine-grained equivalent) on any repo
  whose PRs this hook will touch — it needs to read and update PR
  descriptions via `GET`/`PATCH /repos/{owner}/{repo}/pulls/{number}`.
- **Network access** to `api.github.com`.

## Claude Code

- A version that supports the `if` field on hook entries (subcommand
  filtering like `Bash(gh pr create*)`), and `PostToolUse` hooks in general.
- The hook must be registered in a `settings.json`/`settings.local.json`
  under `hooks.PostToolUse` — see `README.md` for the exact config.
