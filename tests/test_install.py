"""Installation tests: what `install` reconciles into a settings file."""
import json

import pytest

import claude_pr_resume_hook as hook


def install(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["claude-pr-resume-hook", "install", *args])
    return hook.main()


def read(path):
    return json.loads(path.read_text())


def our_entries(settings):
    return [
        entry
        for group in settings["hooks"]["PostToolUse"]
        for entry in group["hooks"]
        if hook.is_ours(entry)
    ]


def seed(path, settings):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings))


# --- the shim is a precondition ---------------------------------------------


def test_writes_the_absolute_resolved_shim_path(home, shim, monkeypatch):
    assert install(monkeypatch) == 0

    entries = our_entries(read(home / ".claude/settings.json"))
    assert [e["command"] for e in entries] == [shim, shim]
    assert entries[0]["command"].startswith("/")


def test_refuses_when_the_shim_is_missing(home, no_shim, monkeypatch, capsys):
    with pytest.raises(SystemExit) as excinfo:
        install(monkeypatch)

    message = str(excinfo.value)
    assert "not found on PATH" in message
    assert f"uv tool install {hook.TOOL_SOURCE}" in message
    assert not (home / ".claude/settings.json").exists()


def test_mentions_uv_when_uv_itself_is_absent(home, no_shim, monkeypatch):
    with pytest.raises(SystemExit) as excinfo:
        install(monkeypatch)

    assert "uv itself is missing" in str(excinfo.value)


def test_does_not_mention_uv_when_uv_is_present(home, monkeypatch, tmp_path):
    """Shim absent but uv available: the hint should not nag about uv."""
    fake_bin = tmp_path / "uvonly"
    fake_bin.mkdir()
    uv = fake_bin / "uv"
    uv.write_text("#!/bin/sh\nexit 0\n")
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))

    with pytest.raises(SystemExit) as excinfo:
        install(monkeypatch)

    assert "uv itself is missing" not in str(excinfo.value)


# --- structure written -------------------------------------------------------


def test_creates_user_settings_from_scratch(home, shim, monkeypatch):
    install(monkeypatch)

    settings = read(home / ".claude/settings.json")
    group = settings["hooks"]["PostToolUse"][0]
    assert group["matcher"] == "Bash"
    assert [e["if"] for e in group["hooks"]] == ["Bash(gh pr create*)", "Bash(gh pr edit*)"]
    assert all(e["type"] == "command" for e in group["hooks"])


@pytest.mark.parametrize(
    "scope,relative",
    [
        ("user", None),
        ("project", ".claude/settings.json"),
        ("local", ".claude/settings.local.json"),
    ],
)
def test_each_scope_writes_its_own_file(home, shim, monkeypatch, tmp_path, scope, relative):
    install(monkeypatch, "--scope", scope)

    expected = home / ".claude/settings.json" if relative is None else tmp_path / "project" / relative
    assert len(our_entries(read(expected))) == 2


def test_unknown_scope_is_rejected(home, shim, monkeypatch):
    with pytest.raises(SystemExit):
        install(monkeypatch, "--scope", "nonsense")


# --- CLAUDE_CONFIG_DIR -------------------------------------------------------


def test_user_scope_honors_claude_config_dir(home, shim, monkeypatch, tmp_path):
    config_dir = tmp_path / "custom-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    install(monkeypatch)

    assert len(our_entries(read(config_dir / "settings.json"))) == 2
    assert not (home / ".claude" / "settings.json").exists()


def test_claude_config_dir_is_created_if_absent(home, shim, monkeypatch, tmp_path):
    config_dir = tmp_path / "deep" / "nested" / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    assert install(monkeypatch) == 0
    assert (config_dir / "settings.json").exists()


def test_claude_config_dir_expands_tilde(home, shim, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/elsewhere")

    install(monkeypatch)

    assert (home / "elsewhere" / "settings.json").exists()


def test_empty_claude_config_dir_falls_back_to_home(home, shim, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "")

    install(monkeypatch)

    assert (home / ".claude" / "settings.json").exists()


def test_project_scopes_ignore_claude_config_dir(home, shim, monkeypatch, tmp_path):
    config_dir = tmp_path / "custom-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    install(monkeypatch, "--scope", "project")

    assert (tmp_path / "project" / ".claude" / "settings.json").exists()
    assert not config_dir.exists()


# --- reconciliation ----------------------------------------------------------


def test_reports_adding_on_a_fresh_install(home, shim, monkeypatch, capsys):
    install(monkeypatch)

    out = capsys.readouterr().out
    assert "gh pr create" in out and "adding" in out
    assert "updated - restart Claude Code" in out


def test_reports_up_to_date_and_writes_nothing_on_reinstall(home, shim, monkeypatch, capsys):
    install(monkeypatch)
    backup = home / ".claude/settings.json.bak"
    backup.write_text("SENTINEL")
    capsys.readouterr()

    install(monkeypatch)

    out = capsys.readouterr().out
    assert "up to date" in out
    assert "already up to date, nothing written" in out
    # Proof no write happened: the backup would have been overwritten.
    assert backup.read_text() == "SENTINEL"


def test_updates_a_stale_path_and_reports_the_change(home, shim, monkeypatch, capsys):
    path = home / ".claude/settings.json"
    old = "/old/checkout/append_resume_footer.py"
    seed(path, {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "if": "Bash(gh pr create*)", "command": f"python3 {old}"},
        {"type": "command", "if": "Bash(gh pr edit*)", "command": f"python3 {old}"},
    ]}]}})

    install(monkeypatch)

    out = capsys.readouterr().out
    assert "stale path, updating" in out
    assert old in out
    assert shim in out
    entries = our_entries(read(path))
    assert [e["command"] for e in entries] == [shim, shim]


