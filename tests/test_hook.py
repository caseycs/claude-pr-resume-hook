"""Usage tests: driving the hook with realistic Claude Code events."""
import urllib.error

import pytest

import claude_pr_resume_hook as hook

FOOTER = "Resume session: `cd /work/tree; claude -r sess-abc`"


def test_gh_pr_create_patches_the_body(run_event, event, api):
    api.body = "Some description."

    assert run_event(event()) == 0

    assert [(m, p) for m, p, _ in api.calls] == [
        ("GET", "/repos/owner/repo/pulls/123"),
        ("PATCH", "/repos/owner/repo/pulls/123"),
    ]
    assert api.patches[0]["body"] == f"Some description.\n\n{FOOTER}\n"


def test_gh_pr_edit_also_fires(run_event, event, api):
    assert run_event(event(tool_input={"command": "gh pr edit 123 --body-file -"})) == 0
    assert api.patches


def test_url_is_read_from_stdout_not_the_command(run_event, event, api):
    event_dict = event(
        tool_input={"command": "gh pr create --repo owner/other --fill"},
        tool_response={"stdout": "Creating pull request\nhttps://github.com/o2/r2/pull/7\n"},
    )
    assert run_event(event_dict) == 0
    assert api.calls[0][1] == "/repos/o2/r2/pulls/7"


def test_unchanged_body_skips_the_patch(run_event, event, api):
    api.body = f"Some description.\n\n{FOOTER}\n"

    assert run_event(event()) == 0

    assert not api.patches


def test_unchanged_body_skips_the_patch_despite_crlf(run_event, event, api):
    """GitHub serves CRLF bodies; that alone must not count as a change."""
    api.body = f"Some description.\r\n\r\n{FOOTER}\r\n"

    assert run_event(event()) == 0

    assert not api.patches


def test_stale_footer_is_replaced_not_stacked(run_event, event, api):
    api.body = "Some description.\n\nResume session: `cd /old; claude -r old-sess`\n"

    run_event(event())

    body = api.patches[0]["body"]
    assert body.count("Resume session:") == 1
    assert "old-sess" not in body


def test_hand_mangled_footer_is_replaced_not_stacked(run_event, event, api):
    api.body = "Some description.\n\n**Resume session:** cd /old && claude -r old-sess\n"

    run_event(event())

    body = api.patches[0]["body"]
    assert body.count("Resume session:") == 1
    assert body == f"Some description.\n\n{FOOTER}\n"


def test_manually_deleted_footer_is_restored(run_event, event, api):
    api.body = "Some description."

    run_event(event())

    assert api.patches[0]["body"].endswith(f"{FOOTER}\n")


def test_second_session_updates_the_footer_in_place(run_event, event, api):
    api.body = f"Some description.\n\n{FOOTER}\n"

    run_event(event(cwd="/other/tree", session_id="sess-xyz"))

    body = api.patches[0]["body"]
    assert body == "Some description.\n\nResume session: `cd /other/tree; claude -r sess-xyz`\n"


@pytest.mark.parametrize(
    "event_kwargs",
    [
        pytest.param({"tool_name": "Edit"}, id="not-a-bash-call"),
        pytest.param({"tool_input": {"command": "git push"}}, id="unrelated-command"),
        pytest.param({"tool_input": {"command": "gh pr view 1"}}, id="other-gh-pr-subcommand"),
        pytest.param({"tool_response": {"stdout": ""}}, id="no-url-means-gh-failed"),
        pytest.param({"tool_response": {}}, id="no-stdout-at-all"),
        pytest.param({"cwd": None}, id="missing-cwd"),
        pytest.param({"session_id": None}, id="missing-session-id"),
        pytest.param({"tool_input": {}}, id="missing-command"),
    ],
)
def test_ignored_events_touch_nothing(run_event, event, api, event_kwargs):
    assert run_event(event(**event_kwargs)) == 0
    assert api.calls == []


def test_api_failure_is_reported_but_not_raised(run_event, event, monkeypatch, capsys, api):
    def boom(method, path, token, payload=None):
        raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)

    monkeypatch.setattr(hook, "api_request", boom)

    assert run_event(event()) == 0
    assert "failed to fetch PR body" in capsys.readouterr().err


def test_missing_token_is_reported_but_not_raised(run_event, event, monkeypatch, capsys, api):
    monkeypatch.setattr(hook, "get_token", lambda: None)

    assert run_event(event()) == 0
    assert "no GitHub token" in capsys.readouterr().err
    assert api.calls == []


def test_malformed_stdin_never_crashes_the_session(monkeypatch, capsys):
    class BadStdin:
        def read(self, *args):
            return "not json"

    monkeypatch.setattr("sys.argv", ["claude-pr-resume-hook"])
    monkeypatch.setattr("sys.stdin", BadStdin())

    assert hook.main() == 0
    assert "unexpected error" in capsys.readouterr().err


def test_token_prefers_environment_over_gh(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "from-env")
    monkeypatch.setattr(hook.subprocess, "run", _explode)
    assert hook.get_token() == "from-env"


def test_token_falls_back_to_gh_auth_token(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    class Result:
        stdout = "gh-cli-token\n"

    monkeypatch.setattr(hook.subprocess, "run", lambda *a, **k: Result())
    assert hook.get_token() == "gh-cli-token"


def test_token_is_none_when_gh_is_unavailable(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(hook.subprocess, "run", _explode)
    assert hook.get_token() is None


def _explode(*args, **kwargs):
    raise OSError("gh not found")
