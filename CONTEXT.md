# claude-pr-resume-hook

A Claude Code hook that keeps a pull request description pointing back at the
Claude Code session which produced it, so you can return to that session from
the PR days later.

## Language

**Resume footer**:
The trailing block in a PR description naming whose session to return to and
the command that returns to it: a horizontal rule, a heading, and a fenced
command. A body carries exactly one, always last.
_Avoid_: footer line, resume link, signature, trailer

**Session**:
One Claude Code conversation, identified by its `session_id`. Resumable only
on the machine that holds its transcript, by the user who owns it.
_Avoid_: conversation, chat, thread

**Hook event**:
The JSON document Claude Code writes to the hook's stdin, describing one
completed tool call — the tool name, its input, its response, and the
session's `cwd` and `session_id`.
_Avoid_: payload, message, request

**Scope**:
Which Claude Code settings file an install targets: `user`, `project`, or
`local`. Determines reach, not content — the entries written are identical.
_Avoid_: level, target, location

**Shim**:
The executable `uv tool install` places in uv's bin directory. What the
settings entries name, by absolute path.
_Avoid_: binary, wrapper, entry point

**Reconcile**:
Bringing a settings file to the desired state by comparing what is there with
what should be: adding, rewriting, or removing entries as needed, and writing
nothing when they already agree. What `install` and `uninstall` do.
_Avoid_: sync, merge, apply, update

**Legacy entry**:
A hook entry in settings from an earlier installation of this tool, matched by
marker rather than by exact command. Reconciled away on install.
_Avoid_: stale hook, old entry, duplicate
