"""Loading configuration from a .env file.

The precedence rule is the part worth pinning down: a real environment variable
must always beat the file, because that is what lets CI and production override
a checkout's .env without editing anything on disk.
"""

import os

from noteacademy_pipeline.config import Settings, _load_dotenv


def write_env(directory, body: str) -> None:
    (directory / ".env").write_text(body)


def test_reads_values_from_a_dotenv_file(tmp_path, monkeypatch):
    write_env(tmp_path, "DATABASE_URL=postgresql://example/db\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    _load_dotenv()
    assert Settings.from_env().database_url == "postgresql://example/db"


def test_a_real_environment_variable_wins(tmp_path, monkeypatch):
    write_env(tmp_path, "DATABASE_URL=postgresql://from-file/db\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://from-env/db")

    _load_dotenv()
    assert os.environ["DATABASE_URL"] == "postgresql://from-env/db"


def test_finds_the_file_from_a_subdirectory(tmp_path, monkeypatch):
    # The CLI is run from anywhere in the checkout; the .env lives at its root.
    write_env(tmp_path, "VOYAGE_API_KEY=abc123\n")
    nested = tmp_path / "pipeline" / "work"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    _load_dotenv()
    assert Settings.from_env().voyage_api_key == "abc123"


def test_ignores_comments_and_blank_lines(tmp_path, monkeypatch):
    write_env(tmp_path, "\n# a comment\n\nDATABASE_URL=postgresql://example/db\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    _load_dotenv()
    assert Settings.from_env().database_url == "postgresql://example/db"


def test_strips_quotes_around_values(tmp_path, monkeypatch):
    write_env(tmp_path, 'GOOGLE_API_KEY="sk-quoted"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    _load_dotenv()
    assert Settings.from_env().google_api_key == "sk-quoted"


def test_missing_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _load_dotenv()  # must not raise
