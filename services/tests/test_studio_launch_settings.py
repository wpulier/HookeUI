"""Exercise private launch defaults through the real shell, without starting services."""
import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "studio.sh"


def read_launch(tmp_path, settings=None, overrides=None, operation="start"):
    if settings is not None:
        (tmp_path / "launch.json").write_text(json.dumps(settings))
    env = {key: value for key, value in os.environ.items() if not key.startswith("MICROMIND_")}
    env.update(MICROMIND_ASSETS=str(tmp_path), **(overrides or {}))
    return subprocess.run(["bash", "-c",
        'script=$1; operation=$2; set -- "$operation"; source "$script"; printf "%s\\n" "${MICROMIND_HOME:-}" '
        '"${MICROMIND_KEYS_FILE:-}" "$LOG_DIR" "$SAVES_ROOT"', "launch-check", str(SCRIPT), operation],
        env=env, text=True, capture_output=True)


def settings(tmp_path):
    home = tmp_path / "preserved profile"
    home.mkdir()
    keys = tmp_path / "private keys.txt"
    keys.write_text("DO_NOT_PRINT_KEY_CONTENTS")
    return {"schema": 1, "home": str(home), "keysFile": str(keys)}


def test_plain_start_uses_private_profile_and_keys_without_reading_key_contents(tmp_path):
    config = settings(tmp_path)
    result = read_launch(tmp_path, config)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [config["home"], config["keysFile"], config["home"] + "/logs", ""]
    assert "DO_NOT_PRINT_KEY_CONTENTS" not in result.stdout + result.stderr


def test_explicit_environment_wins_and_missing_config_preserves_existing_behavior(tmp_path):
    chosen = {"MICROMIND_HOME": str(tmp_path / "explicit"), "MICROMIND_KEYS_FILE": str(tmp_path / "explicit-key")}
    for config in (None, settings(tmp_path)):
        result = read_launch(tmp_path, config, chosen)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines()[:2] == list(chosen.values())


def test_explicit_saves_layout_does_not_get_replaced_by_default_profile(tmp_path):
    result = read_launch(tmp_path, settings(tmp_path), {"MICROMIND_SAVES": str(tmp_path / "other-saves")})
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == ""
    assert result.stdout.splitlines()[-1] == str(tmp_path / "other-saves")


@pytest.mark.parametrize("config", [[], {"schema": 2}, {"schema": 1, "GEMINI_API_KEY": "PRIVATE"},
    {"schema": 1, "home": "relative"}, {"schema": 1, "home": "/not-a-real-micromind-profile"},
    {"schema": 1, "keysFile": "\nPRIVATE"}])
def test_invalid_settings_fail_before_launcher_continues_without_echoing_values(tmp_path, config):
    result = read_launch(tmp_path, config)
    assert result.returncode == 2
    assert not result.stdout
    assert "no services were stopped" in result.stderr
    assert "PRIVATE" not in result.stderr


def test_paths_are_data_not_shell_commands(tmp_path):
    config = settings(tmp_path)
    literal = tmp_path / "$(touch SHOULD_NOT_EXIST)`touch ALSO_NOT`=profile"
    literal.mkdir()
    config["home"] = str(literal)
    result = read_launch(tmp_path, config)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == str(literal)
    assert not (SCRIPT.parent.parent / "SHOULD_NOT_EXIST").exists()
    assert not (SCRIPT.parent.parent / "ALSO_NOT").exists()


def test_malformed_json_is_rejected_without_echoing_contents(tmp_path):
    (tmp_path / "launch.json").write_text("PRIVATE invalid json")
    result = read_launch(tmp_path)
    assert result.returncode == 2 and "PRIVATE" not in result.stderr


@pytest.mark.parametrize("operation", ["stop", "status"])
def test_recovery_commands_do_not_depend_on_valid_startup_settings(tmp_path, operation):
    (tmp_path / "launch.json").write_text("invalid json")
    result = read_launch(tmp_path, operation=operation)
    assert result.returncode == 0, result.stderr


def test_oversized_settings_are_bounded(tmp_path):
    (tmp_path / "launch.json").write_text(" " * (16 * 1024 + 1))
    result = read_launch(tmp_path)
    assert result.returncode == 2 and "exceed 16 KiB" in result.stderr
