# Footer matching is deliberately broad

The footer is a block — a thematic break, a heading naming the local user, and a
fenced command:

```
---

Resume Claude session by `you`:
```
cd ~/github/repo; claude -r <session-id>
```
```

Before appending one, the hook strips *any* footer-shaped block: the thematic
break is optional and may be `---`, `***` or `___`; the heading matches
`Resume [Claude] session…` case-insensitively, tolerating leading whitespace,
`>` quoting and `*`/`_` emphasis; the fence may be backticks or tildes, three
or more, with or without a language. Two narrower patterns then catch a bare
heading whose fenced command was deleted, and the single-line footer that
earlier versions of this tool wrote.

The breadth is the point. A footer that has been hand-edited, re-worded,
emphasised, quoted, moved up the body, stripped of its rule or its fence, or
written by an older version must be *replaced*, not duplicated. Tightening
these patterns reintroduces stacked footers, which is the failure mode the
rewrite exists to prevent.

## Consequences

Prose that begins a line with `Resume session` or `Resume Claude session`
followed immediately by a fenced block will be eaten. That is the accepted
cost. The inline pattern is kept strict — it requires the full
``` `cd …; claude -r …` ``` shape — precisely so that prose merely *mentioning*
a resume session survives, and `tests/test_footer.py` pins both an unrelated
fenced block and an unrelated horizontal rule as things that must not be
touched. Every pattern is covered by tests named after the edit it simulates;
read those before changing any of them.
