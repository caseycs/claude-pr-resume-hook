"""Usage tests: driving the hook with realistic Claude Code events."""
import datetime
import json
import urllib.error

import pytest

import claude_pr_resume_hook as hook

FOOTER = (
    '<details data-generator="caseycs/claude-pr-resume-hook" data-updated="2026-09-12T14:05:00+00:00">\n'
    "<summary>AI session - tester, 12 September 2026 14:05 UTC</summary>\n"
    "\n"
    "```\ncd /work/tree; claude -r sess-abc\n```\n"
    "\n"
    "</details>"
)


FROZEN = datetime.datetime(2026, 9, 12, 14, 5, tzinfo=datetime.timezone.utc)


@pytest.fixture(autouse=True)
def known_user(monkeypatch):
    monkeypatch.setattr(hook, "local_user", lambda: "local-name")


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Pin the time the footer records, so runs are comparable."""

    class Clock:
        value = FROZEN

    monkeypatch.setattr(hook, "now", lambda: Clock.value)
    return Clock


@pytest.fixture
def transcript(tmp_path):
    """Write a session transcript holding the given (model, effort) turns."""

    def write(*turns, titles=()):
        path = tmp_path / "session.jsonl"
        lines = [{"type": "user", "message": {"role": "user", "content": "hi"}}]
        for kind, title in titles:
            key = "customTitle" if kind == "custom-title" else "aiTitle"
            lines.append({"type": kind, key: title, "sessionId": "sess-abc"})
        for model, effort in turns:
            entry = {"type": "assistant", "message": {"role": "assistant", "model": model}}
            if effort:
                entry["effort"] = effort
            lines.append(entry)
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
        return str(path)

    return write


def summaries_in(body):
    return [line for line in body.splitlines() if line.startswith("<summary>")]


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


def test_a_second_session_of_yours_is_appended_below(run_event, event, api):
    api.body = f"Some description.\n\n---\n\n{FOOTER}\n"

    run_event(event(cwd="/other/tree", session_id="sess-xyz"))

    body = api.patches[0]["body"]
    assert users_in(body) == ["tester", "tester"]
    assert commands_in(body) == [
        "cd /work/tree; claude -r sess-abc",
        "cd /other/tree; claude -r sess-xyz",
    ]


def test_the_same_session_later_only_moves_the_timestamp(run_event, event, api, clock):
    api.body = f"Some description.\n\n---\n\n{FOOTER}\n"
    clock.value = FROZEN + datetime.timedelta(days=2, hours=1)

    run_event(event())

    assert api.patches[0]["body"] == api.body.replace(
        "12 September 2026 14:05", "14 September 2026 15:05"
    ).replace("2026-09-12T14:05", "2026-09-14T15:05")


# --- when and on what model --------------------------------------------------


def test_the_summary_names_the_model_and_effort(run_event, event, api, transcript):
    path = transcript(("claude-opus-5-5", "medium"), ("claude-fable-5-5", "high"))

    run_event(event(transcript_path=path))

    assert summaries_in(api.patches[0]["body"]) == [
        "<summary>AI session - tester, 12 September 2026 14:05 UTC, Fable5.5/high</summary>"
    ]


def test_a_model_switch_updates_the_summary(run_event, event, api, transcript):
    api.body = f"Some description.\n\n---\n\n{FOOTER}\n"

    run_event(event(transcript_path=transcript(("claude-sonnet-5", None))))

    body = api.patches[0]["body"]
    assert summaries_in(body) == [
        "<summary>AI session - tester, 12 September 2026 14:05 UTC, Sonnet5</summary>"
    ]
    assert users_in(body) == ["tester"]


def test_placeholder_models_are_skipped(run_event, event, api, transcript):
    path = transcript(("claude-opus-5-5", "low"), ("<synthetic>", None))

    run_event(event(transcript_path=path))

    assert "Opus5.5/low</summary>" in api.patches[0]["body"]


def test_the_summary_names_the_session(run_event, event, api, transcript):
    path = transcript(("claude-fable-5-5", "high"), titles=[("ai-title", "Multiple sessions")])

    run_event(event(transcript_path=path))

    body = api.patches[0]["body"]
    assert summaries_in(body) == [
        "<summary>AI session - tester (Multiple sessions), "
        "12 September 2026 14:05 UTC, Fable5.5/high</summary>"
    ]
    assert users_in(body) == ["tester"]


def test_a_renamed_session_beats_the_generated_title(run_event, event, api, transcript):
    path = transcript(
        ("claude-fable-5-5", "high"),
        titles=[
            ("custom-title", "first name"),
            ("custom-title", "pr-footers"),
            ("ai-title", "Generated later"),
        ],
    )

    run_event(event(transcript_path=path))

    assert "AI session - tester (pr-footers), " in api.patches[0]["body"]


def test_a_rename_updates_the_existing_block(run_event, event, api, transcript):
    api.body = f"Some description.\n\n---\n\n{FOOTER}\n"

    run_event(event(transcript_path=transcript(titles=[("custom-title", "renamed")])))

    body = api.patches[0]["body"]
    assert summaries_in(body) == [
        "<summary>AI session - tester (renamed), 12 September 2026 14:05 UTC</summary>"
    ]


def test_a_missing_transcript_still_writes_a_footer(run_event, event, api, tmp_path):
    run_event(event(transcript_path=str(tmp_path / "gone.jsonl")))

    assert summaries_in(api.patches[0]["body"]) == [
        "<summary>AI session - tester, 12 September 2026 14:05 UTC</summary>"
    ]


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
        pytest.param("mcp__github__add_comment_to_pending_review", id="pending-comment"),
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
    assert "failed to fetch the description" in capsys.readouterr().err


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


# --- comments and reviews ----------------------------------------------------


def test_gh_pr_comment_refreshes_the_description(run_event, event, api):
    api.body = "Some description."

    run_event(event(
        tool_input={"command": "gh pr comment 5 --body 'LGTM'"},
        tool_response={"stdout": "https://github.com/o/r/pull/5#issuecomment-2891234567\n"},
    ))

    assert api.pr_calls == [("GET", "/repos/o/r/pulls/5"), ("PATCH", "/repos/o/r/pulls/5")]
    assert api.patches[0]["body"] == f"Some description.\n\n---\n\n{FOOTER}\n"


def test_gh_pr_comment_without_a_url_does_nothing(run_event, event, api):
    """`--web` opens a browser and prints no URL: nothing was posted yet."""
    run_event(event(tool_input={"command": "gh pr comment 5 --web"}, tool_response={"stdout": ""}))
    assert api.calls == []


@pytest.fixture
def gh_view(monkeypatch):
    """Stub `gh pr view`, recording how it was called."""

    class GhView:
        calls = []
        url = "https://github.com/o/r/pull/12\n"
        fail = False

    def fake_run(argv, **kwargs):
        GhView.calls.append((argv, kwargs.get("cwd")))
        if GhView.fail:
            raise hook.subprocess.CalledProcessError(1, argv)
        return hook.subprocess.CompletedProcess(argv, 0, stdout=GhView.url, stderr="")

    monkeypatch.setattr(hook.subprocess, "run", fake_run)
    return GhView


def review(command):
    """A `gh pr review` event: gh prints nothing when stdout isn't a terminal."""
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"stdout": "", "stderr": ""},
        "cwd": "/work/tree",
        "session_id": "sess-abc",
    }


