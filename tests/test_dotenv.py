"""The git-ignored .env file feeds ${VAR} expansion in settings.yaml; real environment variables win."""
from erp_agents.core.config import load_config, load_dotenv


def test_dotenv_parsing_and_precedence(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nA_KEY=one\nexport B_KEY = "two words"\nC_KEY=\nD_KEY=from-file\nnot a line\n', encoding="utf-8")
    monkeypatch.setenv("D_KEY", "from-shell")
    for k in ("A_KEY", "B_KEY", "C_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert sorted(load_dotenv(env)) == ["A_KEY", "B_KEY"]
    import os
    assert os.environ["A_KEY"] == "one" and os.environ["B_KEY"] == "two words"
    assert "C_KEY" not in os.environ and os.environ["D_KEY"] == "from-shell"
    for k in ("A_KEY", "B_KEY"):
        monkeypatch.delenv(k)


def test_missing_dotenv_is_fine(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == []


def test_config_expands_values_from_the_project_dotenv(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text(
        "erp:\n  type: erpnext\n  api_key: ${DOTENV_TEST_KEY}\nauth:\n  tokens:\n"
        "    - { token: \"${DOTENV_TEST_TOKEN:-fallback}\", user: owner, roles: [admin] }\n", encoding="utf-8")
    (tmp_path / ".env").write_text("DOTENV_TEST_KEY=secret-key\nDOTENV_TEST_TOKEN=\n", encoding="utf-8")
    monkeypatch.delenv("DOTENV_TEST_KEY", raising=False)
    monkeypatch.delenv("DOTENV_TEST_TOKEN", raising=False)
    cfg = load_config(str(tmp_path / "config" / "settings.yaml"))
    assert cfg["erp"]["api_key"] == "secret-key"
    assert cfg["auth"]["tokens"][0]["token"] == "fallback"            # empty value in .env = not set
    monkeypatch.delenv("DOTENV_TEST_KEY", raising=False)
