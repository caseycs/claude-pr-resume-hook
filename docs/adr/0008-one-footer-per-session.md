# One footer per session, stamped with time and model

[0006](./0006-one-footer-per-user.md) keyed footers on the GitHub login alone, so
opening a fresh session on a PR you had already touched overwrote the block for
the earlier session — losing the way back into it. A block is now keyed on the
login **and** the session id, the latter read back out of the block's own
`claude -r <id>` command:

- same login, same session → that block is rewritten in place;
- anything else → a new block is appended at the bottom.

Everything 0006 says about identity (the token's login, case-insensitive,
local-name fallback) still holds; only the key got wider.

## The summary carries a stamp

```
<summary>AI session - alice, 12 September 2026 14:05 CEST, Fable5.5/high</summary>
```

With several blocks per person, the summary needs to tell them apart without
expanding each one. It records the local time of the latest run and the model
and effort of the turn that made it.

Hook events carry neither model nor effort, so both come from the session
transcript (`transcript_path`): the last `assistant` entry has `message.model`
and `effort`. Only the tail of the file is read. If the transcript is missing or
unreadable the model part is simply left out.

The user part of the summary is matched up to the first comma — GitHub logins
cannot contain one — so older unstamped blocks still parse.

## Consequences

Because the stamp includes the time to the minute, re-running the same session
a minute later now PATCHes the body. That is the point — the stamp says when the
session last touched the PR — but it means the "skip unchanged" check only saves
calls within the same minute.

Blocks written by 0.2.x have no stamp and one session each; they are treated
like any other block, so the first run from a *new* session appends rather than
replacing them.