def test_gh_pr_review_asks_gh_which_pr_it_was(run_event, api, gh_view):
    run_event(review("gh pr review 12 --approve"))

    assert gh_view.calls == [
        (["gh", "pr", "view", "12", "--json", "url", "--jq", ".url"], "/work/tree")
    ]
    assert api.pr_calls == [("GET", "/repos/o/r/pulls/12"), ("PATCH", "/repos/o/r/pulls/12")]


@pytest.mark.parametrize(
    "command,expected",
    [
        pytest.param("gh pr review --comment -b 'nit: 42'",
                     ["gh", "pr", "view"], id="current-branch"),
        pytest.param("gh pr review -R o/r 12 -r --body 'see 99'",
                     ["gh", "pr", "view", "12", "--repo", "o/r"], id="repo-flag"),
        pytest.param("gh pr review --repo=o/r --body-file notes.md feature-x --approve",
                     ["gh", "pr", "view", "feature-x", "--repo", "o/r"], id="equals-and-branch"),
        pytest.param("gh pr review 12 --approve && gh pr merge 13",
                     ["gh", "pr", "view", "12"], id="stops-at-operator"),
        pytest.param("cd /x && gh pr review 12 -c -b \"$(cat <<'EOF'\nit's fine\nEOF\n)\"",
                     ["gh", "pr", "view", "12"], id="heredoc-body"),
    ],
)
def test_the_review_selector_is_read_from_the_command(run_event, api, gh_view, command, expected):
    run_event(review(command))

    assert gh_view.calls[0][0] == expected + ["--json", "url", "--jq", ".url"]


