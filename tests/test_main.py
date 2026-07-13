import json

import pytest

from src.updateDynDns import main


MOCK_SETTINGS = {
    "API_PASSWORD": "password",
    "API_KEY": "api_key",
    "CUSTOMER_ID": "123",
    "NETCUP_DOMAIN": "sub.example.com",
    "NEXTCLOUD_PATH": "/var/www/nextcloud",
    "TRUSTED_PROXIES_POS": "0",
}


def _mock_ip_get_responses(mocker, ipv4="1.2.3.4", ipv6="::1"):
    ipv4_response = mocker.MagicMock()
    ipv4_response.json.return_value = {"ip": ipv4}
    ipv6_response = mocker.MagicMock()
    ipv6_response.json.return_value = {"ip": ipv6}
    return [ipv4_response, ipv6_response]


def _common_mocks(mocker, cached_ipv4=None, cached_ipv6=None):
    mocker.patch("src.updateDynDns.create_settings_file_if_not_exists")
    mocker.patch(
        "src.updateDynDns.read_cached_ips", return_value=(cached_ipv4, cached_ipv6)
    )
    write_cached_ips_mock = mocker.patch("src.updateDynDns.write_cached_ips")
    mocker.patch(
        "builtins.open", mocker.mock_open(read_data=json.dumps(MOCK_SETTINGS))
    )
    return write_cached_ips_mock


def test_main_exits_early_when_ips_unchanged(mocker):
    """If cached IPs match current IPs, main should exit without updating anything."""
    write_cached_ips_mock = _common_mocks(
        mocker, cached_ipv4="1.2.3.4", cached_ipv6="::1"
    )
    mocker.patch(
        "requests.get", side_effect=_mock_ip_get_responses(mocker)
    )
    post_mock = mocker.patch("requests.post")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    write_cached_ips_mock.assert_not_called()
    post_mock.assert_not_called()


def test_main_successful_dns_update(mocker):
    """Full happy-path run: login, fetch records, update A + AAAA records, logout."""
    write_cached_ips_mock = _common_mocks(mocker, cached_ipv4=None, cached_ipv6=None)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    nginx_mock = mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    progress_bar_mock = mocker.MagicMock()
    tqdm_mock = mocker.patch(
        "src.updateDynDns.tqdm", return_value=progress_bar_mock
    )

    login_response = mocker.MagicMock()
    login_response.json.return_value = {
        "status": "success",
        "responsedata": {"apisessionid": "session123"},
    }
    info_response = mocker.MagicMock()
    info_response.json.return_value = {
        "status": "success",
        "responsedata": {
            "dnsrecords": [
                {"id": "1", "hostname": "sub", "type": "A"},
                {"id": "2", "hostname": "sub", "type": "AAAA"},
            ]
        },
    }
    update_a_response = mocker.MagicMock()
    update_a_response.json.return_value = {"status": "success"}
    update_aaaa_response = mocker.MagicMock()
    update_aaaa_response.json.return_value = {"status": "success"}
    logout_response = mocker.MagicMock()
    logout_response.json.return_value = {"status": "success"}

    post_mock = mocker.patch(
        "requests.post",
        side_effect=[
            login_response,
            info_response,
            update_a_response,
            update_aaaa_response,
            logout_response,
        ],
    )

    main()

    write_cached_ips_mock.assert_called_once_with("1.2.3.4", "::1")
    nginx_mock.assert_called_once_with("/var/www/nextcloud", "0", "::1")
    assert post_mock.call_count == 5

    # Progress bar is sized from the number of subdomains in NETCUP_DOMAIN (1
    # subdomain * 2 record types), advances once per successful record update,
    # and is closed once all records have been updated.
    tqdm_mock.assert_called_once()
    assert tqdm_mock.call_args.kwargs["total"] == 2
    assert progress_bar_mock.update.call_count == 2
    progress_bar_mock.update.assert_called_with(1)
    progress_bar_mock.close.assert_called_once()


