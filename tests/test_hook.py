"""Usage tests: driving the hook with realistic Claude Code events."""
import urllib.error

import pytest

import claude_pr_resume_hook as hook

FOOTER = (
    "<details>\n"
    "<summary>AI session - tester</summary>\n"
    "\n"
    "```\ncd /work/tree; claude -r sess-abc\n```\n"
    "\n"
    "</details>"
)


@pytest.fixture(autouse=True)
def known_user(monkeypatch):
    monkeypatch.setattr(hook, "local_user", lambda: "local-name")


def users_in(body):
    return [m.group("user") for m in hook.FOOTER_DETAILS_RE.finditer(body)]


def commands_in(body):
    return [line for line in body.splitlines() if line.startswith("cd ")]


def test_gh_pr_create_patches_the_body(run_event, event, api):
    api.body = "Some description."

    assert run_event(event()) == 0

    assert api.pr_calls == [
        ("GET", "/repos/owner/repo/pulls/123"),
        ("PATCH", "/repos/owner/repo/pulls/123"),
    ]
    assert api.patches[0]["body"] == f"Some description.\n\n---\n\n{FOOTER}\n"


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
    api.body = f"Some description.\n\n---\n\n{FOOTER}\n"

    assert run_event(event()) == 0

    assert not api.patches


def test_unchanged_body_skips_the_patch_despite_crlf(run_event, event, api):
    """GitHub serves CRLF bodies; that alone must not count as a change."""
    api.body = f"Some description.\n\n---\n\n{FOOTER}\n".replace("\n", "\r\n")

    assert run_event(event()) == 0

    assert not api.patches


def test_stale_footer_is_replaced_not_stacked(run_event, event, api):
    api.body = (
        "Some description.\n\n---\n\n"
        "Resume Claude session by `tester`:\n```\ncd /old; claude -r old-sess\n```\n"
    )

    run_event(event())

    body = api.patches[0]["body"]
    assert users_in(body) == ["tester"]
    assert "old-sess" not in body


def test_footer_from_the_previous_single_line_format_is_migrated(run_event, event, api):
    api.body = "Some description.\n\nResume session: `cd /old; claude -r old-sess`\n"

    run_event(event())

    body = api.patches[0]["body"]
    assert body == f"Some description.\n\n---\n\n{FOOTER}\n"
    assert "old-sess" not in body


def test_hand_mangled_footer_is_replaced_not_stacked(run_event, event, api):
    api.body = (
        "Some description.\n\n"
        "**Resume session by `tester`:**\n~~~\ncd /old && claude -r old-sess\n~~~\n"
    )

    run_event(event())

    body = api.patches[0]["body"]
    assert users_in(body) == ["tester"]
    assert body == f"Some description.\n\n---\n\n{FOOTER}\n"


def test_manually_deleted_footer_is_restored(run_event, event, api):
    api.body = "Some description."

    run_event(event())

    assert api.patches[0]["body"].endswith(f"{FOOTER}\n")


def test_second_session_updates_the_footer_in_place(run_event, event, api):
    api.body = f"Some description.\n\n---\n\n{FOOTER}\n"

    run_event(event(cwd="/other/tree", session_id="sess-xyz"))

    body = api.patches[0]["body"]
    assert users_in(body) == ["tester"]
    assert commands_in(body) == ["cd /other/tree; claude -r sess-xyz"]


# --- whose footer is it ------------------------------------------------------


def test_the_footer_is_keyed_on_the_github_login_not_the_local_user(run_event, event, api):
    api.login = "caseycs"

    run_event(event())

    body = api.patches[0]["body"]
    assert users_in(body) == ["caseycs"]
    assert "local-name" not in body


def test_the_identity_comes_from_the_token_not_the_pr_author(run_event, event, api):
    """Editing someone else's PR must update your block, never theirs."""
    api.login = "me"
    api.body = (
        "Their description.\n\n---\n\n"
        "<details>\n<summary>AI session - them</summary>\n\n"
        "```\ncd /theirs; claude -r their-sess\n```\n\n</details>\n"
    )

    run_event(event())

    body = api.patches[0]["body"]
    assert users_in(body) == ["them", "me"]
    assert "cd /theirs; claude -r their-sess" in body


def test_a_failed_login_lookup_falls_back_to_the_local_name(run_event, event, api, capsys):
    api.login_error = urllib.error.HTTPError("/user", 401, "Unauthorized", {}, None)

    assert run_event(event()) == 0

    assert users_in(api.patches[0]["body"]) == ["local-name"]
    err = capsys.readouterr().err
    assert "could not read GitHub login" in err
    assert "falling back to local username" in err


def test_an_empty_login_also_falls_back(run_event, event, api):
    api.login = None

    run_event(event())

    assert users_in(api.patches[0]["body"]) == ["local-name"]


# --- the GitHub MCP server route ---------------------------------------------

# What github-mcp-server actually returns: a text result holding JSON. `id` is
# GitHub's database id, deliberately never used as a PR number.
MCP_JSON = '{"id":"2891234567","url":"https://github.com/o/r/pull/9"}'


