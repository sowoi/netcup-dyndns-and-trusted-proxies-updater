import pytest

from src.updateDynDns import (
    apply_file_secret_overrides,
    apply_secret_overrides,
    default_settings,
    fetch_openbao_secrets,
)


# --- fetch_openbao_secrets -------------------------------------------------


def test_fetch_openbao_secrets_returns_empty_dict_when_addr_not_configured():
    assert fetch_openbao_secrets(env={}) == {}


def test_fetch_openbao_secrets_returns_empty_dict_when_no_token_provided(mocker):
    get_mock = mocker.patch("requests.get")

    result = fetch_openbao_secrets(env={"OPENBAO_ADDR": "https://openbao.example.com"})

    assert result == {}
    get_mock.assert_not_called()


def test_fetch_openbao_secrets_fetches_and_filters_known_keys(mocker):
    response = mocker.MagicMock()
    response.json.return_value = {
        "data": {
            "data": {
                "API_PASSWORD": "secret-password",
                "API_KEY": "secret-key",
                "UNKNOWN_KEY": "should-be-ignored",
            }
        }
    }
    get_mock = mocker.patch("requests.get", return_value=response)

    result = fetch_openbao_secrets(
        env={
            "OPENBAO_ADDR": "https://openbao.example.com:8200/",
            "OPENBAO_TOKEN": "s.mytoken",
            "OPENBAO_SECRET_PATH": "secret/data/netcup-dyndns",
        }
    )

    assert result == {"API_PASSWORD": "secret-password", "API_KEY": "secret-key"}
    get_mock.assert_called_once_with(
        "https://openbao.example.com:8200/v1/secret/data/netcup-dyndns",
        headers={"X-Vault-Token": "s.mytoken"},
        timeout=5,
    )


def test_fetch_openbao_secrets_reads_token_from_file(mocker, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("s.filetoken\n")

    response = mocker.MagicMock()
    response.json.return_value = {"data": {"data": {"API_KEY": "from-file-token"}}}
    get_mock = mocker.patch("requests.get", return_value=response)

    result = fetch_openbao_secrets(
        env={
            "OPENBAO_ADDR": "https://openbao.example.com",
            "OPENBAO_TOKEN_FILE": str(token_file),
        }
    )

    assert result == {"API_KEY": "from-file-token"}
    assert get_mock.call_args.kwargs["headers"] == {"X-Vault-Token": "s.filetoken"}


def test_fetch_openbao_secrets_returns_empty_dict_when_token_file_missing():
    result = fetch_openbao_secrets(
        env={
            "OPENBAO_ADDR": "https://openbao.example.com",
            "OPENBAO_TOKEN_FILE": "/nonexistent/path/to/token",
        }
    )
    assert result == {}


def test_fetch_openbao_secrets_returns_empty_dict_on_request_exception(mocker):
    import requests

    mocker.patch(
        "requests.get", side_effect=requests.exceptions.RequestException("boom")
    )

    result = fetch_openbao_secrets(
        env={"OPENBAO_ADDR": "https://openbao.example.com", "OPENBAO_TOKEN": "t"}
    )

    assert result == {}


def test_fetch_openbao_secrets_returns_empty_dict_on_invalid_json(mocker):
    response = mocker.MagicMock()
    response.json.side_effect = ValueError("not json")
    mocker.patch("requests.get", return_value=response)

    result = fetch_openbao_secrets(
        env={"OPENBAO_ADDR": "https://openbao.example.com", "OPENBAO_TOKEN": "t"}
    )

    assert result == {}


def test_fetch_openbao_secrets_uses_default_secret_path(mocker):
    response = mocker.MagicMock()
    response.json.return_value = {"data": {"data": {}}}
    get_mock = mocker.patch("requests.get", return_value=response)

    fetch_openbao_secrets(
        env={"OPENBAO_ADDR": "https://openbao.example.com", "OPENBAO_TOKEN": "t"}
    )

    assert get_mock.call_args.args[0] == "https://openbao.example.com/v1/secret/data/netcup-dyndns"


# --- apply_file_secret_overrides -------------------------------------------


def test_apply_file_secret_overrides_reads_secret_files(tmp_path):
    password_file = tmp_path / "password"
    password_file.write_text("file-password\n")

    settings = dict.fromkeys(default_settings, "")
    result = apply_file_secret_overrides(
        settings, env={"API_PASSWORD_FILE": str(password_file)}
    )

    assert result["API_PASSWORD"] == "file-password"
    assert result is settings


def test_apply_file_secret_overrides_ignores_keys_without_file_env():
    settings = {"API_PASSWORD": "original"}
    result = apply_file_secret_overrides(settings, env={})
    assert result["API_PASSWORD"] == "original"


def test_apply_file_secret_overrides_skips_missing_file(mocker):
    settings = {"API_PASSWORD": "original"}
    apply_file_secret_overrides(
        settings, env={"API_PASSWORD_FILE": "/nonexistent/secret/file"}
    )
    assert settings["API_PASSWORD"] == "original"


# --- apply_secret_overrides (combined precedence) --------------------------


def test_apply_secret_overrides_file_takes_precedence_over_openbao(mocker, tmp_path):
    password_file = tmp_path / "password"
    password_file.write_text("file-password")

    response = mocker.MagicMock()
    response.json.return_value = {"data": {"data": {"API_PASSWORD": "openbao-password"}}}
    mocker.patch("requests.get", return_value=response)

    settings = {"API_PASSWORD": "original"}
    result = apply_secret_overrides(
        settings,
        env={
            "OPENBAO_ADDR": "https://openbao.example.com",
            "OPENBAO_TOKEN": "t",
            "API_PASSWORD_FILE": str(password_file),
        },
    )

    assert result["API_PASSWORD"] == "file-password"


def test_apply_secret_overrides_uses_openbao_when_no_file_override(mocker):
    response = mocker.MagicMock()
    response.json.return_value = {"data": {"data": {"API_KEY": "openbao-key"}}}
    mocker.patch("requests.get", return_value=response)

    settings = {"API_KEY": "original"}
    result = apply_secret_overrides(
        settings,
        env={"OPENBAO_ADDR": "https://openbao.example.com", "OPENBAO_TOKEN": "t"},
    )

    assert result["API_KEY"] == "openbao-key"


def test_apply_secret_overrides_is_noop_without_env_configuration():
    settings = {"API_KEY": "original"}
    result = apply_secret_overrides(settings, env={})
    assert result == {"API_KEY": "original"}