def test_a_review_gh_cannot_resolve_does_nothing(run_event, api, gh_view):
    gh_view.fail = True

    assert run_event(review("gh pr review 12 --approve")) == 0

    assert api.calls == []


def test_mcp_pr_comment_refreshes_the_description(run_event, api):
    event = mcp_event(
        "add_issue_comment",
        response={"type": "text", "text": '{"id":"1","url":"https://github.com/o/r/pull/5#issuecomment-1"}'},
        tool_input={"owner": "o", "repo": "r", "issue_number": 5, "body": "LGTM"},
    )

    run_event(event)

    assert api.pr_calls == [("GET", "/repos/o/r/pulls/5"), ("PATCH", "/repos/o/r/pulls/5")]


def test_mcp_comment_on_a_plain_issue_refreshes_the_issue(run_event, api):
    event = mcp_event(
        "add_issue_comment",
        response={"type": "text", "text": '{"id":"1","url":"https://github.com/o/r/issues/5#issuecomment-1"}'},
        tool_input={"owner": "o", "repo": "r", "issue_number": 5, "body": "see o/r/pull/7"},
    )

    assert run_event(event) == 0

    assert api.pr_calls == [("GET", "/repos/o/r/issues/5"), ("PATCH", "/repos/o/r/issues/5")]


def test_mcp_issue_comment_linking_a_pr_refreshes_the_issue_not_the_pr(run_event, api):
    """An older server echoes the comment body; a PR linked in it is not the target."""
    body = "Duplicate of https://github.com/o/r/pull/7#issuecomment-9"
    event = mcp_event(
        "add_issue_comment",
        response={"type": "text", "text": json.dumps({
            "html_url": "https://github.com/o/r/issues/5#issuecomment-1", "body": body,
        })},
        tool_input={"owner": "o", "repo": "r", "issue_number": 5, "body": body},
    )

    assert run_event(event) == 0

    assert api.pr_calls == [("GET", "/repos/o/r/issues/5"), ("PATCH", "/repos/o/r/issues/5")]


@pytest.mark.parametrize("method", ["create", "submit_pending"])
def test_mcp_review_refreshes_the_description(run_event, api, method):
    event = mcp_event(
        "pull_request_review_write",
        response={"type": "text", "text": "pull request review submitted successfully"},
        tool_input={"method": method, "owner": "o", "repo": "r", "pullNumber": 12, "event": "APPROVE"},
    )

    run_event(event)

    assert api.pr_calls == [("GET", "/repos/o/r/pulls/12"), ("PATCH", "/repos/o/r/pulls/12")]


def test_mcp_deleting_a_pending_review_touches_nothing(run_event, api):
    event = mcp_event(
        "pull_request_review_write",
        response={"type": "text", "text": "pending pull request review successfully deleted"},
        tool_input={"method": "delete_pending", "owner": "o", "repo": "r", "pullNumber": 12},
    )

    assert run_event(event) == 0

    assert api.calls == []


def test_mcp_review_thread_reply_refreshes_the_description(run_event, api):
    event = mcp_event(
        "add_reply_to_pull_request_comment",
        response={"type": "text", "text": '{"id":"3","url":"https://github.com/o/r/pull/12#discussion_r3"}'},
        tool_input={"owner": "o", "repo": "r", "pullNumber": 12, "commentId": 99, "body": "done"},
    )

    run_event(event)

    assert api.pr_calls == [("GET", "/repos/o/r/pulls/12"), ("PATCH", "/repos/o/r/pulls/12")]


# --- recovering footers an edit dropped ---------------------------------------

THEIRS = (
    '<details data-updated="2026-09-10T09:00:00+00:00">\n'
    "<summary>AI session - alice, 10 September 2026 09:00 UTC</summary>\n\n"
    "```\ncd /a; claude -r sess-alice\n```\n\n</details>"
)
JUST_NOW = "2026-09-12T14:04:30Z"


def edit_event(event, command="gh pr edit 123 --body-file body.md"):
    return event(
        tool_input={"command": command},
        tool_response={"stdout": "https://github.com/owner/repo/pull/123\n"},
    )


