import os

import pytest
import requests
from unittest.mock import mock_open


from src.updateDynDns import write_cached_ips, read_cached_ips


@pytest.fixture
def delete_ip_cache():
    yield
    if os.path.exists(".temp"):
        os.remove(".temp/ipv4_cache.txt")
        os.remove(".temp/ipv6_cache.txt")
        os.rmdir(".temp/")


def tests_write_cached_ips(delete_ip_cache, mocker):
    IPV4_API = "https://api.ipify.org?format=json"
    IPV6_API = "https://api6.ipify.org?format=json"
    TEST_IPV4 = "1.2.3.4"
    TEST_IPV6 = "9999:9999:9999:9999:9999:9999:9999:9999"

    mock_response_ipv4 = mocker.MagicMock()
    mock_response_ipv4.json.return_value = {"ip": f"{TEST_IPV4}"}
    mock_response_ipv6 = mocker.MagicMock()
    mock_response_ipv6.json.return_value = {"ip": f"{TEST_IPV6}"}
    mocker.patch("requests.get", return_value=mock_response_ipv4)
    IPv4 = requests.get(url=IPV4_API).json()["ip"]
    mocker.patch("requests.get", return_value=mock_response_ipv6)
    IPv6 = requests.get(url=IPV6_API).json()["ip"]

    write_cached_ips(IPv4, IPv6)

    with open(".temp/ipv4_cache.txt", "r") as ipv4_file:
        actual_content_ipv4_file = ipv4_file.read()

    with open(".temp/ipv6_cache.txt", "r") as ipv6_file:
        actual_content_ipv6_file = ipv6_file.read()

    # Assertions
    assert os.path.exists(".temp/ipv4_cache.txt"), "ipv4 File was not created."
    assert os.path.exists(".temp/ipv6_cache.txt"), "ipv6 File was not created."
    assert actual_content_ipv4_file == f"{TEST_IPV4}", "IPv4 is not correct."
    assert actual_content_ipv6_file == f"{TEST_IPV6}", "IPv6 is not correct."


def test_read_cached_ips(mocker):
    TEST_IPV4 = "1.2.3.4"
    TEST_IPV6 = "9999:9999:9999:9999:9999:9999:9999:9999"
    mocker_open = mock_open(read_data=TEST_IPV4)
    mocker.patch("builtins.open", mocker_open)

    ipv4, _ = read_cached_ips()

    assert ipv4 == TEST_IPV4

    mocker_open = mock_open(read_data=TEST_IPV6)
    mocker.patch("builtins.open", mocker_open)

    _, ipv6 = read_cached_ips()
    assert ipv6 == TEST_IPV6

    mocker.patch("builtins.open", side_effect=FileNotFoundError)
    ipv4, ipv6 = read_cached_ips()

    assert ipv4 is None
    assert ipv6 is None