def test_removes_legacy_entries_that_do_not_match_our_filters(home, shim, monkeypatch, capsys):
    path = home / ".claude/settings.json"
    seed(path, {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "python3 /old/append_resume_footer.py"},
        {"type": "command", "if": "Bash(gh pr merge*)",
         "command": "python3 /old/append_resume_footer.py"},
    ]}]}})

    install(monkeypatch)

    assert "2 removed" in capsys.readouterr().out
    entries = our_entries(read(path))
    assert len(entries) == 2
    assert all(e["command"] == shim for e in entries)


def test_installing_twice_does_not_duplicate(home, shim, monkeypatch):
    install(monkeypatch)
    first = read(home / ".claude/settings.json")
    install(monkeypatch)

    second = read(home / ".claude/settings.json")
    assert first == second
    assert len(our_entries(second)) == 2


def test_reinstall_after_the_shim_moves_rewrites_the_path(home, shim, monkeypatch, tmp_path):
    install(monkeypatch)

    moved = tmp_path / "bin2"
    moved.mkdir()
    exe = moved / hook.CONSOLE_SCRIPT
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(moved))

    install(monkeypatch)

    entries = our_entries(read(home / ".claude/settings.json"))
    assert [e["command"] for e in entries] == [str(exe.resolve())] * 2


# --- merging into existing settings ------------------------------------------


def test_unrelated_settings_are_preserved(home, shim, monkeypatch):
    path = home / ".claude/settings.json"
    seed(path, {
        "model": "opus",
        "permissions": {"allow": ["Bash(ls)"]},
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo pre"}]}],
            "PostToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "echo fmt"}]}],
        },
    })

    install(monkeypatch)

    settings = read(path)
    assert settings["model"] == "opus"
    assert settings["permissions"] == {"allow": ["Bash(ls)"]}
    assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "echo pre"
    write_group = next(g for g in settings["hooks"]["PostToolUse"] if g["matcher"] == "Write")
    assert write_group["hooks"][0]["command"] == "echo fmt"
    assert len(our_entries(settings)) == 2


def test_joins_an_existing_bash_matcher_group(home, shim, monkeypatch):
    path = home / ".claude/settings.json"
    seed(path, {"hooks": {"PostToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo other"}]}
    ]}})

    install(monkeypatch)

    groups = read(path)["hooks"]["PostToolUse"]
    assert len(groups) == 1
    assert groups[0]["hooks"][0]["command"] == "echo other"
    assert len(groups[0]["hooks"]) == 3


def test_emptied_matcher_group_is_dropped(home, shim, monkeypatch):
    path = home / ".claude/settings.json"
    seed(path, {"hooks": {"PostToolUse": [
        {"matcher": "Write", "hooks": [{"type": "command", "command": "uvx --from x claude-pr-resume-hook"}]},
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo other"}]},
    ]}})

    install(monkeypatch)

    groups = read(path)["hooks"]["PostToolUse"]
    assert [g["matcher"] for g in groups] == ["Bash"]


# --- safety ------------------------------------------------------------------


def test_existing_settings_are_backed_up(home, shim, monkeypatch):
    path = home / ".claude/settings.json"
    original = json.dumps({"model": "opus"})
    seed(path, {"model": "opus"})

    install(monkeypatch)

    assert (home / ".claude/settings.json.bak").read_text() == original


def test_no_backup_when_there_was_no_file(home, shim, monkeypatch):
    install(monkeypatch)
    assert not (home / ".claude/settings.json.bak").exists()


def test_dry_run_writes_nothing(home, shim, monkeypatch, capsys):
    assert install(monkeypatch, "--dry-run") == 0

    assert not (home / ".claude/settings.json").exists()
    out = capsys.readouterr().out
    assert "dry run, nothing written" in out
    assert "Bash(gh pr create*)" in out


def test_dry_run_still_reports_what_would_change(home, shim, monkeypatch, capsys):
    seed(home / ".claude/settings.json", {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "if": "Bash(gh pr create*)", "command": "python3 /old/append_resume_footer.py"},
    ]}]}})

    install(monkeypatch, "--dry-run")

    out = capsys.readouterr().out
    assert "stale path, updating" in out
    assert "adding" in out


def test_invalid_json_settings_aborts_without_writing(home, shim, monkeypatch):
    path = home / ".claude/settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ this is not json")

    with pytest.raises(SystemExit, match="not valid JSON"):
        install(monkeypatch)

    assert path.read_text() == "{ this is not json"


def test_non_object_settings_aborts(home, shim, monkeypatch):
    path = home / ".claude/settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]")

    with pytest.raises(SystemExit, match="does not contain a JSON object"):
        install(monkeypatch)


def test_malformed_hooks_section_aborts(home, shim, monkeypatch):
    seed(home / ".claude/settings.json", {"hooks": {"PostToolUse": "nope"}})

    with pytest.raises(SystemExit, match="not an array"):
        install(monkeypatch)


def test_malformed_hooks_object_aborts(home, shim, monkeypatch):
    seed(home / ".claude/settings.json", {"hooks": []})

    with pytest.raises(SystemExit, match="not an object"):
        install(monkeypatch)
