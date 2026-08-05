"""Unit tests for the footer text itself.

The replacement contract, in four cases:
  * no footer yet            -> append one
  * footer removed by hand   -> append one again
  * footer changed (mangled, moved, or from another session) -> replace it
  * footer already correct   -> leave the body byte-for-byte alone
"""
import pytest

import claude_pr_resume_hook as hook

FOOTER = "Resume Claude session by `tester`:\n```\ncd /work/tree; claude -r sess-abc\n```"
# Bound before the autouse fixture below replaces the module attribute.
REAL_LOCAL_USER = hook.local_user


@pytest.fixture(autouse=True)
def known_user(monkeypatch):
    """Pin the local account name so expected footers are deterministic."""
    monkeypatch.setattr(hook, "local_user", lambda: "tester")


def build(body):
    return hook.build_body(body, "/work/tree", "sess-abc")


def resume_headings(body):
    return [line for line in body.splitlines() if "Resume" in line and "session" in line]


def commands_in(body):
    return [line for line in body.splitlines() if line.startswith("cd ")]


# --- shape -------------------------------------------------------------------


def test_footer_is_a_rule_a_heading_and_a_fenced_command():
    assert build("Some description.") == (
        "Some description.\n"
        "\n"
        "---\n"
        "\n"
        "Resume Claude session by `tester`:\n"
        "```\n"
        "cd /work/tree; claude -r sess-abc\n"
        "```\n"
    )


def test_the_rule_is_preceded_by_a_blank_line():
    """`text\\n---` is a setext H2 in markdown; a blank line keeps it an <hr>."""
    assert "Some description.\n\n---\n" in build("Some description.")


def test_empty_body_gets_no_leading_rule():
    """A description that is nothing but a horizontal rule reads as broken."""
    assert build("") == f"{FOOTER}\n"
    assert build(None) == f"{FOOTER}\n"
    assert not build("").startswith("---")


def test_the_local_username_is_named(monkeypatch):
    monkeypatch.setattr(hook, "local_user", lambda: "someone-else")
    assert "Resume Claude session by `someone-else`:" in hook.build_body("Body.", "/w", "s")


def test_an_unavailable_username_does_not_crash(monkeypatch):
    def boom():
        raise KeyError("no passwd entry")

    monkeypatch.setattr(hook.getpass, "getuser", boom)
    assert REAL_LOCAL_USER() == "unknown"


# --- how the directory is rendered ------------------------------------------


@pytest.fixture
def at_home(monkeypatch):
    """Pin $HOME so tilde-relative rendering is deterministic."""
    monkeypatch.setenv("HOME", "/Users/someone")
    monkeypatch.setattr(hook.Path, "home", staticmethod(lambda: hook.Path("/Users/someone")))


def test_path_under_home_is_tilde_relative(at_home):
    body = hook.build_body("", "/Users/someone/github/repo", "s1")
    assert "cd ~/github/repo; claude -r s1" in body
    assert "/Users/someone" not in body


def test_home_itself_is_bare_tilde(at_home):
    assert "cd ~; claude -r s1" in hook.footer_for("/Users/someone", "s1")


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
    assert f"cd {expected}; claude -r s1" in hook.footer_for(cwd, "s1")


def test_escaping_leaves_the_tilde_and_slashes_bare(at_home):
    """Backslash escaping, not quoting, so `cd ~/...` still expands."""
    footer = hook.footer_for("/Users/someone/my repo", "s1")
    assert "cd ~/" in footer
    assert "'" not in footer and '"' not in footer


# --- no footer yet -----------------------------------------------------------


def test_normalizes_trailing_whitespace():
    assert build("Some description.\n\n\n") == build("Some description.")


def test_a_body_that_already_ends_in_a_rule_is_left_alone():
    result = build("Some description.\n\n---")
    assert result.startswith("Some description.\n\n---\n\n---\n")


# --- footer already correct --------------------------------------------------


def test_correct_footer_is_left_untouched():
    body = build("Some description.")
    assert build(body) == body


def test_is_idempotent_over_repeated_runs():
    body = build("Some description.")
    for _ in range(3):
        assert build(body) == body
        body = build(body)


def test_is_idempotent_with_an_empty_body():
    body = build("")
    for _ in range(3):
        assert build(body) == body
        body = build(body)


def test_crlf_body_is_recognized_as_unchanged():
    """GitHub returns CRLF; the caller normalizes before comparing."""
    body = build("Some description.").replace("\n", "\r\n")
    assert build(body) == hook.normalize(body)


# --- footer removed by hand -------------------------------------------------


def test_manually_removed_footer_is_added_back():
    body = build("Some description.")
    edited = body.split("\n\n---\n\n")[0]

    assert resume_headings(edited) == []
    assert build(edited) == body


def test_body_emptied_by_hand_still_gets_a_footer():
    assert build("   \n\n  ") == f"{FOOTER}\n"


# --- footer changed ---------------------------------------------------------


def test_replaces_a_footer_from_another_session():
    stale = hook.build_body("Some description.", "/other", "old-session")

    result = build(stale)

    assert result == build("Some description.")
    assert "old-session" not in result


