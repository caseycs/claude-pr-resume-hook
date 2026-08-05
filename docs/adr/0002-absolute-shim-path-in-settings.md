# Settings name the tool by absolute shim path, not a `uvx --from` command

`install` writes the absolute path of the `uv tool install` shim (for example
`/Users/you/.local/bin/claude-pr-resume-hook`) into `settings.json`, rather
than the more portable-looking `uvx --from git+https://… claude-pr-resume-hook`.

## Considered options

Measured on macOS with a warm uv cache:

| Form | Per hook fire | Offline |
| --- | --- | --- |
| absolute shim path | ~60ms | works |
| `uvx --from <local path>` | ~60ms | works |
| `uvx --from git+https://…` | ~560ms — `git ls-remote` resolves the unpinned branch every run | fails |
| `uvx --from git+…@tag` | ~60ms once cached | works |

The git forms also mean a bad push breaks every machine at once.

## Consequences

The written command is machine-specific, so a `settings.json` synced across
machines would break — acceptable, because Claude Code settings are local and
not version-controlled here. In exchange, hook-time `PATH` stops mattering
entirely: a wrong path fails loudly at install time rather than silently at
PR-create time. `install` re-resolves the shim on every run, so moving or
reinstalling the tool is fixed by re-running it.