def test_a_rewritten_description_gets_dropped_footers_back(run_event, event, api):
    before = f"Old description.\n\n---\n\n{THEIRS}\n"
    api.body = "Rewritten from scratch."
    api.edits = [("2026-09-10T09:00:00Z", before), (JUST_NOW, api.body)]

    run_event(edit_event(event))

    assert api.history_calls[0]["variables"] == {"owner": "owner", "repo": "repo", "number": 123}
    assert api.patches[0]["body"] == f"Rewritten from scratch.\n\n---\n\n{THEIRS}\n\n{FOOTER}\n"


def test_mcp_body_update_also_recovers(run_event, api):
    api.body = "Rewritten from scratch."
    api.edits = [("2026-09-10T09:00:00Z", f"Old.\n\n---\n\n{THEIRS}\n"), (JUST_NOW, api.body)]
    event = mcp_event(
        "update_pull_request",
        tool_input={"owner": "o", "repo": "r", "pullNumber": 9, "body": "Rewritten from scratch."},
    )

    run_event(event)

    assert users_in(api.patches[0]["body"]) == ["alice", "tester"]


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("gh pr edit 123 --title 'Better title'", id="title-only-edit"),
        pytest.param("gh pr edit 123 --add-label bug", id="label-edit"),
    ],
)
def test_edits_that_leave_the_description_alone_skip_history(run_event, event, api, command):
    api.edits = [("2026-09-10T09:00:00Z", f"Old.\n\n---\n\n{THEIRS}\n"), (JUST_NOW, "Body.")]

    run_event(edit_event(event, command))

    assert api.history_calls == []


def test_comments_and_creates_skip_history(run_event, event, api):
    run_event(event())
    run_event(event(
        tool_input={"command": "gh pr comment 123 -b hi"},
        tool_response={"stdout": "https://github.com/owner/repo/pull/123#issuecomment-1\n"},
    ))

    assert api.history_calls == []


def test_an_old_edit_is_not_trusted(run_event, event, api):
    """A person removing a block yesterday must not see it come back today."""
    api.body = "Body without alice."
    api.edits = [
        ("2026-09-10T09:00:00Z", f"Body.\n\n---\n\n{THEIRS}\n"),
        ("2026-09-11T09:00:00Z", api.body),
    ]

    run_event(edit_event(event))

    assert users_in(api.patches[0]["body"]) == ["tester"]


def test_history_that_does_not_end_at_the_current_body_is_not_trusted(run_event, event, api):
    api.body = "What is on GitHub now."
    api.edits = [("2026-09-10T09:00:00Z", f"Old.\n\n---\n\n{THEIRS}\n"), (JUST_NOW, "Something else.")]

    run_event(edit_event(event))

    assert users_in(api.patches[0]["body"]) == ["tester"]


def test_a_failed_history_lookup_still_writes_the_footer(run_event, event, api, capsys):
    api.body = "Body."
    api.edits_error = urllib.error.HTTPError("/graphql", 502, "Bad Gateway", {}, None)

    assert run_event(edit_event(event)) == 0

    assert users_in(api.patches[0]["body"]) == ["tester"]
    assert "edit history" in capsys.readouterr().err


def test_a_description_never_edited_before_has_nothing_to_recover(run_event, event, api):
    api.body = "Body."
    api.edits = [(JUST_NOW, "Body.")]

    run_event(edit_event(event))

    assert api.patches[0]["body"] == f"Body.\n\n---\n\n{FOOTER}\n"


# --- issues ------------------------------------------------------------------


def issue_event(event, command, stdout):
    return event(tool_input={"command": command}, tool_response={"stdout": stdout})


@pytest.mark.parametrize(
    "command,stdout",
    [
        pytest.param("gh issue create --title Bug --body 'It broke'",
                     "https://github.com/o/r/issues/42\n", id="create"),
        pytest.param("gh issue edit 42 --add-label bug",
                     "https://github.com/o/r/issues/42\n", id="edit"),
        pytest.param("gh issue comment 42 --body 'Still broken'",
                     "https://github.com/o/r/issues/42#issuecomment-2891234567\n", id="comment"),
    ],
)
def test_gh_issue_routes_write_the_footer_into_the_issue(run_event, event, api, command, stdout):
    api.body = "It broke."

    run_event(issue_event(event, command, stdout))

    assert api.pr_calls == [("GET", "/repos/o/r/issues/42"), ("PATCH", "/repos/o/r/issues/42")]
    assert api.patches[0]["body"] == f"It broke.\n\n---\n\n{FOOTER}\n"


