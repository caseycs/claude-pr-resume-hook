# Release tags are plain `vX.Y.Z`

`include-component-in-tag` is set to `false`. Without it, releases were tagged
`claude-pr-resume-hook-v0.1.0` rather than `v0.1.0`, and the install command the
README documents —

```
uv tool install git+https://github.com/caseycs/claude-pr-resume-hook@v0.2.0
```

— failed, because no such tag existed.

The cause is not obvious: `release-type: python` requires `package-name`, setting
`package-name` gives the package a *component*, and `include-component-in-tag`
defaults to **true**, so the component is prefixed onto every tag. Nothing in the
config says "prefix the tag"; it is an emergent default. Removing `package-name`
would also fix the tags but is not an option — the manifest docs mark it required
for non-node release types.

## Consequences

Do not delete this key thinking it is a no-op. Restoring the default silently
renames future tags and breaks every pinned install line in the README, which the
generic updater keeps current under an `x-release-please` block.

Versions 0.1.0 and 0.2.0 were released before this was set, so they carry *both*
tags: the original `claude-pr-resume-hook-vX.Y.Z` that their GitHub Releases point
at, and a plain `vX.Y.Z` backfilled onto the same commit so pinned installs work.
The duplicates are harmless and only affect those two versions; releases from
0.3.0 onward have one tag each.
