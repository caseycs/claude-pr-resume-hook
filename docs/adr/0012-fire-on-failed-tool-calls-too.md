# Fire on failed tool calls too

A real session lost a PR's footer this way: Claude ran

```
python3 - <<'PY' … PY
gh pr edit 2207 --body-file pr.md >/dev/null && echo "body updated"
COMMENT_ID=$(gh api …)
gh api -X PATCH "repos/o/r/issues/2207/comments/$COMMENT_ID" -f body=…
```

in one Bash call. The edit replaced the description with the local draft,
which never contained the footer, and the last step hit a 404. The call exited
non-zero, so Claude Code raised `PostToolUseFailure` rather than `PostToolUse`,
the hook was not registered for it, and nothing put the footer back until the
next hooked call. The `if` filters were not the problem: they match any
sub-command of a compound command and fail open.

A description edit is the first thing in such a command to take effect, and
the tool call's exit status says nothing about whether it did. Only the hook
can tell, by parsing the command.

## Decision

Register every hook target under `PostToolUseFailure` as well as
`PostToolUse`, and treat the two events alike: the targets come from the
command, as [0011](./0011-resolve-gh-targets-from-the-command.md) decided, so
the missing `tool_response` of a failure event changes nothing for a call that
names its PR or runs in a checkout with a remote. `install` reconciles both
lists, `uninstall` empties both, and a settings file with only the older
`PostToolUse` entries is reconciled to carry both.

## Consequences

A `gh pr create` that failed outright finds no open PR for the branch and does
nothing, and a `gh pr edit` that failed before writing refreshes a footer that
was already there. Neither writes anything wrong. A `gh pr create` whose only
link to the PR was the URL it printed still falls back on that link under
`PostToolUse`; under `PostToolUseFailure` there is no output to read, so a
create that printed its link and then failed later in the same command is
not matched - the next hooked call restores the block from the edit history.