def test_replaces_a_footer_written_for_another_user(monkeypatch):
    monkeypatch.setattr(hook, "local_user", lambda: "colleague")
    theirs = hook.build_body("Body.", "/their/tree", "their-sess")
    monkeypatch.setattr(hook, "local_user", lambda: "tester")

    result = build(theirs)

    assert resume_headings(result) == ["Resume Claude session by `tester`:"]
    assert "colleague" not in result


def test_migrates_the_old_single_line_footer(at_home):
    """Footers written before the block format must be replaced, not stacked."""
    old = "Body.\n\nResume session: `cd ~/github/repo; claude -r s1`\n"

    result = hook.build_body(old, "/Users/someone/github/repo", "s1")

    assert resume_headings(result) == ["Resume Claude session by `tester`:"]
    assert commands_in(result) == ["cd ~/github/repo; claude -r s1"]
    assert "`cd ~/github/repo; claude -r s1`" not in result


def test_migrates_an_old_absolute_path_footer(at_home):
    old = "Body.\n\nResume session: `cd /Users/someone/github/repo; claude -r s1`\n"

    result = hook.build_body(old, "/Users/someone/github/repo", "s1")

    assert commands_in(result) == ["cd ~/github/repo; claude -r s1"]
    assert "/Users/someone" not in result


@pytest.mark.parametrize(
    "mangled",
    [
        pytest.param(
            "Resume Claude session by `tester`:\n```\ncd /elsewhere; claude -r other\n```",
            id="different-session",
        ),
        pytest.param(
            "**Resume Claude session by `tester`:**\n```\ncd /a; claude -r x\n```",
            id="heading-bolded",
        ),
        pytest.param(
            "Resume session by `tester`:\n```\ncd /a; claude -r x\n```",
            id="claude-word-dropped",
        ),
        pytest.param(
            "resume claude session:\n```\ncd /a; claude -r x\n```",
            id="lowercased-and-shortened",
        ),
        pytest.param(
            "Resume Claude session by `tester`:\n```sh\ncd /a; claude -r x\n```",
            id="fence-given-a-language",
        ),
        pytest.param(
            "Resume Claude session by `tester`:\n~~~\ncd /a; claude -r x\n~~~",
            id="tilde-fence",
        ),
        pytest.param(
            "Resume Claude session by `tester`:\n````\ncd /a; claude -r x\n````",
            id="four-backtick-fence",
        ),
        pytest.param(
            "> Resume Claude session by `tester`:\n> ```\n> cd /a; claude -r x\n> ```",
            id="blockquoted",
        ),
    ],
)
def test_replaces_a_hand_mangled_block_footer(mangled):
    result = build(f"Some description.\n\n---\n\n{mangled}\n")

    assert resume_headings(result) == ["Resume Claude session by `tester`:"]
    assert commands_in(result) == ["cd /work/tree; claude -r sess-abc"]


def test_replaces_a_block_whose_rule_was_deleted():
    body = f"Some description.\n\n{FOOTER}\n"

    result = build(body)

    assert result == build("Some description.")
    assert result.count("---") == 1


def test_replaces_a_block_whose_fence_was_deleted():
    body = "Some description.\n\n---\n\nResume Claude session by `tester`:\ncd /a; claude -r x\n"

    result = build(body)

    assert resume_headings(result) == ["Resume Claude session by `tester`:"]
    assert commands_in(result) == ["cd /a; claude -r x", "cd /work/tree; claude -r sess-abc"]


def test_replaces_a_footer_that_was_moved_up_the_body():
    body = f"Intro.\n\n{FOOTER}\n\nMore prose."

    result = build(body)

    assert resume_headings(result) == ["Resume Claude session by `tester`:"]
    assert result.endswith("```\n")
    assert "Intro." in result and "More prose." in result


def test_collapses_several_stacked_footers():
    body = f"Body.\n\n---\n\n{FOOTER}\n\n---\n\n{FOOTER}\n"

    result = build(body)

    assert resume_headings(result) == ["Resume Claude session by `tester`:"]
    assert commands_in(result) == ["cd /work/tree; claude -r sess-abc"]


def test_moved_footer_result_is_itself_stable():
    once = build(f"Intro.\n\n{FOOTER}\n\nMore prose.")
    assert build(once) == once


def test_mangled_footer_result_is_itself_stable():
    once = build("Body.\n\n**Resume session by `x`:**\n~~~\ncd /a; claude -r y\n~~~\n")
    assert build(once) == once


# --- what must survive -------------------------------------------------------


def test_prose_mentioning_the_footer_inline_is_not_eaten():
    body = "The hook adds a Resume Claude session block at the end."

    result = build(body)

    assert result.startswith(f"{body}\n\n---\n")


def test_an_unrelated_fenced_block_survives():
    body = "Run this:\n```\nmake test\n```\n\nThen review."

    result = build(body)

    assert "make test" in result
    assert commands_in(result) == ["cd /work/tree; claude -r sess-abc"]


def test_an_unrelated_horizontal_rule_survives():
    body = "Intro.\n\n---\n\n## Details\n\nMore."

    result = build(body)

    assert "## Details" in result
    assert result.count("---") == 2