def test_main_exits_when_login_fails(mocker):
    """If the Netcup API login fails, main should exit(1) without further requests."""
    _common_mocks(mocker, cached_ipv4=None, cached_ipv6=None)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")

    login_response = mocker.MagicMock()
    login_response.json.return_value = {"status": "failed"}
    post_mock = mocker.patch("requests.post", return_value=login_response)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert post_mock.call_count == 1


def test_main_exits_when_info_dns_records_fails(mocker):
    """If fetching the DNS records fails, main should exit(1)."""
    _common_mocks(mocker, cached_ipv4=None, cached_ipv6=None)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")

    login_response = mocker.MagicMock()
    login_response.json.return_value = {
        "status": "success",
        "responsedata": {"apisessionid": "session123"},
    }
    info_response = mocker.MagicMock()
    info_response.json.return_value = {"status": "failed"}
    post_mock = mocker.patch(
        "requests.post", side_effect=[login_response, info_response]
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert post_mock.call_count == 2


def test_main_exits_when_update_a_record_fails(mocker):
    """If updating the A record fails, main should exit(1) and still close the progress bar."""
    _common_mocks(mocker, cached_ipv4=None, cached_ipv6=None)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    progress_bar_mock = mocker.MagicMock()
    mocker.patch("src.updateDynDns.tqdm", return_value=progress_bar_mock)

    login_response = mocker.MagicMock()
    login_response.json.return_value = {
        "status": "success",
        "responsedata": {"apisessionid": "session123"},
    }
    info_response = mocker.MagicMock()
    info_response.json.return_value = {
        "status": "success",
        "responsedata": {
            "dnsrecords": [{"id": "1", "hostname": "sub", "type": "A"}]
        },
    }
    update_a_response = mocker.MagicMock()
    update_a_response.json.return_value = {"status": "failed"}
    post_mock = mocker.patch(
        "requests.post",
        side_effect=[login_response, info_response, update_a_response],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert post_mock.call_count == 3
    # The progress bar must be closed even when a record update fails part-way.
    progress_bar_mock.close.assert_called_once()
    progress_bar_mock.update.assert_not_called()


def test_main_exits_when_logout_fails(mocker):
    """If logging out from the Netcup API fails, main should exit(1)."""
    _common_mocks(mocker, cached_ipv4=None, cached_ipv6=None)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")

    login_response = mocker.MagicMock()
    login_response.json.return_value = {
        "status": "success",
        "responsedata": {"apisessionid": "session123"},
    }
    info_response = mocker.MagicMock()
    info_response.json.return_value = {
        "status": "success",
        "responsedata": {"dnsrecords": []},
    }
    logout_response = mocker.MagicMock()
    logout_response.json.return_value = {"status": "failed"}
    post_mock = mocker.patch(
        "requests.post",
        side_effect=[login_response, info_response, logout_response],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert post_mock.call_count == 3


def test_main_exits_when_update_aaaa_record_fails(mocker):
    """If updating the AAAA record fails, main should exit(1)."""
    _common_mocks(mocker, cached_ipv4=None, cached_ipv6=None)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")

    login_response = mocker.MagicMock()
    login_response.json.return_value = {
        "status": "success",
        "responsedata": {"apisessionid": "session123"},
    }
    info_response = mocker.MagicMock()
    info_response.json.return_value = {
        "status": "success",
        "responsedata": {
            "dnsrecords": [{"id": "2", "hostname": "sub", "type": "AAAA"}]
        },
    }
    update_aaaa_response = mocker.MagicMock()
    update_aaaa_response.json.return_value = {"status": "failed"}
    post_mock = mocker.patch(
        "requests.post",
        side_effect=[login_response, info_response, update_aaaa_response],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert post_mock.call_count == 3


def test_main_continues_without_ipv6_on_request_exception(mocker):
    """If fetching the public IPv6 address fails, IPv6 should fall back to None."""
    import requests

    write_cached_ips_mock = _common_mocks(mocker, cached_ipv4=None, cached_ipv6=None)
    ipv4_response = mocker.MagicMock()
    ipv4_response.json.return_value = {"ip": "1.2.3.4"}
    mocker.patch(
        "requests.get",
        side_effect=[ipv4_response, requests.exceptions.RequestException("boom")],
    )
    nginx_mock = mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")

    login_response = mocker.MagicMock()
    login_response.json.return_value = {
        "status": "success",
        "responsedata": {"apisessionid": "session123"},
    }
    info_response = mocker.MagicMock()
    info_response.json.return_value = {
        "status": "success",
        "responsedata": {"dnsrecords": []},
    }
    logout_response = mocker.MagicMock()
    logout_response.json.return_value = {"status": "success"}

    mocker.patch(
        "requests.post",
        side_effect=[login_response, info_response, logout_response],
    )

    main()

    write_cached_ips_mock.assert_called_once_with("1.2.3.4", None)
    nginx_mock.assert_called_once_with("/var/www/nextcloud", "0", None)


def test_main_updates_multiple_domains(mocker):
    """Multiple configured domains should each be logged in, updated, and summarized."""
    settings = dict(MOCK_SETTINGS)
    settings["NETCUP_DOMAIN"] = "sub.example.com, app.example.net"
    mocker.patch("src.updateDynDns.create_settings_file_if_not_exists")
    mocker.patch(
        "src.updateDynDns.read_cached_ips", return_value=(None, None)
    )
    write_cached_ips_mock = mocker.patch("src.updateDynDns.write_cached_ips")
    mocker.patch(
        "builtins.open", mocker.mock_open(read_data=json.dumps(settings))
    )
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")

    login_response = mocker.MagicMock()
    login_response.json.return_value = {
        "status": "success",
        "responsedata": {"apisessionid": "session123"},
    }
    info_response_example_com = mocker.MagicMock()
    info_response_example_com.json.return_value = {
        "status": "success",
        "responsedata": {
            "dnsrecords": [{"id": "1", "hostname": "sub", "type": "A"}]
        },
    }
    info_response_example_net = mocker.MagicMock()
    info_response_example_net.json.return_value = {
        "status": "success",
        "responsedata": {
            "dnsrecords": [{"id": "2", "hostname": "app", "type": "A"}]
        },
    }
    update_response = mocker.MagicMock()
    update_response.json.return_value = {"status": "success"}
    logout_response = mocker.MagicMock()
    logout_response.json.return_value = {"status": "success"}

    post_mock = mocker.patch(
        "requests.post",
        side_effect=[
            login_response,
            info_response_example_com,
            update_response,
            login_response,
            info_response_example_net,
            update_response,
            logout_response,
        ],
    )

    main()

    write_cached_ips_mock.assert_called_once_with("1.2.3.4", "::1")
    assert post_mock.call_count == 7


def test_main_exits_when_settings_file_missing(mocker):
    """A missing .settings.json during the post-cache-write reload should exit(1)."""
    mocker.patch("src.updateDynDns.create_settings_file_if_not_exists")
    mocker.patch("src.updateDynDns.read_cached_ips", return_value=(None, None))
    mocker.patch("src.updateDynDns.write_cached_ips")
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))

    mock_open = mocker.mock_open(read_data=json.dumps(MOCK_SETTINGS))
    mock_open.side_effect = [
        mock_open.return_value,
        FileNotFoundError,
    ]
    mocker.patch("builtins.open", mock_open)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1


def test_main_exits_when_settings_key_missing_on_reload(mocker):
    """A key missing only on the second settings read should exit(1)."""
    incomplete_settings = dict(MOCK_SETTINGS)
    del incomplete_settings["NEXTCLOUD_PATH"]

    mocker.patch("src.updateDynDns.create_settings_file_if_not_exists")
    mocker.patch("src.updateDynDns.read_cached_ips", return_value=(None, None))
    mocker.patch("src.updateDynDns.write_cached_ips")
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))

    call_count = {"n": 0}
    real_mock_open_first = mocker.mock_open(read_data=json.dumps(MOCK_SETTINGS))
    real_mock_open_second = mocker.mock_open(read_data=json.dumps(incomplete_settings))

    def open_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_mock_open_first.return_value
        return real_mock_open_second.return_value

    mocker.patch("builtins.open", side_effect=open_side_effect)
    mocker.patch(
        "src.updateDynDns.validate_settings",
        side_effect=[None, KeyError("NEXTCLOUD_PATH")],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
