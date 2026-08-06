"""Uninstall tests: removing our entries and nothing else."""
import json

import pytest

import claude_pr_resume_hook as hook


def uninstall(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["claude-pr-resume-hook", "uninstall", *args])
    return hook.main()


def install(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["claude-pr-resume-hook", "install", *args])
    return hook.main()


def read(path):
    return json.loads(path.read_text())


def seed(path, settings):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings))


def test_install_then_uninstall_round_trips_to_nothing(home, shim, monkeypatch):
    path = home / ".claude/settings.json"
    seed(path, {"model": "opus"})

    install(monkeypatch)
    assert uninstall(monkeypatch) == 0

    assert read(path) == {"model": "opus"}


def test_reports_each_removed_entry(home, shim, monkeypatch, capsys):
    install(monkeypatch)
    capsys.readouterr()

    uninstall(monkeypatch)

    out = capsys.readouterr().out
    assert "gh pr create" in out and "gh pr edit" in out
    assert out.count("removed") >= 2
    assert "restart Claude Code" in out


def test_empty_containers_are_pruned(home, shim, monkeypatch):
    install(monkeypatch)

    uninstall(monkeypatch)

    assert read(home / ".claude/settings.json") == {}


def test_emptied_matcher_group_is_dropped_and_reported(home, shim, monkeypatch, capsys):
    install(monkeypatch)
    capsys.readouterr()

    uninstall(monkeypatch)

    assert "emptied, dropped" in capsys.readouterr().out


def test_unrelated_hooks_and_settings_survive(home, shim, monkeypatch):
    path = home / ".claude/settings.json"
    seed(path, {
        "model": "opus",
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo pre"}]}],
            "PostToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "echo fmt"}]}],
        },
    })
    install(monkeypatch)

    uninstall(monkeypatch)

    settings = read(path)
    assert settings["model"] == "opus"
    assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "echo pre"
    assert settings["hooks"]["PostToolUse"] == [
        {"matcher": "Write", "hooks": [{"type": "command", "command": "echo fmt"}]}
    ]


def test_sibling_entries_in_our_bash_group_survive(home, shim, monkeypatch):
    path = home / ".claude/settings.json"
    seed(path, {"hooks": {"PostToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo other"}]}
    ]}})
    install(monkeypatch)

    uninstall(monkeypatch)

    groups = read(path)["hooks"]["PostToolUse"]
    assert groups == [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo other"}]}]


def test_removes_the_mcp_group_too(home, shim, monkeypatch, capsys):
    install(monkeypatch)
    groups = read(home / ".claude/settings.json")["hooks"]["PostToolUse"]
    assert [g["matcher"] for g in groups] == ["Bash", hook.MCP_MATCHER]
    capsys.readouterr()

    uninstall(monkeypatch)

    out = capsys.readouterr().out
    assert "github mcp pull requests" in out
    assert read(home / ".claude/settings.json") == {}


def test_an_unrelated_mcp_group_survives(home, shim, monkeypatch):
    path = home / ".claude/settings.json"
    seed(path, {"hooks": {"PostToolUse": [
        {"matcher": "mcp__memory__.*", "hooks": [{"type": "command", "command": "echo mem"}]}
    ]}})
    install(monkeypatch)

    uninstall(monkeypatch)

    assert read(path)["hooks"]["PostToolUse"] == [
        {"matcher": "mcp__memory__.*", "hooks": [{"type": "command", "command": "echo mem"}]}
    ]


def test_removes_a_legacy_installation(home, shim, monkeypatch):
    """Uninstall must clean up entries this version never wrote."""
    path = home / ".claude/settings.json"
    seed(path, {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "if": "Bash(gh pr create*)",
         "command": "python3 /old/append_resume_footer.py"},
        {"type": "command", "if": "Bash(gh pr edit*)",
         "command": "uvx --from git+https://example.com/x claude-pr-resume-hook"},
    ]}]}})

    uninstall(monkeypatch)

    assert read(path) == {}


def test_writes_nothing_when_not_installed(home, shim, monkeypatch, capsys):
    path = home / ".claude/settings.json"
    seed(path, {"model": "opus"})
    backup = home / ".claude/settings.json.bak"
    backup.write_text("SENTINEL")

    assert uninstall(monkeypatch) == 0

    assert "not installed, nothing written" in capsys.readouterr().out
    assert backup.read_text() == "SENTINEL"


def test_missing_settings_file_is_not_an_error(home, shim, monkeypatch, capsys):
    assert uninstall(monkeypatch) == 0
    assert "nothing to do" in capsys.readouterr().out


def test_dry_run_writes_nothing(home, shim, monkeypatch, capsys):
    install(monkeypatch)
    before = (home / ".claude/settings.json").read_text()
    capsys.readouterr()

    assert uninstall(monkeypatch, "--dry-run") == 0

    assert (home / ".claude/settings.json").read_text() == before
    assert "dry run, nothing written" in capsys.readouterr().out


def test_hints_at_uv_tool_uninstall_while_the_shim_remains(home, shim, monkeypatch, capsys):
    install(monkeypatch)
    capsys.readouterr()

    uninstall(monkeypatch)

    assert f"uv tool uninstall {hook.CONSOLE_SCRIPT}" in capsys.readouterr().out


def test_backs_up_before_removing(home, shim, monkeypatch):
    install(monkeypatch)
    installed = (home / ".claude/settings.json").read_text()

    uninstall(monkeypatch)

    assert (home / ".claude/settings.json.bak").read_text() == installed


@pytest.mark.parametrize("scope,relative", [
    ("project", ".claude/settings.json"),
    ("local", ".claude/settings.local.json"),
])
def test_scopes_are_honored(home, shim, monkeypatch, tmp_path, scope, relative):
    install(monkeypatch, "--scope", scope)
    target = tmp_path / "project" / relative
    assert target.exists()

    uninstall(monkeypatch, "--scope", scope)

    assert read(target) == {}


def test_invalid_json_aborts_without_writing(home, shim, monkeypatch):
    path = home / ".claude/settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ nope")

    with pytest.raises(SystemExit, match="not valid JSON"):
        uninstall(monkeypatch)

    assert path.read_text() == "{ nope"
