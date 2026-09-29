import json
import os
import urllib.parse

import pytest

import claude_pr_resume_hook as hook


@pytest.fixture
def api(monkeypatch):
    """Stub out the GitHub API and token lookup, recording every request."""

    class FakeApi:
        def __init__(self):
            self.calls = []
            self.body = ""
            # Whose session the footer is keyed on: the authenticated login.
            self.login = "tester"
            self.login_error = None
            # Revisions of the description, as (editedAt, body), oldest first.
            self.edits = []
            self.edits_error = None
            # Open PRs by head, "owner:branch" -> number, for branch lookups.
            self.open_prs = {}

        def __call__(self, method, path, token, payload=None):
            self.calls.append((method, path, payload))
            if path == "/user":
                if self.login_error:
                    raise self.login_error
                return {"login": self.login}
            if path == "/graphql":
                if self.edits_error:
                    raise self.edits_error
                nodes = [{"editedAt": at, "diff": body} for at, body in self.edits[-2:]]
                # GitHub lists them newest first.
                nodes.reverse()
                return {"data": {"repository": {"issueOrPullRequest": {"userContentEdits": {"nodes": nodes}}}}}
            if method == "GET" and "/pulls?" in path:
                head = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["head"][0]
                number = self.open_prs.get(head)
                return [{"number": number}] if number else []
            if method == "GET":
                return {"body": self.body}
            return {}

        @property
        def patches(self):
            return [payload for method, _, payload in self.calls if method == "PATCH"]

        @property
        def pr_calls(self):
            """REST calls about the PR itself, ignoring identity and history lookups."""
            return [(m, p) for m, p, _ in self.calls if p not in ("/user", "/graphql") and "?" not in p]

        @property
        def history_calls(self):
            return [payload for _, path, payload in self.calls if path == "/graphql"]

    fake = FakeApi()
    monkeypatch.setattr(hook, "api_request", fake)
    monkeypatch.setattr(hook, "get_token", lambda: "test-token")
    return fake


@pytest.fixture
def event():
    """A minimal, valid `gh pr create` hook event."""

    def build(**overrides):
        base = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --fill"},
            "tool_response": {"stdout": "https://github.com/owner/repo/pull/123\n"},
            "cwd": "/work/tree",
            "session_id": "sess-abc",
        }
        base.update(overrides)
        return base

    return build


@pytest.fixture
def run_event(api, monkeypatch):
    """Feed an event dict through the hook exactly as Claude Code would."""

    def run(event_dict):
        monkeypatch.setattr("sys.argv", ["claude-pr-resume-hook"])
        monkeypatch.setattr("sys.stdin", _StringStdin(json.dumps(event_dict)))
        return hook.main()

    return run


class _StringStdin:
    def __init__(self, text):
        self._text = text

    def read(self, *args):
        text, self._text = self._text, ""
        return text


# --- install/uninstall fixtures ---------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated $HOME and cwd, so no test touches the real settings."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "project").mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(hook.Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.chdir(tmp_path / "project")
    return tmp_path / "home"


@pytest.fixture
def shim(tmp_path, monkeypatch):
    """A real executable named like our console script, first on PATH.

    Exercises the actual shutil.which lookup rather than stubbing it.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    exe = bindir / hook.CONSOLE_SCRIPT
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir), prepend=os.pathsep)
    return str(exe.resolve())


@pytest.fixture
def no_shim(tmp_path, monkeypatch):
    """A PATH containing neither our console script nor uv."""
    empty = tmp_path / "empty-bin"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty))


@pytest.fixture
def git(monkeypatch):
    """Stand-in for the local git queries, keyed by directory.

    Fails the test if the hook runs anything but git: gh subcommands are off
    limits (docs/adr/0001).
    """

    class Git:
        calls = []
        # directory -> {"remotes": {name: url}, "branch": name}
        checkouts = {}

    def fake_run(argv, **kwargs):
        assert argv[0] == "git", f"unexpected subprocess: {argv}"
        Git.calls.append(argv)
        directory, args = argv[2], argv[3:]
        checkout = Git.checkouts.get(directory)
        out = None
        if checkout and args[:2] == ["config", "--get"]:
            out = checkout.get("remotes", {}).get(args[2].split(".")[1])
        elif checkout and args[:2] == ["rev-parse", "--abbrev-ref"]:
            out = checkout.get("branch")
        if out is None:
            raise hook.subprocess.CalledProcessError(1, argv)
        return hook.subprocess.CompletedProcess(argv, 0, stdout=out + "\n", stderr="")

    monkeypatch.setattr(hook.subprocess, "run", fake_run)
    Git.checkouts["/work/tree"] = {
        "remotes": {"origin": "git@github.com:o/r.git"}, "branch": "feature",
    }
    return Git
