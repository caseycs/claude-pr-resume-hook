# Issues get footers too

A session often files or works an issue rather than a PR — reporting a bug it
found, updating an issue's plan, commenting on progress. The same way back into
the session is just as useful there, so issues are handled like PRs:

| Route | How the issue is found |
| --- | --- |
| `gh issue create`, `gh issue edit` | the issue URL they print |
| `gh issue comment` | the comment URL it prints, `…/issues/N#issuecomment-…` |
| MCP `issue_write` `create` | the URL in its `{id, url}` result, anchored on the input's owner/repo |
| MCP `issue_write` `update` | input `owner`/`repo`/`issue_number` |
| MCP `add_issue_comment` | input fields, confirmed by the comment URL |

## One code path, two REST collections

A target is now `(owner, repo, number, kind)`, where `kind` is the REST
collection: `pulls` or `issues`. Reading and writing the description are the
same `GET`/`PATCH` against `/repos/{owner}/{repo}/{kind}/{number}`. Footer
building, recovery and ordering are unchanged.

The kind comes from the URL, not from which command ran. `gh issue comment 7`
works on PR #7 too, and prints `…/pull/7#issuecomment-…`, so it updates the PR.
Likewise `add_issue_comment` — which previously only counted on PRs — now
updates whichever of the two its comment landed on.

## Edit history

Recovery ([0009](./0009-recover-footers-from-edit-history.md)) queries
`repository.issueOrPullRequest(number:)` with an inline fragment for each type,
so one query serves both. The triggers extend naturally: `gh issue edit` with a
body flag, or `issue_write` `update` carrying a `body`. A state change or label
edit doesn't consult history.

## Not covered

`sub_issue_write` and the older `create_issue`/`update_issue` MCP tool names
aren't hooked. A multi-issue `gh issue edit 1 2 …` updates only the first
issue it prints.