def test_gh_issue_comment_on_a_pr_number_updates_the_pr(run_event, event, api):
    """gh issue comment accepts PR numbers; the /pull/ URL it prints says so."""
    run_event(issue_event(
        event, "gh issue comment 7 -b hi", "https://github.com/o/r/pull/7#issuecomment-1\n"
    ))

    assert api.pr_calls[0] == ("GET", "/repos/o/r/pulls/7")


@pytest.mark.parametrize(
    "command,stdout",
    [
        pytest.param("gh issue create --web", "", id="web-prints-no-url"),
        pytest.param("gh issue list", "https://github.com/o/r/issues/1\n", id="read-only-subcommand"),
        pytest.param("gh issue view 1", "https://github.com/o/r/issues/1\n", id="view"),
    ],
)
def test_other_gh_issue_calls_touch_nothing(run_event, event, api, command, stdout):
    assert run_event(issue_event(event, command, stdout)) == 0
    assert api.calls == []


def test_gh_issue_edit_of_the_body_recovers_dropped_footers(run_event, event, api):
    api.body = "Rewritten issue."
    api.edits = [("2026-09-10T09:00:00Z", f"Old issue.\n\n---\n\n{THEIRS}\n"), (JUST_NOW, api.body)]

    run_event(issue_event(event, "gh issue edit 42 --body-file body.md", "https://github.com/o/r/issues/42\n"))

    assert api.history_calls[0]["variables"] == {"owner": "o", "repo": "r", "number": 42}
    assert "issueOrPullRequest" in api.history_calls[0]["query"]
    assert api.patches[0]["body"] == f"Rewritten issue.\n\n---\n\n{THEIRS}\n\n{FOOTER}\n"


def test_gh_issue_edit_without_a_body_skips_history(run_event, event, api):
    run_event(issue_event(event, "gh issue edit 42 --title New", "https://github.com/o/r/issues/42\n"))
    assert api.history_calls == []


def test_mcp_issue_create_writes_the_footer(run_event, api):
    event = mcp_event(
        "issue_write",
        response={"type": "text", "text": '{"id":"3456789012","url":"https://github.com/o/r/issues/42"}'},
        tool_input={"method": "create", "owner": "o", "repo": "r", "title": "Bug",
                    "body": "Same as https://github.com/o/r/issues/1"},
    )

    run_event(event)

    # The URL gives the number; the database id is never used.
    assert api.pr_calls == [("GET", "/repos/o/r/issues/42"), ("PATCH", "/repos/o/r/issues/42")]
    assert api.history_calls == []


def test_mcp_issue_create_without_a_url_touches_nothing(run_event, api):
    event = mcp_event(
        "issue_write",
        response={"type": "text", "text": "created"},
        tool_input={"method": "create", "owner": "o", "repo": "r", "title": "Bug",
                    "body": "Same as https://github.com/o/r/issues/1"},
    )

    assert run_event(event) == 0

    assert api.calls == []


def test_mcp_issue_update_uses_the_input_and_recovers(run_event, api):
    api.body = "Rewritten issue."
    api.edits = [("2026-09-10T09:00:00Z", f"Old.\n\n---\n\n{THEIRS}\n"), (JUST_NOW, api.body)]
    event = mcp_event(
        "issue_write",
        response={"type": "text", "text": '{"id":"1","url":"https://github.com/o/r/issues/42"}'},
        tool_input={"method": "update", "owner": "o", "repo": "r", "issue_number": 42,
                    "body": "Rewritten issue."},
    )

    run_event(event)

    assert api.pr_calls == [("GET", "/repos/o/r/issues/42"), ("PATCH", "/repos/o/r/issues/42")]
    assert users_in(api.patches[0]["body"]) == ["alice", "tester"]


def test_mcp_issue_state_change_skips_history(run_event, api):
    event = mcp_event(
        "issue_write",
        response={"type": "text", "text": '{"id":"1","url":"https://github.com/o/r/issues/42"}'},
        tool_input={"method": "update", "owner": "o", "repo": "r", "issue_number": 42, "state": "closed"},
    )

    run_event(event)

    assert api.patches
    assert api.history_calls == []
