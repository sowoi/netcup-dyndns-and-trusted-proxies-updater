import requests

from src.updateDynDns import (
    DEFAULT_IP_MODE,
    DEFAULT_PARALLEL_PROCESSES,
    check_endpoint_reachable,
    get_ip_mode,
    get_parallel_processes,
)


def test_get_parallel_processes_returns_configured_value():
    assert get_parallel_processes({"PARALLEL_PROCESSES": 4}) == 4


def test_get_parallel_processes_returns_default_when_missing():
    assert get_parallel_processes({}) == DEFAULT_PARALLEL_PROCESSES


def test_get_parallel_processes_returns_default_for_invalid_type():
    assert get_parallel_processes({"PARALLEL_PROCESSES": "not-a-number"}) == DEFAULT_PARALLEL_PROCESSES


def test_get_parallel_processes_returns_default_for_non_positive_value():
    assert get_parallel_processes({"PARALLEL_PROCESSES": 0}) == DEFAULT_PARALLEL_PROCESSES
    assert get_parallel_processes({"PARALLEL_PROCESSES": -1}) == DEFAULT_PARALLEL_PROCESSES


def test_get_ip_mode_returns_configured_value():
    assert get_ip_mode({"IP_MODE": "ipv4"}) == "ipv4"
    assert get_ip_mode({"IP_MODE": "IPv6"}) == "ipv6"


def test_get_ip_mode_returns_default_when_missing():
    assert get_ip_mode({}) == DEFAULT_IP_MODE


def test_get_ip_mode_returns_default_for_invalid_value():
    assert get_ip_mode({"IP_MODE": "bogus"}) == DEFAULT_IP_MODE


def test_check_endpoint_reachable_returns_true_on_success(mocker):
    mocker.patch("requests.head")
    assert check_endpoint_reachable("https://example.com") is True


def test_check_endpoint_reachable_returns_false_on_request_exception(mocker):
    mocker.patch(
        "requests.head", side_effect=requests.exceptions.RequestException("boom")
    )
    assert check_endpoint_reachable("https://example.com") is False
