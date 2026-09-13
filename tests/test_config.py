import os

from survival.config import load_dotenv


def test_dotenv_loads_without_overriding(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nFOO_TEST=bar\nQUOTED_TEST=\"x y\"\nEXISTING_TEST=from_file\n")
    monkeypatch.setenv("EXISTING_TEST", "from_shell")
    monkeypatch.delenv("FOO_TEST", raising=False)
    load_dotenv(str(env))
    assert os.environ["FOO_TEST"] == "bar"
    assert os.environ["QUOTED_TEST"] == "x y"
    assert os.environ["EXISTING_TEST"] == "from_shell"
