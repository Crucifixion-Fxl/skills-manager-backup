import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "ninedata_openapi.py"
SPEC = importlib.util.spec_from_file_location("ninedata_openapi", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_resolve_config_path_prefers_explicit_path(monkeypatch, tmp_path):
    env_path = tmp_path / "env.json"
    explicit_path = tmp_path / "explicit.json"
    monkeypatch.setenv("NINEDATA_SKILL_CONFIG", str(env_path))

    assert MODULE.resolve_config_path(str(explicit_path)) == explicit_path


def test_resolve_config_path_prefers_environment_override(monkeypatch, tmp_path):
    env_path = tmp_path / "env.json"
    monkeypatch.setenv("NINEDATA_SKILL_CONFIG", str(env_path))

    assert MODULE.resolve_config_path(None) == env_path


def test_resolve_config_path_uses_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.delenv("NINEDATA_SKILL_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert (
        MODULE.resolve_config_path(None)
        == tmp_path / "addx" / "ninedata" / "config.json"
    )


def test_resolve_config_path_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("NINEDATA_SKILL_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert (
        MODULE.resolve_config_path(None)
        == tmp_path / ".config" / "addx" / "ninedata" / "config.json"
    )


def test_load_config_overlays_credentials_from_environment(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "endpoint": "https://ninedata.example.com",
                "accessKeyId": "legacy-id",
                "accessKeySecret": "legacy-secret",
            }
        ),
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    monkeypatch.setenv("NINEDATA_API_KEY", "environment-id")
    monkeypatch.setenv("NINEDATA_SECRET_KEY", "environment-secret")

    config, resolved_path = MODULE.load_config(str(config_path))

    assert resolved_path == str(config_path)
    assert config["accessKeyId"] == "environment-id"
    assert config["accessKeySecret"] == "environment-secret"


def test_example_config_is_valid_with_environment_credentials(monkeypatch):
    example_path = Path(__file__).parents[1] / "config.example.json"
    monkeypatch.setenv("NINEDATA_API_KEY", "environment-id")
    monkeypatch.setenv("NINEDATA_SECRET_KEY", "environment-secret")

    config, _ = MODULE.load_config(str(example_path))

    assert MODULE.get_runtime_config(config)["accessKeyId"] == "environment-id"
    assert MODULE.get_runtime_config(config)["accessKeySecret"] == "environment-secret"


def test_example_config_contains_only_placeholder_credentials():
    example_path = Path(__file__).parents[1] / "config.example.json"

    config = json.loads(example_path.read_text(encoding="utf-8"))

    assert config["accessKeyId"] == "replace-with-access-key-id"
    assert config["accessKeySecret"] == "replace-with-access-key-secret"
