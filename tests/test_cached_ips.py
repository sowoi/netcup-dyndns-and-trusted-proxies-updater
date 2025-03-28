from pathlib import Path


import pytest
import requests

from src.updateDynDns import write_cached_ips, read_cached_ips


@pytest.fixture
def delete_ip_cache():
    temp_test_directory_path = Path(__file__).parent / ".temp/"
    yield
    if temp_test_directory_path.exists():
        ipv4_cache_path = temp_test_directory_path / "ipv4_cache.txt"
        ipv6_cache_path = temp_test_directory_path / "ipv6_cache.txt"
        ipv4_cache_path.unlink()
        ipv6_cache_path.unlink()
        temp_test_directory_path.rmdir()


def tests_write_cached_ips(delete_ip_cache, mocker):
    IPV4_API = "https://api.ipify.org?format=json"
    IPV6_API = "https://api6.ipify.org?format=json"
    TEST_IPV4 = "1.2.3.4"
    TEST_IPV6 = "9999:9999:9999:9999:9999:9999:9999:9999"
    temp_test_directory_path = Path(__file__).parent / ".temp/"
    temp_test_directory_path.mkdir(parents=True, exist_ok=True)

    mock_response_ipv4 = mocker.MagicMock()
    mock_response_ipv4.json.return_value = {"ip": f"{TEST_IPV4}"}
    mock_response_ipv6 = mocker.MagicMock()
    mock_response_ipv6.json.return_value = {"ip": f"{TEST_IPV6}"}
    mocker.patch("requests.get", return_value=mock_response_ipv4)
    IPv4 = requests.get(url=IPV4_API).json()["ip"]
    mocker.patch("requests.get", return_value=mock_response_ipv6)
    IPv6 = requests.get(url=IPV6_API).json()["ip"]

    write_cached_ips(IPv4, IPv6, cache_dir=temp_test_directory_path)

    ipv4_path = temp_test_directory_path / "ipv4_cache.txt"
    ipv6_path = temp_test_directory_path / "ipv6_cache.txt"

    # Assertions
    assert ipv4_path.exists(), "ipv4 File was not created."
    assert ipv6_path.exists(), "ipv6 File was not created."
    assert ipv4_path.read_text() == f"{TEST_IPV4}", "IPv4 is not correct."
    assert ipv6_path.read_text() == f"{TEST_IPV6}", "IPv6 is not correct."


def test_read_cached_ips(mocker):
    TEST_IPV4 = "1.2.3.4"
    TEST_IPV6 = "9999:9999:9999:9999:9999:9999:9999:9999"
    temp_test_directory_path = Path(__file__).parent / ".temp/"
    mocker.patch.object(Path, "read_text", side_effect=[TEST_IPV4, TEST_IPV6])

    ipv4, _ = read_cached_ips(cache_dir=temp_test_directory_path)

    assert ipv4 == TEST_IPV4

    mocker.patch.object(Path, "read_text", side_effect=[TEST_IPV4, TEST_IPV6])

    _, ipv6 = read_cached_ips(cache_dir=temp_test_directory_path)
    assert ipv6 == TEST_IPV6

    mocker.patch.object(Path, "read_text", side_effect=FileNotFoundError)
    ipv4, ipv6 = read_cached_ips(cache_dir=temp_test_directory_path)

    assert ipv4 is None
    assert ipv6 is None
