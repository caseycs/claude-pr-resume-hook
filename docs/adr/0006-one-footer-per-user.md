# One footer per user, keyed on the GitHub login

A PR can be touched from more than one person's Claude Code session — you open
it, a colleague edits it. Earlier versions collapsed the body to exactly one
footer, so whoever ran last silently erased the previous person's way back into
their session. The footer is now a `<details>` block per user, and each run adds
or updates only its own:

```
---

<details>
<summary>AI session - alice</summary>

```
cd ~/github/repo; claude -r <session-id>
```

</details>

<details>
<summary>AI session - bob</summary>
…
</details>
```

Blocks are lifted out in document order and put back in the same order, so
updating your own never reorders or rewrites anyone else's. A single thematic
break separates the group from the description.

## Identity is the authenticated GitHub login

The key is `GET /user` — the login belonging to the *token*, not the PR author.
That distinction is the whole point: editing someone else's PR must update your
block, not theirs. Matching is case-insensitive, since GitHub logins are.

This costs one extra API call per hook fire. It cannot be avoided by reusing the
PR payload: `pr.user.login` is whoever opened the PR, which is the wrong person
exactly when it matters.

If the call fails, the hook falls back to the local account name and says so on
stderr. That keeps a footer appearing during an outage, at the cost of a second
block when the local name and the GitHub login differ — the two identities can't
recognise each other, so neither run will clean the other up. Re-running once the
API is reachable does not merge them; delete the stray block by hand.

## Consequences

This reverses the "collapse to exactly one" rule in
[0003](./0003-broad-footer-matching.md). That ADR's breadth still applies *within*
a block, but the strip-everything-then-append approach is gone: a body is now
split into prose plus a list of `(user, block)` pairs, and only the matching pair
is rewritten.

Footers from the older formats are still stripped wholesale on sight, whoever
wrote them. Those schemes kept one footer per PR by construction, so there is
never more than one to lose, and it is superseded by the block being written.
