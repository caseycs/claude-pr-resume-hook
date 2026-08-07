import json
import os

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

        def __call__(self, method, path, token, payload=None):
            self.calls.append((method, path, payload))
            if path == "/user":
                if self.login_error:
                    raise self.login_error
                return {"login": self.login}
            if method == "GET":
                return {"body": self.body}
            return {}

        @property
        def patches(self):
            return [payload for method, _, payload in self.calls if method == "PATCH"]

        @property
        def pr_calls(self):
            """Calls about the PR itself, ignoring the identity lookup."""
            return [(m, p) for m, p, _ in self.calls if p != "/user"]

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
