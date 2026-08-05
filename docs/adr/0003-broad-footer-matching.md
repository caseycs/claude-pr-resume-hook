# Footer matching is deliberately broad

Before appending a footer, the hook strips *any* line beginning with `Resume
session:` — anywhere in the body, case-insensitively, tolerating leading
whitespace, `>` quoting and `*`/`_` emphasis — plus the well-formed footer
glued inline onto the end of a line. This is much looser than matching the
exact string the hook itself writes.

The breadth is the point. A footer that has been hand-edited, re-worded,
emphasised, moved up the body, or had its blank line deleted must be
*replaced*, not duplicated. Tightening these patterns reintroduces stacked
footers, which is the failure mode the rewrite exists to prevent.

## Consequences

Prose that begins a line with the exact phrase "Resume session:" will be eaten.
That is the accepted cost. The inline pattern is kept strict — it requires the
full ``` `cd …; claude -r …` ``` shape — precisely so that prose merely
*mentioning* a resume session survives. Both regexes are covered by tests
named after the edit they simulate; read those before changing either.