def mcp_event(tool="create_pull_request", response=None, **overrides):
    event = {
        "tool_name": f"mcp__github__{tool}",
        "tool_input": {"owner": "o", "repo": "r", "title": "T", "head": "f", "base": "main"},
        "tool_response": {"type": "text", "text": MCP_JSON} if response is None else response,
        "cwd": "/work/tree",
        "session_id": "sess-abc",
    }
    event.update(overrides)
    return event


def test_mcp_create_pull_request_patches_the_body(run_event, api):
    api.body = "Some description."

    assert run_event(mcp_event()) == 0

    assert api.pr_calls == [
        ("GET", "/repos/o/r/pulls/9"),
        ("PATCH", "/repos/o/r/pulls/9"),
    ]
    assert api.patches[0]["body"] == f"Some description.\n\n---\n\n{FOOTER}\n"


def test_mcp_update_pull_request_also_fires(run_event, api):
    event = mcp_event("update_pull_request")
    event["tool_input"] = {"owner": "o", "repo": "r", "pullNumber": 9, "title": "T"}

    assert run_event(event) == 0

    assert api.patches


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({"type": "text", "text": MCP_JSON}, id="text-block"),
        pytest.param([{"type": "text", "text": MCP_JSON}], id="list-of-blocks"),
        pytest.param(MCP_JSON, id="bare-string"),
        pytest.param({"content": [{"type": "text", "text": MCP_JSON}]}, id="nested-content"),
        pytest.param({"url": "https://github.com/o/r/pull/9"}, id="plain-dict"),
    ],
)
def test_the_pr_url_is_found_whatever_the_response_shape(run_event, api, response):
    """How Claude Code nests MCP content is undocumented, so do not depend on it."""
    assert run_event(mcp_event(response=response)) == 0
    assert api.calls[0][1] == "/repos/o/r/pulls/9"


def test_the_database_id_is_never_used_as_a_pr_number(run_event, api):
    """`id` is 2891234567; targeting it instead of 9 would hit the wrong PR."""
    run_event(mcp_event())
    assert all("2891234567" not in path for _, path, _ in api.calls)


def test_update_falls_back_to_the_input_fields_without_a_url(run_event, api):
    """A future server version returning only an id must still work for updates."""
    event = mcp_event("update_pull_request", response={"type": "text", "text": '{"id":"123"}'})
    event["tool_input"] = {"owner": "o2", "repo": "r2", "pullNumber": 42}

    assert run_event(event) == 0

    assert api.calls[0][1] == "/repos/o2/r2/pulls/42"


def test_a_float_pull_number_is_normalized(run_event, api):
    """MCP declares pullNumber as a JSON number, so it arrives as a float."""
    event = mcp_event("update_pull_request", response={"text": "no url here"})
    event["tool_input"] = {"owner": "o", "repo": "r", "pullNumber": 42.0}

    run_event(event)

    assert api.calls[0][1] == "/repos/o/r/pulls/42"


def test_a_pr_url_in_the_input_body_is_never_used(run_event, api):
    """A body may reference other PRs; patching those would be badly wrong."""
    event = mcp_event(response={"type": "text", "text": "Created, but no URL returned"})
    event["tool_input"] = {
        "owner": "o",
        "repo": "r",
        "body": "Fixes https://github.com/other/repo/pull/777",
    }

    assert run_event(event) == 0

    assert api.calls == []


def test_the_insiders_mode_confirmation_is_not_treated_as_a_pr(run_event, api):
    """With MCP Apps UI the tool returns a prompt and creates nothing."""
    event = mcp_event(response={
        "type": "text",
        "text": "Ready to create a pull request in o/r. IMPORTANT: The PR has NOT been created yet.",
    })

    assert run_event(event) == 0

    assert api.calls == []


@pytest.mark.parametrize(
    "tool_name",
    [
        pytest.param("mcp__github_remote__create_pull_request", id="renamed-server"),
        pytest.param("mcp__plugin_myplug_github__update_pull_request", id="plugin-scoped"),
        pytest.param("mcp__gh__create_pull_request", id="short-server-key"),
    ],
)
def test_other_server_keys_are_still_handled(run_event, api, tool_name):
    """install pins the matcher, but a hand-widened one must still work."""
    assert run_event(mcp_event(tool_name=tool_name)) == 0
    assert api.patches


@pytest.mark.parametrize(
    "tool_name",
    [
        pytest.param("mcp__github__search_code", id="unrelated-github-tool"),
        pytest.param("mcp__github__pull_request_read", id="read-only-pr-tool"),
        pytest.param("mcp__github__add_issue_comment", id="comment-tool"),
        pytest.param("mcp__memory__create_entities", id="unrelated-server"),
    ],
)
def test_unrelated_mcp_tools_touch_nothing(run_event, api, tool_name):
    assert run_event(mcp_event(tool_name=tool_name)) == 0
    assert api.calls == []


def test_mcp_footer_replacement_matches_the_bash_route(run_event, api):
    """Both routes must converge on the same body."""
    api.body = f"Some description.\n\n---\n\n{FOOTER}\n"

    assert run_event(mcp_event()) == 0

    assert not api.patches


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
