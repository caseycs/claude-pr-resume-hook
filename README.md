# claude-pr-resume-hook

A [Claude Code](https://code.claude.com) `PostToolUse` hook. Every time Claude
opens or edits a pull request in a session — through `gh pr create`/`gh pr edit`
or the GitHub MCP server — it makes sure the PR description ends with a block
that lets you jump straight back into that session later:

> ---
>
> <details>
> <summary>AI session - caseycs</summary>
>
> ```
> cd ~/github/my-repo; claude -r 85b3ce92-2e7b-432b-bf68-8ca769a1ad8a
> ```
>
> </details>

## Key features

- **A copy-pasteable command, not a lookup.** The footer carries the literal
  `cd <dir>; claude -r <session-id>`. You read it off the PR page and paste it
  into any terminal — nothing to resolve, no checkout required, no need to be in
  a particular directory first.
- **The directory is half the answer.** Both parts come from the hook event, so
  the command lands in the exact worktree that produced the PR. That matters if
  you run Claude under more than one `$HOME` to keep contexts separate: the `cd`
  puts you in the right one rather than resuming against whichever context
  happens to be current.
- **One block per person.** The summary names the **GitHub login** whose session
  it is. A colleague working the same PR from their own session gets a block
  alongside yours; each run only ever adds or updates its own, never touching
  anyone else's. Collapsed by default, so several stay out of the way.

## Why not `claude --from-pr`?

Claude Code ships `--from-pr`, which resumes a session linked to a PR by number
or URL, or via an interactive picker. If that covers your workflow, you may not
need this hook at all.

It doesn't fit every shape of work:

- **Tasks spanning several repositories.** When one task carries PRs across three
  or more repos, there is no single PR to resume "the" session from, and you are
  back to remembering which session belonged to which repo.
- **It needs repository context.** Resolving a PR to a session leans on the git
  repository you are sitting in. Reading a PR in the browser, on another machine,
  or from a directory that is not a checkout leaves nothing to resolve against.
- **Several Claude home directories.** If you keep separate `$HOME`s so Claude
  holds separate contexts, the session you want may not live in the context that
  is currently active — and nothing in a PR number says which one it is.

The footer sidesteps all three by writing the answer into the PR itself. It is a
plain shell command, so it travels: it works from any directory, on any machine
that holds the session, and it states out loud which context to `cd` into first.

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

To pin a release rather than track `main`:

<!-- x-release-please-start-version -->
```bash
uv tool install git+https://github.com/caseycs/claude-pr-resume-hook@v0.2.1
```
<!-- x-release-please-end -->

Releases are cut by [release-please](.github/workflows/release.yml): merging the
release PR it maintains tags that commit, publishes the GitHub Release, and
attaches the sdist and wheel. Version bumps come from
[conventional commits](https://www.conventionalcommits.org) — `feat:` and `fix:`
subjects are what move the version and appear in the changelog.

### install and uninstall

`install` is a reconciler, not an appender. It adds what's missing, rewrites
stale paths, removes entries from older installations, prunes matcher groups
it empties, reports every action, and writes **nothing at all** when settings
already match:

```
$ claude-pr-resume-hook install
uv tool ...................... found  /Users/you/.local/bin/claude-pr-resume-hook
settings ..................... /Users/you/.claude/settings.json
  gh pr create ............... up to date
  gh pr edit ................. stale path, updating
                       was  python3 /old/checkout/append_resume_footer.py
                       now  /Users/you/.local/bin/claude-pr-resume-hook
  github mcp pull requests ... adding
backup ....................... settings.json.bak
result ....................... updated - restart Claude Code

$ claude-pr-resume-hook install
result ....................... already up to date, nothing written
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

1. Ignores anything that isn't one of the two routes a PR can be opened by:
   a `Bash` call running `gh pr create`/`gh pr edit`, or the GitHub MCP server's
   `create_pull_request`/`update_pull_request`.
2. Extracts the PR's `owner/repo/number` from the PR URL — printed to stdout by
   `gh`, or returned in the MCP tool's JSON result. If there's no URL — the
   command failed, `gh pr create --web` was used, or the MCP tool only asked for
   confirmation — it does nothing.
3. Fetches the current PR body and takes the session's `cwd`/`session_id`
   from the hook event, authenticating to the GitHub REST API with a token
   from `$GH_TOKEN`/`$GITHUB_TOKEN`, falling back to `gh auth token`.
4. Reads the token's own GitHub login via `GET /user` — that, not the PR
   author, is whose footer this run owns.
5. Rewrites its own block in place, or appends one, leaving every other
   person's block exactly where it was.
6. If the body would be unchanged, skips the `PATCH` entirely.

The hook never blocks the tool call (`PostToolUse` can't block anyway) and
logs failures to stderr rather than raising, so a broken token or a network
hiccup never interrupts the session.

The `if` filters keep Claude Code from spawning the hook for most Bash calls,
but they are an optimisation rather than a guarantee: `if` matching **fails
open**, so when Claude Code cannot parse a compound command the hook runs
anyway. Correctness comes from the hook re-checking the command itself.

### The GitHub MCP server

`install` writes a second matcher group for the MCP route:

```json
{
  "matcher": "mcp__github__(create_pull_request|update_pull_request)",
  "hooks": [{ "type": "command", "command": "/path/to/claude-pr-resume-hook" }]
}
```

The matcher needs no `if` filter — it already names the exact two tools. It does,
however, **assume your GitHub MCP server is keyed `github`**, which is the
default but is yours to choose. If yours differs, or it comes from a plugin (those
appear as `mcp__plugin_<plugin>_<server>__…`), edit that matcher by hand; the hook
itself recognises PR tools on any server, so no code change is needed. Note that a
bare `mcp__github` matches *nothing* — Claude Code compares metacharacter-free
matchers as exact strings, so the parenthesised group or a `.*` suffix is required.

See [`docs/adr/`](./docs/adr) for why the hook talks to the REST API directly
instead of using `gh pr edit`, why settings name the shim by absolute path,
and why footer matching is looser than it looks. [`CONTEXT.md`](./CONTEXT.md)
defines the vocabulary.

### Footer handling

The footer is rewritten to stay correct rather than accumulating:

| Existing body | Result |
| --- | --- |
| no footer of yours | yours appended |
| your footer deleted by hand | yours appended again |
| your session or worktree changed | your block rewritten in place |
| someone else's footer present | left untouched, yours added alongside |
| your footer already correct | body left byte-for-byte alone, no API write |
| footer from an earlier version's format | migrated to a `<details>` block |

Your block keeps its position when rewritten, so the order people appear in
never shuffles. Matching is case-insensitive, since GitHub logins are. An
unrelated `<details>`, fenced code block, or horizontal rule elsewhere in the
body is left alone; all three are pinned by tests.

Directories are rendered `~/…` when under `$HOME`, `~` when they *are* `$HOME`,
and absolute otherwise. Shell metacharacters get backslash-escaped rather than
quoted — `cd ~/my\ repo` — so the tilde still expands. The rule is always
preceded by a blank line, since `text` immediately above `---` is a setext H2 in
markdown rather than a horizontal rule. CRLF line endings, which the GitHub API
returns, are normalized before comparison, so an unchanged body never produces a
spurious write.

## Requirements

- **uv** for the recommended install path; not required if you register the
  script directly with `python3`.
- **`gh` CLI**, authenticated, unless you set `$GH_TOKEN`/`$GITHUB_TOKEN`
  yourself. Only `gh auth token` is ever invoked.
- **A token with `repo` scope** (or the fine-grained equivalent) on any repo
  whose PRs the hook will touch — it needs `GET` and `PATCH` on
  `/repos/{owner}/{repo}/pulls/{number}`.

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
