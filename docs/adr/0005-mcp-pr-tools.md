# Also fire on the GitHub MCP server's PR tools

A PR can be opened two ways in a Claude Code session: `gh pr create` through
Bash, or `mcp__github__create_pull_request` through the GitHub MCP server. The
hook covers both, so whether a PR is resumable does not depend on which tool
Claude happened to reach for. Two decisions here are not obvious from the code.

## The PR identity comes from the response, never from a regex over the input

`github-mcp-server` returns a text result holding JSON —
`{"id": "...", "url": "https://github.com/owner/repo/pull/N"}` — so the hook
serializes the whole `tool_response` and searches it for a PR URL. Serializing
rather than reaching into the structure is deliberate: how Claude Code nests MCP
content in `tool_response` is undocumented, and the same code then works for a
text block, a list of blocks, a bare string, or a plain dict.

Two traps this avoids:

- **`id` is GitHub's database id, not the PR number.** Using it would target a
  different PR or 404. The URL is the only usable source.
- **`tool_input` must never be regex-searched for a URL.** A PR body legitimately
  references other pull requests ("closes …/pull/5"), so a regex over the input
  would patch the wrong PR. The only input fallback reads the named `owner`,
  `repo` and `pullNumber` fields, and only for `update_pull_request`, where they
  identify the target unambiguously.

When neither yields a PR, the hook does nothing — which is also the correct
behaviour for insiders-mode MCP Apps UI, where `create_pull_request` returns
"the PR has NOT been created yet" and no PR exists to annotate.

## The matcher pins the server key; the script does not

`install` writes `matcher: "mcp__github__(create_pull_request|update_pull_request)"`.
A regex over the server segment (`mcp__.*__…`) was considered and rejected: it
would spawn the hook for a same-named tool on any unrelated server. The cost is
that the server key is guessed — a server keyed differently, or bundled in a
plugin as `mcp__plugin_<plugin>_github__…`, needs the matcher hand-edited.

The script's own check (`MCP_PR_TOOL_RE`) stays server-agnostic precisely so that
a hand-widened matcher works without a code change. Note that a bare
`mcp__github` matcher would match nothing at all — Claude Code compares matchers
with no regex metacharacters as exact strings.

## Consequences

Entries now span two matcher groups, so `install`/`uninstall` reconcile on
`(matcher, if)` pairs declared in `HOOK_TARGETS` rather than on `if` alone. The
MCP entry carries no `if`: its matcher already names the exact two tools.

No feedback loop: the hook writes through the REST API, never through MCP.
