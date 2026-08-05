# Read and write PR bodies through the REST API, not `gh pr` subcommands

The hook reads and updates PR descriptions with `GET`/`PATCH
/repos/{owner}/{repo}/pulls/{number}` over `urllib.request`, rather than the
obvious `gh pr view --json body` / `gh pr edit --body-file`. Several `gh`
subcommands shell out to `git` internally and are unreliable in sandboxed
environments, which is exactly where this hook runs. `gh` is still used, but
only for `gh auth token`, which touches neither git nor the network.

## Consequences

The hook carries its own HTTP and token handling — about thirty lines that
`gh` would otherwise have provided for free. In exchange it has no dependency
on `gh` behaving well under sandboxing, and it can distinguish a missing token
from a failed write. Do not "simplify" this back to `gh pr edit`.
