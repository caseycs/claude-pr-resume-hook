# claude-pr-resume-hook

A [Claude Code](https://code.claude.com) `PostToolUse` hook. Every time Claude
runs `gh pr create` or `gh pr edit` in a session, it makes sure the resulting
PR description ends with a line that lets you jump straight back into that
session later:

```
Resume session: `cd <cwd>; claude -r <session_id>`
```

`<cwd>` and `<session_id>` are taken from the hook event itself, so the
footer always points at the exact directory and session that produced (or
last touched) the PR.

## How it works

`append_resume_footer.py` reads the Claude Code hook event JSON from stdin:

1. Ignores anything that isn't a `Bash` call running `gh pr create` or
   `gh pr edit`.
2. Extracts the PR's `owner/repo/number` from the PR URL that `gh` prints to
   stdout on success. If `gh` didn't print a URL (the command failed), it
   does nothing.
3. Fetches the current PR body and the session's `cwd`/`session_id` from the
   hook event, authenticating to the GitHub REST API with a token from
   `gh auth token` (or `$GH_TOKEN`/`$GITHUB_TOKEN` if set).
4. Strips any previous `Resume session: ...` footer and appends a fresh one,
   so re-editing the same PR from a different session updates the footer
   in place instead of stacking copies.
5. If the body is unchanged, skips the PATCH request entirely.

The script never blocks the tool call (`PostToolUse` can't block anyway) and
logs failures to stderr rather than raising, so a broken token or a network
hiccup never interrupts the Claude Code session.

It intentionally talks to the GitHub REST API directly (`urllib.request`)
rather than shelling out to `gh pr view`/`gh pr edit` for the read/write —
some `gh` subcommands that shell out to `git` internally are known to be
unreliable in sandboxed environments, so the hook only uses `gh` for
`gh auth token`, which doesn't touch git at all.

## Setup

1. Make sure `gh` is installed and authenticated (`gh auth status`) with a
   token that has `repo` scope for the repos you open PRs against.

2. Add the hook to your Claude Code settings (global `~/.claude/settings.json`
   to cover every project, or a project's `.claude/settings.json` /
   `settings.local.json` to scope it to one repo):

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
               "command": "python3 /Users/ilia/github/claude-pr-resume-hook/append_resume_footer.py"
             },
             {
               "type": "command",
               "if": "Bash(gh pr edit*)",
               "command": "python3 /Users/ilia/github/claude-pr-resume-hook/append_resume_footer.py"
             }
           ]
         }
       ]
     }
   }
   ```

   The `if` filters mean the hook only spawns for `gh pr create`/`gh pr edit`
   Bash calls, not every Bash command.

3. Restart/reload Claude Code so the settings change takes effect.

## Testing

Feed it a fake event by hand, without touching a real PR:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"gh pr create --fill"},"tool_response":{"stdout":"https://github.com/owner/repo/pull/123\n"},"cwd":"/path/to/worktree","session_id":"test-session"}' \
  | python3 append_resume_footer.py
```

This will make a real GET/PATCH against `owner/repo#123` if the URL is real
and your token has access — use a scratch repo/PR for a live test.

## See also

See `requirements.md` for prerequisites.
