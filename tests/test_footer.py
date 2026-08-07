"""Unit tests for the footer text itself.

The contract, per user:
  * no footer for you yet     -> yours appended, everyone else's untouched
  * your footer removed       -> yours appended again
  * your session changed      -> your block rewritten in place
  * your footer already right -> body left byte-for-byte alone

Other people's footers are never rewritten, reordered, or removed.
"""
import pytest

import claude_pr_resume_hook as hook


def build(body, cwd="/work/tree", session="sess-abc", user="tester"):
    return hook.build_body(body, cwd, session, user)


def footer(user="tester", cwd="/work/tree", session="sess-abc"):
    return hook.footer_for(cwd, session, user)


def users_in(body):
    return [m.group("user") for m in hook.FOOTER_DETAILS_RE.finditer(body)]


def commands_in(body):
    return [line for line in body.splitlines() if line.startswith("cd ")]


# --- shape -------------------------------------------------------------------


def test_footer_is_a_collapsed_details_block():
    assert build("Some description.") == (
        "Some description.\n"
        "\n"
        "---\n"
        "\n"
        "<details>\n"
        "<summary>AI session - tester</summary>\n"
        "\n"
        "```\n"
        "cd /work/tree; claude -r sess-abc\n"
        "```\n"
        "\n"
        "</details>\n"
    )


def test_blank_lines_inside_details_keep_the_fence_rendering():
    """GitHub only renders markdown inside <details> when blank lines separate it."""
    block = footer()
    assert block.startswith("<details>\n<summary>")
    assert "</summary>\n\n```" in block
    assert "```\n\n</details>" in block


def test_the_rule_is_preceded_by_a_blank_line():
    """`text\\n---` is a setext H2 in markdown; a blank line keeps it an <hr>."""
    assert "Some description.\n\n---\n" in build("Some description.")


def test_empty_body_gets_no_rule():
    result = build("")
    assert result == f"{footer()}\n"
    assert "---" not in result


# --- one user ----------------------------------------------------------------


def test_correct_footer_is_left_untouched():
    body = build("Some description.")
    assert build(body) == body


def test_is_idempotent_over_repeated_runs():
    body = build("Some description.")
    for _ in range(3):
        assert build(body) == body
        body = build(body)


def test_a_new_session_rewrites_your_block():
    first = build("Body.", session="sess-1")

    second = build(first, session="sess-2")

    assert commands_in(second) == ["cd /work/tree; claude -r sess-2"]
    assert users_in(second) == ["tester"]


def test_a_moved_worktree_rewrites_your_block():
    first = build("Body.", cwd="/old/tree")
    second = build(first, cwd="/new/tree")
    assert commands_in(second) == ["cd /new/tree; claude -r sess-abc"]


def test_manually_removed_footer_is_added_back():
    body = build("Some description.")
    edited = body.split("\n\n---\n\n")[0]

    assert users_in(edited) == []
    assert build(edited) == body


def test_crlf_body_is_recognized_as_unchanged():
    body = build("Some description.").replace("\n", "\r\n")
    assert build(body) == hook.normalize(body)


# --- several users -----------------------------------------------------------


def test_a_second_user_appends_without_disturbing_the_first():
    alice = build("Body.", cwd="/a", session="sa", user="alice")

    both = build(alice, cwd="/b", session="sb", user="bob")

    assert users_in(both) == ["alice", "bob"]
    assert commands_in(both) == ["cd /a; claude -r sa", "cd /b; claude -r sb"]


def test_updating_your_block_preserves_position_and_others():
    both = build(build("Body.", cwd="/a", session="sa", user="alice"),
                 cwd="/b", session="sb", user="bob")

    result = build(both, cwd="/a2", session="sa2", user="alice")

    # alice stays first, bob is byte-for-byte unchanged
    assert users_in(result) == ["alice", "bob"]
    assert commands_in(result) == ["cd /a2; claude -r sa2", "cd /b; claude -r sb"]


def test_the_last_user_can_update_without_touching_earlier_ones():
    both = build(build("Body.", cwd="/a", session="sa", user="alice"),
                 cwd="/b", session="sb", user="bob")

    result = build(both, cwd="/b2", session="sb2", user="bob")

    assert commands_in(result) == ["cd /a; claude -r sa", "cd /b2; claude -r sb2"]


def test_three_users_all_survive():
    body = "Body."
    for name in ("alice", "bob", "carol"):
        body = build(body, cwd=f"/{name}", session=name, user=name)

    assert users_in(body) == ["alice", "bob", "carol"]
    assert len(commands_in(body)) == 3


