import pytest

from src.netcup_dyndns import logger


@pytest.fixture(autouse=True)
def isolated_working_directory(tmp_path, monkeypatch):
    """Run every test in its own working directory, so runs with the default
    paths never write to the repository's .settings.json or .temp."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def reset_log_level():
    """main() sets the updater's log level from -v/-q; don't leak it into other tests."""
    yield
    logger.setLevel("NOTSET")
