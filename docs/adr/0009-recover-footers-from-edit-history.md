# Recover footers from the description's edit history

Claude usually edits a description by writing a whole new one —
`gh pr edit --body-file`, or `update_pull_request` with a `body` — without first
reading what is there. The hook runs afterwards and restores the running
session's own block, but every other block (your other sessions, colleagues')
is already gone, and the hook has never seen it.

## Where the old blocks come from

GitHub keeps every revision of a description. The GraphQL
`PullRequest.userContentEdits` connection lists them newest first; each node's
`diff` is, despite the name, the full text after that edit. So after an edit
the hook reads the last two revisions and takes the older one as "the
description before this edit". Any footer block there whose (login, session)
key is missing from the current body is put back.

This needs no extra hook entries and no local state, and it works whichever
machine made the edit. The alternative — a `PreToolUse` hook snapshotting blocks
to disk before each edit — would need more settings entries and would only help
on the machine holding the snapshot.

## When it is trusted

A block a person deleted on purpose must stay deleted. The history is only
consulted:

- after a call that rewrote the description: `gh pr edit` with `-b`/`--body`/
  `-F`/`--body-file`, or `update_pull_request` with a `body` — not create,
  comment, review, or a title/label-only edit;
- when the newest revision equals the current body, so it is the edit that
  just ran and the revision before it is really its predecessor;
- when that newest revision is under ten minutes old.

Without the last two checks, an edit that left the description alone would make
the hook compare against whatever the *previous* description edit was — quite
possibly a person removing a block — and resurrect it.

If the lookup fails, the hook says so on stderr and carries on without
recovery.

## Ordering

With blocks now coming back from older revisions, "append at the bottom" no
longer gives a meaningful order. Blocks are sorted by when they were last
written, oldest first, so the session that just ran is always last.

The visible summary shows the time with a zone abbreviation (`CEST`), which
cannot be reliably converted to an offset. Each block therefore also carries
the time as `<details data-updated="2026-09-12T14:05:00+02:00">`. GitHub drops
the attribute when rendering; it lives only in the raw body. Blocks from 0.3.x
fall back to the summary's date, zone ignored — close enough to order them.
Blocks with no date at all sort first, keeping their relative order.

## Identifying our blocks

Every block also carries `data-generator="caseycs/claude-pr-resume-hook"`. A
block matching the summary pattern but naming a different generator is left in
the prose untouched. That matters now that blocks are restored and reordered:
the hook should only ever move what it wrote. Blocks from before the attribute
existed have no generator at all, and are still treated as ours. The attribute
is `data-generator`, not `author`, because "author" is reserved vocabulary (see
CONTEXT.md — it means the PR's opener, which is not the footer owner).

This replaces 0008's "update in place, append new sessions": a returning session
now moves to the bottom.
