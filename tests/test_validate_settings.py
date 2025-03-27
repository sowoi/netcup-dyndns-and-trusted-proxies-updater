import json
from unittest import mock

import pytest

from src.updateDynDns import validate_settings


def test_validate_settings(mocker):
    mock_settings = {
        "API_PASSWORD": "password",
        "API_KEY": "api_key",
        "CUSTOMER_ID": "123",
        "NETCUP_DOMAIN": "mynetcupdomain",
        "NEXTCLOUD_PATH": "/var/www/nextcloud",
        "TRUSTED_PROXIES_POS": "3",
    }

    mock_open = mock.mock_open(read_data=json.dumps(mock_settings))

    with mock.patch("builtins.open", mock_open):
        with mock.patch("json.load", return_value=mock_settings):
            with open("fake_path") as fp:
                settings = json.load(fp)
                validate_settings(settings)


def test_validate_valueerror_settings(mocker):
    mock_settings = {
        "API_PASSWORD": "password",
        "API_KEY": "",
        "CUSTOMER_ID": "123",
        "NETCUP_DOMAIN": "mynetcupdomain",
        "NEXTCLOUD_PATH": "/var/www/nextcloud",
        "TRUSTED_PROXIES_POS": "3",
    }

    mock_open = mock.mock_open(read_data=json.dumps(mock_settings))
    with pytest.raises(ValueError):
        with mock.patch("builtins.open", mock_open):
            with mock.patch("json.load", return_value=mock_settings):
                with open("fake_path") as fp:
                    settings = json.load(fp)
                    validate_settings(settings)


def test_validate_keyerror_settings(mocker):
    mock_settings = {"NEXTCLOUD_PATH": "/var/www/nextcloud", "TRUSTED_PROXIES_POS": "3"}

    mock_open = mock.mock_open(read_data=json.dumps(mock_settings))
    with pytest.raises(KeyError):
        with mock.patch("builtins.open", mock_open):
            with mock.patch("json.load", return_value=mock_settings):
                with open("fake_path") as fp:
                    settings = json.load(fp)
                    validate_settings(settings)
