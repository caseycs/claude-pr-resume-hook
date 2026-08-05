"""Unit tests for the footer text itself.

The replacement contract, in four cases:
  * no footer yet            -> append one
  * footer removed by hand   -> append one again
  * footer changed (mangled, moved, or from another session) -> replace it
  * footer already correct   -> leave the body byte-for-byte alone
"""
import pytest

import claude_pr_resume_hook as hook

FOOTER = "Resume session: `cd /work/tree; claude -r sess-abc`"


def build(body):
    return hook.build_body(body, "/work/tree", "sess-abc")


def resume_lines(body):
    return [line for line in body.splitlines() if "Resume session" in line]


# --- how the directory is rendered ------------------------------------------


@pytest.fixture
def at_home(monkeypatch, tmp_path):
    """Pin $HOME so tilde-relative rendering is deterministic."""
    monkeypatch.setenv("HOME", "/Users/someone")
    monkeypatch.setattr(hook.Path, "home", staticmethod(lambda: hook.Path("/Users/someone")))


def test_path_under_home_is_tilde_relative(at_home):
    body = hook.build_body("", "/Users/someone/github/repo", "s1")
    assert body == "Resume session: `cd ~/github/repo; claude -r s1`\n"
    assert "someone" not in body


def test_home_itself_is_bare_tilde(at_home):
    assert hook.footer_for("/Users/someone", "s1") == "Resume session: `cd ~; claude -r s1`"


def test_path_outside_home_stays_absolute(at_home):
    assert "cd /Volumes/scratch/wt;" in hook.footer_for("/Volumes/scratch/wt", "s1")


def test_a_sibling_of_home_is_not_mistaken_for_home(at_home):
    """/Users/someone-else must not be rewritten as ~-else."""
    assert "cd /Users/someone-else/repo;" in hook.footer_for("/Users/someone-else/repo", "s1")


@pytest.mark.parametrize(
    "cwd,expected",
    [
        ("/Users/someone/my repo", "~/my\\ repo"),
        ("/Users/someone/a b/c d", "~/a\\ b/c\\ d"),
        ("/Users/someone/pr(1)", "~/pr\\(1\\)"),
        ("/Users/someone/it's", "~/it\\'s"),
        ("/Users/someone/a&b", "~/a\\&b"),
        ("/Users/someone/plain-ok_1.2", "~/plain-ok_1.2"),
    ],
)
def test_shell_metacharacters_are_backslash_escaped(at_home, cwd, expected):
    assert hook.footer_for(cwd, "s1") == f"Resume session: `cd {expected}; claude -r s1`"


def test_escaping_leaves_the_tilde_and_slashes_bare(at_home):
    """Backslash escaping, not quoting, so `cd ~/...` still expands."""
    footer = hook.footer_for("/Users/someone/my repo", "s1")
    assert "cd ~/" in footer
    assert "'" not in footer and '"' not in footer


# --- no footer yet -----------------------------------------------------------


def test_appends_to_existing_body():
    assert build("Some description.") == f"Some description.\n\n{FOOTER}\n"


def test_empty_body_gets_only_the_footer():
    assert build("") == f"{FOOTER}\n"
    assert build(None) == f"{FOOTER}\n"


def test_normalizes_trailing_whitespace():
    assert build("Some description.\n\n\n") == f"Some description.\n\n{FOOTER}\n"


# --- footer already correct --------------------------------------------------


def test_correct_footer_is_left_untouched():
    body = f"Some description.\n\n{FOOTER}\n"
    assert build(body) == body


def test_is_idempotent_over_repeated_runs():
    body = build("Some description.")
    for _ in range(3):
        assert build(body) == body
        body = build(body)


def test_crlf_body_is_recognized_as_unchanged():
    """GitHub returns CRLF; the caller normalizes before comparing."""
    body = f"Some description.\r\n\r\n{FOOTER}\r\n"
    assert build(body) == hook.normalize(body)


# --- footer removed by hand -------------------------------------------------


def test_manually_removed_footer_is_added_back():
    body = build("Some description.")
    edited = body.replace(f"\n\n{FOOTER}\n", "")

    assert resume_lines(edited) == []
    assert build(edited) == f"Some description.\n\n{FOOTER}\n"


