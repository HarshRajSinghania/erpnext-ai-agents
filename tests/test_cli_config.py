"""The shipped example config must work, and the CLI must drive the same machinery as the API."""
import filecmp
import os
from pathlib import Path

import pytest

from erp_agents.bootstrap import build_app
from erp_agents.cli import main
from erp_agents.config import expand_env, load_config

ROOT = Path(__file__).resolve().parent.parent


def test_example_config_copies_are_identical():
    assert filecmp.cmp(ROOT / "config" / "settings.example.yaml", ROOT / "src" / "erp_agents" / "settings.example.yaml", shallow=False)


def test_example_config_loads_and_runs(tmp_path, monkeypatch):
    cfg = load_config(str(ROOT / "config" / "settings.example.yaml"))
    cfg["database"]["path"] = str(tmp_path / "x.db")
    app = build_app(config=cfg)
    app.channels["console"].writer = lambda s: None
    r = app.inventory.run(dry_run=True)
    assert r.checked == 10 and not r.errors
    assert app.settings.system().check_cron == "0 6 * * *"


def test_env_expansion_and_defaults(monkeypatch):
    monkeypatch.setenv("A_TOKEN", "abc")
    monkeypatch.delenv("MISSING", raising=False)
    out = expand_env({"x": "${A_TOKEN}", "y": "${MISSING:-fallback}", "z": ["${MISSING}", 5]})
    assert out == {"x": "abc", "y": "fallback", "z": ["", 5]}


def test_unset_token_variable_is_skipped_not_crashing(tmp_path, monkeypatch):
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    cfg = load_config(str(ROOT / "config" / "settings.example.yaml"))
    cfg["database"]["path"] = str(tmp_path / "x.db")
    cfg["auth"]["tokens"] = [{"token": "${NOPE_TOKEN}", "user": "ghost", "roles": ["admin"]}]
    cfg = __import__("erp_agents.config", fromlist=["expand_env"]).expand_env(cfg)
    assert build_app(config=cfg).tokens == {}


@pytest.fixture
def cfg_file(tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text(f"database: {{ path: {tmp_path / 'cli.db'} }}\nerp: {{ type: mock }}\n", encoding="utf-8")
    return str(p)


def test_cli_policy_roundtrip_and_validation(cfg_file, capsys):
    assert main(["--config", cfg_file, "policy", "set", "--scope", "item", "--key", "BOLT-M8", "min_qty=500", "min_days_cover=null"]) == 0
    assert main(["--config", cfg_file, "policy", "show", "--scope", "item", "--key", "BOLT-M8"]) == 0
    out = capsys.readouterr().out
    assert "min_qty" in out and "item 'BOLT-M8' override" in out
    assert main(["--config", cfg_file, "policy", "set", "--scope", "global", "min_qty=-5"]) == 1
    assert "greater than or equal to 0" in capsys.readouterr().out
    assert main(["--config", cfg_file, "policy", "audit", "--scope", "item", "--key", "BOLT-M8"]) == 0
    assert "min_qty" in capsys.readouterr().out


def test_cli_setting_changes_what_run_once_reports(cfg_file, capsys):
    main(["--config", cfg_file, "run-once", "--dry-run", "--item", "STEEL-2MM"])
    assert "new reorder requests 0" in capsys.readouterr().out
    main(["--config", cfg_file, "policy", "set", "--scope", "item", "--key", "STEEL-2MM", "min_days_cover=90"])
    main(["--config", cfg_file, "run-once", "--dry-run", "--item", "STEEL-2MM"])
    assert "new reorder requests 1" in capsys.readouterr().out


def test_cli_alert_then_decide_from_the_terminal(cfg_file, capsys):
    main(["--config", cfg_file, "run-once", "--item", "BOLT-M8"])
    capsys.readouterr()
    main(["--config", cfg_file, "approvals", "list"])
    line = capsys.readouterr().out.strip().splitlines()[-1]
    approval_id = line.split()[0]
    assert main(["--config", cfg_file, "approvals", "decide", approval_id, "approve"]) == 0
    assert main(["--config", cfg_file, "approvals", "decide", approval_id, "approve"]) == 1     # second time refused
    assert "Already answered" in capsys.readouterr().out


def test_cli_init_writes_config_without_overwriting(tmp_path, capsys):
    assert main(["init", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / "config" / "settings.yaml").exists()
    assert main(["init", "--dir", str(tmp_path)]) == 1
    assert main(["init", "--dir", str(tmp_path), "--force"]) == 0


def test_demo_runs(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "RELAY-12V" in out and "CRITICAL" in out and "2 early warnings" in out


def test_policy_set_with_options_before_the_values_parses_on_every_python_version():
    from erp_agents.cli import build_parser, parse_cli
    a = parse_cli(build_parser(), ["policy", "set", "--scope", "item", "--key", "BOLT-M8", "min_qty=5", "min_days_cover=null"])
    assert a.action == "set" and a.scope == "item" and a.key == "BOLT-M8" and a.values == ["min_qty=5", "min_days_cover=null"]
    b = parse_cli(build_parser(), ["policy", "set", "min_qty=5", "--scope", "global"])
    assert b.values == ["min_qty=5"] and b.scope == "global"


def test_unknown_arguments_are_still_refused(capsys):
    import pytest
    from erp_agents.cli import build_parser, parse_cli
    for argv in (["policy", "set", "--nonsense", "x"], ["analyze", "BOLT-M8", "extra"], ["policy", "show", "--scope", "item", "--bogus"]):
        with pytest.raises(SystemExit) as exc:
            parse_cli(build_parser(), argv)
        assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
