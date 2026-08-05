"""Integration tests against a really-installed uv tool.

These run `uv tool install` for real, but redirected into a temp directory via
UV_TOOL_DIR/UV_TOOL_BIN_DIR, so they never touch the developer's own tools.
Slower than the rest of the suite; deselect with `-m 'not integration'`.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENT = json.dumps({"tool_name": "Edit", "tool_input": {}, "cwd": "/x", "session_id": "y"})

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed"),
]


@pytest.fixture(scope="session")
def tool_root(tmp_path_factory):
    """Install the tool for real, into an isolated uv tool dir."""
    root = tmp_path_factory.mktemp("uvtool")
    env = {
        **os.environ,
        "UV_TOOL_DIR": str(root / "tools"),
        "UV_TOOL_BIN_DIR": str(root / "bin"),
    }
    subprocess.run(
        ["uv", "tool", "install", "--force", "-e", REPO],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    return root


@pytest.fixture(scope="session")
def shim(tool_root):
    shim = tool_root / "bin" / "claude-pr-resume-hook"
    assert shim.exists(), f"uv tool install produced no shim in {tool_root / 'bin'}"
    return shim


def run(shim, *args, stdin=None, home=None):
    """Run the shim with the shim's own directory on PATH, HOME redirected."""
    env = {**os.environ, "PATH": f"{shim.parent}{os.pathsep}{os.environ['PATH']}"}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run(
        [str(shim), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def settings_of(home):
    return json.loads((home / ".claude" / "settings.json").read_text())


def entries_of(home):
    return settings_of(home)["hooks"]["PostToolUse"][0]["hooks"]


def test_the_console_script_is_installed_and_runs(shim):
    result = run(shim, "--help")

    assert result.returncode == 0, result.stderr
    assert "install" in result.stdout
    assert "uninstall" in result.stdout


def test_install_writes_the_absolute_shim_path(shim, tmp_path):
    result = run(shim, "install", home=tmp_path)

    assert result.returncode == 0, result.stderr
    commands = {e["command"] for e in entries_of(tmp_path)}
    assert commands == {str(shim.resolve())}
    assert [e["if"] for e in entries_of(tmp_path)] == ["Bash(gh pr create*)", "Bash(gh pr edit*)"]


def test_the_installed_command_string_actually_executes(shim, tmp_path):
    """Whatever install writes into settings must be a runnable command."""
    assert run(shim, "install", home=tmp_path).returncode == 0
    command = entries_of(tmp_path)[0]["command"]

    result = subprocess.run(
        command, shell=True, input=EVENT, capture_output=True, text=True, timeout=300
    )

    assert result.returncode == 0, result.stderr


def test_reinstall_is_a_no_op(shim, tmp_path):
    assert run(shim, "install", home=tmp_path).returncode == 0
    before = (tmp_path / ".claude" / "settings.json").read_text()

    result = run(shim, "install", home=tmp_path)

    assert "already up to date" in result.stdout
    assert (tmp_path / ".claude" / "settings.json").read_text() == before


def test_uninstall_removes_what_install_wrote(shim, tmp_path):
    assert run(shim, "install", home=tmp_path).returncode == 0

    result = run(shim, "uninstall", home=tmp_path)

    assert result.returncode == 0, result.stderr
    assert settings_of(tmp_path) == {}
    assert "uv tool uninstall" in result.stdout


def test_install_honors_claude_config_dir(shim, tmp_path):
    config_dir = tmp_path / "cfg"
    env = {
        **os.environ,
        "PATH": f"{shim.parent}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "CLAUDE_CONFIG_DIR": str(config_dir),
    }
    result = subprocess.run(
        [str(shim), "install"], capture_output=True, text=True, timeout=300, env=env
    )

    assert result.returncode == 0, result.stderr
    assert (config_dir / "settings.json").exists()
    assert not (tmp_path / ".claude").exists()


def test_install_refuses_when_the_shim_is_not_on_path(shim, tmp_path):
    """Run the shim with a PATH that cannot find it: install must refuse."""
    empty = tmp_path / "empty"
    empty.mkdir()
    result = subprocess.run(
        [str(shim), "install"],
        capture_output=True,
        text=True,
        timeout=300,
        env={"PATH": str(empty), "HOME": str(tmp_path)},
    )

    assert result.returncode != 0
    assert "uv tool install" in result.stderr
    assert not (tmp_path / ".claude").exists()


def test_hook_mode_ignores_an_unrelated_event(shim):
    """The no-argument path works end to end and makes no network calls."""
    result = run(shim, stdin=EVENT)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_editable_install_runs_this_checkout(tool_root):
    """`uv tool install -e` must resolve the module to the working tree, not a copy.

    This is the documented development workflow, so it is worth pinning down.
    """
    python = tool_root / "tools" / "claude-pr-resume-hook" / "bin" / "python"
    if not python.exists():
        pytest.skip(f"unexpected uv tool layout: no python at {python}")

    result = subprocess.run(
        [str(python), "-c", "import claude_pr_resume_hook as m; print(m.__file__)"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )

    assert result.stdout.strip() == os.path.join(REPO, "claude_pr_resume_hook.py")


def test_module_stays_runnable_by_path():
    """`python3 claude_pr_resume_hook.py` still works for uv-less setups."""
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "claude_pr_resume_hook.py")],
        input=EVENT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