def test_body_emptied_by_hand_still_gets_a_footer():
    assert build("   \n\n  ") == f"{FOOTER}\n"


# --- footer changed ---------------------------------------------------------


def test_replaces_a_footer_from_another_session():
    stale = "Some description.\n\nResume session: `cd /other; claude -r old-session`\n"

    result = build(stale)

    assert result == f"Some description.\n\n{FOOTER}\n"
    assert "old-session" not in result


def test_replaces_a_footer_whose_cwd_moved():
    stale = "Body.\n\nResume session: `cd /old/worktree; claude -r sess-abc`\n"
    assert build(stale) == f"Body.\n\n{FOOTER}\n"


def test_migrates_an_old_absolute_path_footer(at_home):
    """Footers written before tilde-relative paths must be rewritten, not stacked."""
    old = "Body.\n\nResume session: `cd /Users/someone/github/repo; claude -r s1`\n"

    result = hook.build_body(old, "/Users/someone/github/repo", "s1")

    assert result == "Body.\n\nResume session: `cd ~/github/repo; claude -r s1`\n"
    assert result.count("Resume session:") == 1


@pytest.mark.parametrize(
    "mangled",
    [
        pytest.param("Resume session: cd /a; claude -r x", id="backticks-stripped"),
        pytest.param("**Resume session:** `cd /a; claude -r x`", id="bolded"),
        pytest.param("_Resume session: `cd /a; claude -r x`_", id="italicized"),
        pytest.param("> Resume session: `cd /a; claude -r x`", id="blockquoted"),
        pytest.param("  Resume session: `cd /a; claude -r x`", id="indented"),
        pytest.param("resume session: `cd /a; claude -r x`", id="lowercased"),
        pytest.param("Resume session: (deleted the command)", id="tail-rewritten"),
        pytest.param("Resume session:", id="tail-deleted-entirely"),
    ],
)
def test_replaces_a_hand_mangled_footer(mangled):
    result = build(f"Some description.\n\n{mangled}\n")

    assert resume_lines(result) == [FOOTER]
    assert result == f"Some description.\n\n{FOOTER}\n"


def test_replaces_a_footer_that_was_moved_up_the_body():
    body = "Intro.\n\nResume session: `cd /a; claude -r x`\n\nMore prose."

    result = build(body)

    assert resume_lines(result) == [FOOTER]
    assert result.endswith(f"{FOOTER}\n")
    assert "Intro." in result and "More prose." in result


def test_restores_the_blank_line_when_one_newline_was_deleted():
    """Footer on the line right below the body, with no blank line between."""
    body = "Some description.\nResume session: `cd /a; claude -r x`\n"

    result = build(body)

    assert result == f"Some description.\n\n{FOOTER}\n"
    assert resume_lines(result) == [FOOTER]


def test_separates_the_footer_when_both_newlines_were_deleted():
    """Footer glued onto the end of the last prose line."""
    body = "Some description.Resume session: `cd /a; claude -r x`"

    result = build(body)

    assert result == f"Some description.\n\n{FOOTER}\n"
    assert resume_lines(result) == [FOOTER]


def test_glued_footer_mid_body_is_pulled_out():
    body = "Intro.Resume session: `cd /a; claude -r x`\n\nMore prose."

    result = build(body)

    assert resume_lines(result) == [FOOTER]
    assert result == f"Intro.\n\nMore prose.\n\n{FOOTER}\n"


def test_glued_footer_result_is_itself_stable():
    once = build("Some description.Resume session: `cd /a; claude -r x`")
    assert build(once) == once


def test_prose_mentioning_the_footer_inline_is_not_eaten():
    body = "The hook adds a Resume session line at the end."

    result = build(body)

    assert result == f"{body}\n\n{FOOTER}\n"


def test_collapses_several_stacked_footers():
    body = (
        "Body.\n\n"
        "Resume session: `cd /a; claude -r one`\n"
        "Resume session: `cd /b; claude -r two`\n"
    )

    assert resume_lines(build(body)) == [FOOTER]


def test_moved_footer_result_is_itself_stable():
    once = build("Intro.\n\nResume session: `cd /a; claude -r x`\n\nMore prose.")
    assert build(once) == once
