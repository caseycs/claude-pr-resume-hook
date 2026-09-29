# Resolve gh targets from the command, not from what gh printed

The hook used to find the PR a `gh pr create|edit|comment` call touched by
searching its stdout for the first PR URL. A real session showed both ways that
fails:

- **The URL never arrives.** Claude routinely writes
  `gh pr edit 19 --body-file pr1.md >/dev/null && echo ok`. The URL goes to
  `/dev/null`, the hook concludes the command failed, and does nothing — while
  the edit has just replaced the description with Claude's local draft, which
  never contained the footer. In that session 12 of 16 edits did this, and
  cloudflare-terraform#19 lost its block for good.
- **The wrong URL arrives first.** Commands such as
  `gh pr edit 19 … && gh pr view 19 --json body | grep …` print links from the
  description. The hook took the first `/pull/N` it saw and updated that PR.

`gh pr review`, which prints nothing at all, was resolved by running
`gh pr view`, which broke [0001](./0001-rest-api-not-gh-subcommands.md).

## Decision

Resolve every `gh pr|issue <sub>` call from the command line itself:

1. Split the command into shell words (heredoc bodies dropped, newlines as
   separators, redirections removed) and walk it, following `cd` so each call
   knows its directory. Claude Code reports the session's cwd, not the shell's.
2. Parse the call's arguments with that subcommand's value flags, so a flag's
   value is never taken for the selector (`-r` takes a value for `pr create` but
   is `--request-changes` for `pr review`).
3. A URL selector is the target. Otherwise the repo is `--repo`, or the
   checkout's remote, in gh's own order: `upstream`, `github`, `origin`. A
   numeric selector is the number. A branch selector, `--head`, or the
   checkout's current branch is looked up with
   `GET /repos/{o}/{r}/pulls?head={o}:{branch}&state=open`.
4. Only when that fails, and the command made one call that printed exactly one
   link, use the link.

Every call in the command is handled, so `gh pr edit 19 … && gh pr edit 20 …`
updates both, and a PR touched twice is written once.

## Consequences

`gh` is back to `gh auth token` only. The local lookups are `git config --get`
and `git rev-parse --abbrev-ref HEAD`, which read the checkout and never touch
the network. The GitHub lookups use the REST API with the token the hook
already has.

Not resolvable: `cd` into a shell variable (`cd $WT`), forks whose PR lives
on a repo that isn't among those remotes, and `gh issue create` with its output
discarded (there is no branch to look an issue up by). These fall back to the
printed link, or do nothing.
