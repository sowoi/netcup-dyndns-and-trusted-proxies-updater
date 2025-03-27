import os
import pytest
from src.updateDynDns import create_settings_file_if_not_exists


@pytest.fixture
def create_settings_file():
    """Fixture for settings file"""
    mock_settings_file_path = ".settings.json"
    mock_default_setting = {
        "API_PASSWORD": "",
        "API_KEY": "",
        "CUSTOMER_ID": "",
        "NETCUP_DOMAIN": "",
        "NEXTCLOUD_PATH": "",
        "TRUSTED_PROXIES_POS": "",
    }

    # run process with mocking data"
    settings_file = create_settings_file_if_not_exists(
        mock_settings_file_path, mock_default_setting
    )

    # wait until process is finished
    yield settings_file

    # remove settings file
    if os.path.exists(mock_settings_file_path):
        os.remove(mock_settings_file_path)


def test_create_settings_file_if_not_exists(create_settings_file):
    """Check if file was created"""
    assert os.path.exists(".settings.json"), "File was not created."