def test_another_users_footer_is_never_removed_when_yours_is_added():
    theirs = build("Body.", cwd="/t", session="st", user="someone-else")

    mine = build(theirs)

    assert "someone-else" in mine
    assert "cd /t; claude -r st" in mine


def test_matching_is_case_insensitive():
    """GitHub logins are case-insensitive, so ALICE must not get a second block."""
    body = build("Body.", user="alice")

    result = build(body, cwd="/new", session="s2", user="ALICE")

    assert len(users_in(result)) == 1
    assert commands_in(result) == ["cd /new; claude -r s2"]


def test_a_login_that_is_a_prefix_of_another_gets_its_own_block():
    body = build("Body.", cwd="/a", session="sa", user="al")

    result = build(body, cwd="/b", session="sb", user="alice")

    assert users_in(result) == ["al", "alice"]


# --- migration from the older formats ---------------------------------------


def test_migrates_the_block_footer_format():
    old = (
        "Body.\n\n---\n\n"
        "Resume Claude session by `tester`:\n```\ncd /old; claude -r old-sess\n```\n"
    )

    result = build(old)

    assert "Resume Claude session by" not in result
    assert users_in(result) == ["tester"]
    assert commands_in(result) == ["cd /work/tree; claude -r sess-abc"]


def test_migrates_the_single_line_footer_format():
    old = "Body.\n\nResume session: `cd /old; claude -r old-sess`\n"

    result = build(old)

    assert "Resume session:" not in result
    assert users_in(result) == ["tester"]


def test_migration_does_not_strand_a_rule():
    old = "Body.\n\n---\n\nResume Claude session by `tester`:\n```\ncd /o; claude -r s\n```\n"
    assert build(old).count("---") == 1


# --- what must survive -------------------------------------------------------


def test_an_unrelated_details_block_survives():
    body = "Body.\n\n<details>\n<summary>Benchmark output</summary>\n\n```\n42\n```\n\n</details>"

    result = build(body)

    assert "Benchmark output" in result
    assert users_in(result) == ["tester"]


def test_an_unrelated_fenced_block_survives():
    body = "Run this:\n```\nmake test\n```\n\nThen review."

    result = build(body)

    assert "make test" in result
    assert commands_in(result) == ["cd /work/tree; claude -r sess-abc"]


def test_prose_mentioning_a_session_is_not_eaten():
    body = "This adds an AI session footer to every PR."

    result = build(body)

    assert result.startswith(f"{body}\n\n---\n")


def test_body_text_after_the_footers_is_kept():
    """A footer moved up the body must not silently drop the prose below it."""
    body = f"Intro.\n\n{footer()}\n\nMore prose."

    result = build(body)

    assert "Intro." in result and "More prose." in result
    assert users_in(result) == ["tester"]


# --- path rendering ----------------------------------------------------------


@pytest.fixture
def at_home(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/someone")
    monkeypatch.setattr(hook.Path, "home", staticmethod(lambda: hook.Path("/Users/someone")))


def test_path_under_home_is_tilde_relative(at_home):
    body = build("", cwd="/Users/someone/github/repo")
    assert "cd ~/github/repo; claude -r sess-abc" in body
    assert "/Users/someone" not in body


def test_home_itself_is_bare_tilde(at_home):
    assert "cd ~; claude -r s1" in hook.footer_for("/Users/someone", "s1", "u")


def test_path_outside_home_stays_absolute(at_home):
    assert "cd /Volumes/scratch/wt;" in hook.footer_for("/Volumes/scratch/wt", "s1", "u")


def test_a_sibling_of_home_is_not_mistaken_for_home(at_home):
    assert "cd /Users/someone-else/repo;" in hook.footer_for("/Users/someone-else/repo", "s1", "u")


@pytest.mark.parametrize(
    "cwd,expected",
    [
        ("/Users/someone/my repo", "~/my\\ repo"),
        ("/Users/someone/a b/c d", "~/a\\ b/c\\ d"),
        ("/Users/someone/pr(1)", "~/pr\\(1\\)"),
        ("/Users/someone/it's", "~/it\\'s"),
        ("/Users/someone/plain-ok_1.2", "~/plain-ok_1.2"),
    ],
)
def test_shell_metacharacters_are_backslash_escaped(at_home, cwd, expected):
    assert f"cd {expected}; claude -r s1" in hook.footer_for(cwd, "s1", "u")


def test_escaping_leaves_the_tilde_and_slashes_bare(at_home):
    block = hook.footer_for("/Users/someone/my repo", "s1", "u")
    assert "cd ~/" in block
    assert "'" not in block and '"' not in block
