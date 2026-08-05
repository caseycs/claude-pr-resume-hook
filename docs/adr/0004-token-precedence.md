# Token precedence: environment first, then `gh auth token`

The hook takes its GitHub token from `$GH_TOKEN`, then `$GITHUB_TOKEN`, and
only falls back to shelling out to `gh auth token`. `gh` is the source of
truth for who you are on the machine, so the reverse order is defensible — but
environment-first keeps an explicit per-invocation override trivial and skips a
subprocess spawn on every hook fire when a token is already present.

## Consequences

An ambient token you did not set — one injected by a devcontainer or a CI
runner, often scoped to a single repository — wins over your `gh` identity and
produces a `403` on the `PATCH` that looks like a `gh auth` problem but is not.
If that bites, invert the order in `get_token()`; the failure message already
names which stage failed.
