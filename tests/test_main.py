import json
import subprocess
from pathlib import Path

import pytest
import requests

from src.netcup_dyndns import MAX_SUBDOMAIN_RETRIES, NETCUP_API, main

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


def _response(mocker, payload):
    response = mocker.MagicMock()
    response.json.return_value = payload
    return response


def _common_mocks(
    mocker,
    cached_ipv4=None,
    cached_ipv6=None,
    failed_domains=None,
    settings=None,
    trusted_proxy=None,
):
    mocker.patch("src.updateDynDns.create_settings_file_if_not_exists")
    mocker.patch(
        "src.updateDynDns.read_cached_ips", return_value=(cached_ipv4, cached_ipv6)
    )
    write_cached_ips_mock = mocker.patch("src.updateDynDns.write_cached_ips")
    mocker.patch(
        "src.updateDynDns.read_failed_domains",
        return_value=dict(failed_domains) if failed_domains else {},
    )
    mocker.patch("src.updateDynDns.write_failed_domains")
    mocker.patch("src.updateDynDns.read_rate_limit_state", return_value={})
    mocker.patch("src.updateDynDns.write_rate_limit_state")
    mocker.patch("src.updateDynDns.read_trusted_proxy_ip", return_value=trusted_proxy)
    mocker.patch("src.updateDynDns.write_trusted_proxy_ip")
    mocker.patch(
        "builtins.open",
        mocker.mock_open(read_data=json.dumps(settings or MOCK_SETTINGS)),
    )
    return write_cached_ips_mock


def _login(mocker):
    return _response(
        mocker, {"status": "success", "responsedata": {"apisessionid": "session123"}}
    )


def _info(mocker, records):
    return _response(
        mocker, {"status": "success", "responsedata": {"dnsrecords": records}}
    )


def _ok(mocker):
    return _response(mocker, {"status": "success"})


def _zone_entries(process_mock):
    """Configured entries passed to process_zone, over all calls."""
    return sorted(
        entry for call in process_mock.call_args_list for entry, _ in call.args[1]
    )


def test_main_exits_early_when_ips_unchanged(mocker):
    """If cached IPs match current IPs, main should exit without updating anything."""
    write_cached_ips_mock = _common_mocks(
        mocker, cached_ipv4="1.2.3.4", cached_ipv6="::1"
    )
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    post_mock = mocker.patch("requests.post")
    nginx_mock = mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    write_cached_ips_mock.assert_not_called()
    post_mock.assert_not_called()
    nginx_mock.assert_not_called()


def test_main_force_updates_despite_unchanged_cached_ips(mocker):
    """--force should bypass the cached-IP check and update every domain."""
    write_cached_ips_mock = _common_mocks(
        mocker, cached_ipv4="1.2.3.4", cached_ipv6="::1"
    )
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    process_mock = mocker.patch("src.updateDynDns.process_zone", return_value={})

    assert main(["--force"]) == 0

    write_cached_ips_mock.assert_called_once_with(
        "1.2.3.4", "::1", cache_dir=Path(".temp").resolve()
    )
    process_mock.assert_called_once()
    assert process_mock.call_args.args[0] == "example.com"
    assert process_mock.call_args.args[1] == [("sub.example.com", "sub")]


def test_main_successful_dns_update(mocker):
    """Happy path: login, fetch records, one batched update for A + AAAA, logout."""
    write_cached_ips_mock = _common_mocks(mocker)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    nginx_mock = mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    progress_bar_mock = mocker.MagicMock()
    tqdm_mock = mocker.patch("src.updateDynDns.tqdm", return_value=progress_bar_mock)

    post_mock = mocker.patch(
        "requests.post",
        side_effect=[
            _login(mocker),
            _info(mocker, [
                {"id": "1", "hostname": "sub", "type": "A", "destination": "9.9.9.9"},
                {"id": "2", "hostname": "sub", "type": "AAAA", "destination": "::9"},
            ]),
            _ok(mocker),
            _ok(mocker),
        ],
    )

    assert main() == 0

    write_cached_ips_mock.assert_called_once_with(
        "1.2.3.4", "::1", cache_dir=Path(".temp").resolve()
    )
    nginx_mock.assert_called_once_with("/var/www/nextcloud", "0", "::1")
    actions = [call.kwargs["json"]["action"] for call in post_mock.call_args_list]
    assert actions == ["login", "infoDnsRecords", "updateDnsRecords", "logout"]
    update_records = post_mock.call_args_list[2].kwargs["json"]["param"]["dnsrecordset"]["dnsrecords"]
    assert update_records == [
        {"id": "1", "hostname": "sub", "type": "A", "destination": "1.2.3.4"},
        {"id": "2", "hostname": "sub", "type": "AAAA", "destination": "::1"},
    ]
    # Every request carries a timeout.
    assert all(call.kwargs["timeout"] for call in post_mock.call_args_list)

    tqdm_mock.assert_called_once()
    assert tqdm_mock.call_args.kwargs["total"] == 2
    progress_bar_mock.update.assert_called_once_with(2)
    progress_bar_mock.close.assert_called_once()


def test_main_dns_update_runs_before_trusted_proxy_update(mocker):
    """The DNS records are updated before OCC/nginx run."""
    _common_mocks(mocker)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    calls = []
    mocker.patch(
        "src.updateDynDns.process_zone",
        side_effect=lambda *args, **kwargs: calls.append("dns") or {},
    )
    mocker.patch(
        "src.updateDynDns.nginx_trusted_proxies_configuration",
        side_effect=lambda *args: calls.append("nginx"),
    )

    main()

    assert calls == ["dns", "nginx"]


def test_main_skips_update_of_records_already_up_to_date(mocker, caplog):
    """Records that already point at the current address are not sent to netcup."""
    _common_mocks(mocker)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    post_mock = mocker.patch(
        "requests.post",
        side_effect=[
            _login(mocker),
            _info(mocker, [
                {"id": "1", "hostname": "sub", "type": "A", "destination": "1.2.3.4"},
                {"id": "2", "hostname": "sub", "type": "AAAA", "destination": "::1"},
            ]),
            _ok(mocker),
        ],
    )

    with caplog.at_level("INFO"):
        assert main() == 0

    actions = [call.kwargs["json"]["action"] for call in post_mock.call_args_list]
    assert actions == ["login", "infoDnsRecords", "logout"]
    assert "1.2.3.4 (unchanged)" in caplog.text


def test_main_uses_one_session_per_zone(mocker):
    """Several entries of one zone share a single login and a single update."""
    settings = dict(MOCK_SETTINGS, NETCUP_DOMAIN="a.example.com, b.example.com, example.com")
    _common_mocks(mocker, settings=settings)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    records = [
        {"id": str(i), "hostname": host, "type": record_type}
        for i, (host, record_type) in enumerate(
            (host, record_type) for host in ("a", "b", "@") for record_type in ("A", "AAAA")
        )
    ]
    post_mock = mocker.patch(
        "requests.post",
        side_effect=[_login(mocker), _info(mocker, records), _ok(mocker), _ok(mocker)],
    )

    assert main() == 0

    actions = [call.kwargs["json"]["action"] for call in post_mock.call_args_list]
    assert actions == ["login", "infoDnsRecords", "updateDnsRecords", "logout"]
    sent = post_mock.call_args_list[2].kwargs["json"]["param"]["dnsrecordset"]["dnsrecords"]
    assert len(sent) == 6


def test_main_continues_when_login_fails(mocker):
    """A failed login is reported and makes main return 1, without SystemExit."""
    _common_mocks(mocker)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    post_mock = mocker.patch(
        "requests.post", return_value=_response(mocker, {"status": "failed"})
    )

    assert main() == 1

    # Login failure short-circuits before the finally/logout block.
    assert post_mock.call_count == 1


def test_main_continues_when_info_dns_records_fails(mocker):
    """If fetching the DNS records fails, main still logs out and returns 1."""
    _common_mocks(mocker)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    post_mock = mocker.patch(
        "requests.post",
        side_effect=[_login(mocker), _response(mocker, {"status": "failed"}), _ok(mocker)],
    )

    assert main() == 1
    assert post_mock.call_count == 3


def test_main_continues_when_update_fails(mocker):
    """A failed updateDnsRecords call still logs out and closes the progress bar."""
    _common_mocks(mocker)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    progress_bar_mock = mocker.MagicMock()
    mocker.patch("src.updateDynDns.tqdm", return_value=progress_bar_mock)
    write_failed_mock = mocker.patch("src.updateDynDns.write_failed_domains")
    post_mock = mocker.patch(
        "requests.post",
        side_effect=[
            _login(mocker),
            _info(mocker, [
                {"id": "1", "hostname": "sub", "type": "A"},
                {"id": "2", "hostname": "sub", "type": "AAAA"},
            ]),
            _response(mocker, {"status": "failed"}),
            _ok(mocker),
        ],
    )

    assert main() == 1

    assert post_mock.call_count == 4
    progress_bar_mock.close.assert_called_once()
    progress_bar_mock.update.assert_called_once_with(2)
    assert write_failed_mock.call_args.args[0] == {"sub.example.com": 1}


def test_main_continues_when_update_raises_http_error(mocker):
    """A request error during the update is reported as HTTP ERROR."""
    _common_mocks(mocker)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    mocker.patch(
        "requests.post",
        side_effect=[
            _login(mocker),
            _info(mocker, [{"id": "1", "hostname": "sub", "type": "A"}]),
            requests.exceptions.Timeout("timed out"),
            _ok(mocker),
        ],
    )

    assert main(["--ip-mode", "ipv4"]) == 1


def test_main_continues_when_logout_fails(mocker):
    """A failing logout does not stop the run."""
    _common_mocks(mocker)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    post_mock = mocker.patch(
        "requests.post",
        side_effect=[
            _login(mocker),
            _info(mocker, [
                {"id": "1", "hostname": "sub", "type": "A", "destination": "1.2.3.4"},
                {"id": "2", "hostname": "sub", "type": "AAAA", "destination": "::1"},
            ]),
            requests.exceptions.ConnectionError("logout failed"),
        ],
    )

    assert main() == 0
    assert post_mock.call_count == 3


def test_main_reports_missing_records(mocker):
    """Configured hosts without A/AAAA records are reported as errors."""
    _common_mocks(mocker)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    mocker.patch(
        "requests.post",
        side_effect=[_login(mocker), _info(mocker, []), _ok(mocker)],
    )

    assert main() == 1


def test_main_without_ipv6_skips_trusted_proxy_and_aaaa(mocker, caplog):
    """Without a public IPv6 address, OCC/nginx are not called with 'None' and
    AAAA records are skipped instead of failing."""
    write_cached_ips_mock = _common_mocks(mocker)
    ipv4_response = _response(mocker, {"ip": "1.2.3.4"})
    mocker.patch(
        "requests.get",
        side_effect=[
            ipv4_response,
            requests.exceptions.ConnectionError("no route"),
            requests.exceptions.ConnectionError("no route"),
        ],
    )
    nginx_mock = mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    mocker.patch(
        "requests.post",
        side_effect=[
            _login(mocker),
            _info(mocker, [{"id": "1", "hostname": "sub", "type": "A"}]),
            _ok(mocker),
            _ok(mocker),
        ],
    )

    with caplog.at_level("INFO"):
        assert main() == 0

    write_cached_ips_mock.assert_called_once_with(
        "1.2.3.4", None, cache_dir=Path(".temp").resolve()
    )
    nginx_mock.assert_not_called()
    assert "SKIPPED - no public address" in caplog.text


def test_main_falls_back_to_second_ip_provider(mocker):
    """If the first IPv4 provider fails, the next one is used."""
    _common_mocks(mocker, settings=dict(MOCK_SETTINGS, DISABLE_NEXTCLOUD_NGINX=True))
    fallback = mocker.MagicMock()
    fallback.json.side_effect = ValueError("not json")
    fallback.text = "5.6.7.8\n"
    get_mock = mocker.patch(
        "requests.get",
        side_effect=[requests.exceptions.Timeout("slow"), fallback],
    )
    process_mock = mocker.patch("src.updateDynDns.process_zone", return_value={})

    main(["--ip-mode", "ipv4"])

    assert get_mock.call_count == 2
    assert process_mock.call_args.args[3] == "5.6.7.8"
    assert process_mock.call_args.args[4] is None


def test_main_ipv6_mode_does_not_fetch_ipv4(mocker):
    _common_mocks(mocker, settings=dict(MOCK_SETTINGS, DISABLE_NEXTCLOUD_NGINX=True))
    get_mock = mocker.patch(
        "requests.get", return_value=_response(mocker, {"ip": "2001:db8::1"})
    )
    process_mock = mocker.patch("src.updateDynDns.process_zone", return_value={})

    main(["--ip-mode", "ipv6"])

    get_mock.assert_called_once()
    assert "api6" in get_mock.call_args.kwargs["url"]
    assert process_mock.call_args.args[3:] == (None, "2001:db8::1")


def test_main_ipv6_mode_ignores_changed_ipv4(mocker):
    """In ipv6 mode a changed (or unknown) IPv4 address does not trigger updates."""
    _common_mocks(
        mocker,
        cached_ipv4="9.9.9.9",
        cached_ipv6="2001:db8::1",
        settings=dict(MOCK_SETTINGS, DISABLE_NEXTCLOUD_NGINX=True),
    )
    mocker.patch("requests.get", return_value=_response(mocker, {"ip": "2001:db8::1"}))
    process_mock = mocker.patch("src.updateDynDns.process_zone")

    with pytest.raises(SystemExit) as exc_info:
        main(["--ip-mode", "ipv6"])

    assert exc_info.value.code == 0
    process_mock.assert_not_called()


def test_main_rejects_invalid_ip_from_provider(mocker):
    """A provider answer that is not an address of the expected family is ignored."""
    _common_mocks(mocker, settings=dict(MOCK_SETTINGS, DISABLE_NEXTCLOUD_NGINX=True))
    mocker.patch(
        "requests.get",
        side_effect=[
            _response(mocker, {"ip": "<html>error</html>"}),
            _response(mocker, {"ip": "::1"}),
        ],
    )
    process_mock = mocker.patch("src.updateDynDns.process_zone")

    assert main(["--ip-mode", "ipv4"]) == 1
    process_mock.assert_not_called()


def test_main_returns_1_without_any_public_ip(mocker):
    _common_mocks(mocker)
    mocker.patch("requests.get", side_effect=requests.exceptions.ConnectionError("offline"))
    process_mock = mocker.patch("src.updateDynDns.process_zone")
    write_cached_ips_mock = mocker.patch("src.updateDynDns.write_cached_ips")

    assert main() == 1
    process_mock.assert_not_called()
    write_cached_ips_mock.assert_not_called()


def test_main_updates_multiple_zones(mocker):
    """Entries of different zones each get their own session."""
    settings = dict(MOCK_SETTINGS, NETCUP_DOMAIN="sub.example.com, app.example.net")
    write_cached_ips_mock = _common_mocks(mocker, settings=settings)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    post_mock = mocker.patch(
        "requests.post",
        side_effect=[
            _login(mocker),
            _info(mocker, [{"id": "1", "hostname": "sub", "type": "A"}]),
            _ok(mocker),
            _ok(mocker),
            _login(mocker),
            _info(mocker, [{"id": "2", "hostname": "app", "type": "A"}]),
            _ok(mocker),
            _ok(mocker),
        ],
    )

    main(["--ip-mode", "ipv4"])

    write_cached_ips_mock.assert_called_once()
    assert post_mock.call_count == 8
    zones = [
        call.kwargs["json"]["param"]["domainname"]
        for call in post_mock.call_args_list
        if call.kwargs["json"]["action"] == "infoDnsRecords"
    ]
    assert zones == ["example.com", "example.net"]


def test_main_reports_invalid_domain_entries(mocker, caplog):
    _common_mocks(mocker, settings=dict(MOCK_SETTINGS, NETCUP_DOMAIN="localhost"))
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    process_mock = mocker.patch("src.updateDynDns.process_zone")
    write_failed_mock = mocker.patch("src.updateDynDns.write_failed_domains")

    with caplog.at_level("INFO"):
        assert main() == 1

    process_mock.assert_not_called()
    assert "INVALID FORMAT" in caplog.text
    assert write_failed_mock.call_args.args[0] == {"localhost": 1}


# --- settings loading -------------------------------------------------------


def _settings_file_mocks(mocker):
    mocker.patch("src.updateDynDns.create_settings_file_if_not_exists")
    get_mock = mocker.patch("requests.get")
    return get_mock


def test_main_exits_when_settings_file_missing(mocker, caplog):
    get_mock = _settings_file_mocks(mocker)
    mocker.patch("builtins.open", side_effect=FileNotFoundError)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert "not found" in caplog.text
    get_mock.assert_not_called()


def test_main_exits_when_settings_file_is_invalid_json(mocker, caplog):
    get_mock = _settings_file_mocks(mocker)
    mocker.patch("builtins.open", mocker.mock_open(read_data="{not json"))

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert "not a valid JSON document" in caplog.text
    get_mock.assert_not_called()


def test_main_exits_with_readable_message_for_empty_settings(mocker, caplog):
    """A freshly created settings file has empty values: no traceback, exit 1."""
    get_mock = _settings_file_mocks(mocker)
    empty = dict(MOCK_SETTINGS, API_PASSWORD="")
    mocker.patch("builtins.open", mocker.mock_open(read_data=json.dumps(empty)))

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert "API_PASSWORD cannot be empty" in caplog.text
    get_mock.assert_not_called()


def test_main_exits_when_settings_key_missing(mocker, caplog):
    _settings_file_mocks(mocker)
    incomplete = dict(MOCK_SETTINGS)
    del incomplete["NEXTCLOUD_PATH"]
    mocker.patch("builtins.open", mocker.mock_open(read_data=json.dumps(incomplete)))

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert "NEXTCLOUD_PATH is missing" in caplog.text


def test_main_loads_settings_and_secrets_only_once(mocker):
    _common_mocks(mocker)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    mocker.patch("src.updateDynDns.process_zone", return_value={})
    secrets_mock = mocker.patch(
        "src.updateDynDns.apply_secret_overrides", side_effect=lambda settings: settings
    )

    main()

    secrets_mock.assert_called_once()


# --- trusted proxies --------------------------------------------------------


def _trusted_proxy_mocks(mocker, **kwargs):
    write_cached_ips_mock = _common_mocks(mocker, **kwargs)
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    nginx_mock = mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    process_mock = mocker.patch("src.updateDynDns.process_zone", return_value={})
    return write_cached_ips_mock, nginx_mock, process_mock


def test_main_trusted_proxy_failure_keeps_dns_update_and_retries(mocker, caplog):
    """A failing OCC call no longer aborts the run before the DNS update, and
    the trusted proxy is not marked as applied, so the next run retries it."""
    write_cached_ips_mock, nginx_mock, process_mock = _trusted_proxy_mocks(mocker)
    nginx_mock.side_effect = subprocess.CalledProcessError(1, "occ")
    write_trusted_mock = mocker.patch("src.updateDynDns.write_trusted_proxy_ip")

    with caplog.at_level("INFO"):
        assert main() == 1

    process_mock.assert_called_once()
    write_cached_ips_mock.assert_called_once()
    write_trusted_mock.assert_not_called()
    assert "Retrying on the next run" in caplog.text


def test_main_retries_pending_trusted_proxy_without_ip_change(mocker):
    """IPs unchanged but the trusted proxy was not applied yet: only OCC/nginx run."""
    _, nginx_mock, process_mock = _trusted_proxy_mocks(
        mocker, cached_ipv4="1.2.3.4", cached_ipv6="::1", trusted_proxy="::9"
    )
    write_trusted_mock = mocker.patch("src.updateDynDns.write_trusted_proxy_ip")

    assert main() == 0

    process_mock.assert_not_called()
    nginx_mock.assert_called_once_with("/var/www/nextcloud", "0", "::1")
    write_trusted_mock.assert_called_once_with("::1", cache_dir=Path(".temp").resolve())


def test_main_retry_run_does_not_reload_nginx(mocker):
    """Retrying failed domains with unchanged IPs does not touch OCC/nginx."""
    _, nginx_mock, process_mock = _trusted_proxy_mocks(
        mocker,
        cached_ipv4="1.2.3.4",
        cached_ipv6="::1",
        trusted_proxy="::1",
        failed_domains={"sub.example.com": 1},
    )

    main()

    process_mock.assert_called_once()
    nginx_mock.assert_not_called()


def test_main_without_trusted_proxy_cache_uses_cached_ipv6(mocker):
    """After upgrading, the cached IPv6 counts as the applied trusted proxy."""
    _, nginx_mock, _ = _trusted_proxy_mocks(
        mocker, cached_ipv4="1.2.3.4", cached_ipv6="::1", trusted_proxy=None
    )

    with pytest.raises(SystemExit):
        main()

    nginx_mock.assert_not_called()


# --- --force ----------------------------------------------------------------


def _force_mocks(mocker, failed_domains=None):
    return _trusted_proxy_mocks(
        mocker,
        cached_ipv4="1.2.3.4",
        cached_ipv6="::1",
        trusted_proxy="::1",
        failed_domains=failed_domains,
    )


def test_main_without_force_does_not_update_when_ips_unchanged(mocker):
    """Sanity check: the same setup without --force exits early."""
    write_cached_ips_mock, nginx_mock, process_mock = _force_mocks(mocker)

    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 0
    write_cached_ips_mock.assert_not_called()
    nginx_mock.assert_not_called()
    process_mock.assert_not_called()


def test_main_force_updates_all_configured_domains(mocker):
    """--force updates every configured domain, not only pending retries."""
    _, _, process_mock = _force_mocks(mocker, failed_domains={"a.example.com": 1})

    main(["--force", "--netcup-domain", "a.example.com, b.example.com"])

    assert _zone_entries(process_mock) == ["a.example.com", "b.example.com"]


def test_main_force_retries_domains_that_exhausted_their_retries(mocker):
    """--force also updates domains that have exhausted their retry budget."""
    _, _, process_mock = _force_mocks(
        mocker, failed_domains={"sub.example.com": MAX_SUBDOMAIN_RETRIES}
    )
    write_failed_domains_mock = mocker.patch("src.updateDynDns.write_failed_domains")

    main(["--force"])

    assert _zone_entries(process_mock) == ["sub.example.com"]
    # The domain succeeded, so its failure counter is cleared.
    assert write_failed_domains_mock.call_args.args[0] == {}


def test_main_force_runs_nextcloud_nginx_configuration(mocker):
    """--force runs the trusted-proxies configuration with the current IPv6."""
    _, nginx_mock, _ = _force_mocks(mocker)

    main(["--force"])

    nginx_mock.assert_called_once_with("/var/www/nextcloud", "0", "::1")


def test_main_force_respects_disable_nextcloud_nginx(mocker):
    """--force does not re-enable disabled Nextcloud/Nginx tasks."""
    _, nginx_mock, process_mock = _force_mocks(mocker)

    main(["--force", "--disable-nextcloud-nginx"])

    nginx_mock.assert_not_called()
    process_mock.assert_called_once()


def test_main_force_logs_reason_when_ips_unchanged(mocker, caplog):
    """--force logs that it is updating despite unchanged cached IPs."""
    _force_mocks(mocker)

    with caplog.at_level("INFO"):
        main(["--force"])

    assert "--force given" in caplog.text
    assert "No update necessary" not in caplog.text


def test_main_force_with_changed_ips_does_not_log_force_message(mocker, caplog):
    """When IPs really changed, --force behaves like a normal update."""
    write_cached_ips_mock, _, process_mock = _trusted_proxy_mocks(
        mocker, cached_ipv4="9.9.9.9", cached_ipv6="::9"
    )

    with caplog.at_level("INFO"):
        main(["--force"])

    assert "--force given" not in caplog.text
    write_cached_ips_mock.assert_called_once()
    process_mock.assert_called_once()


def test_main_returns_1_when_exhausted_domains_are_reported(mocker):
    """Domains that exhausted their retries are listed and make the run fail."""
    _, _, process_mock = _force_mocks(
        mocker, failed_domains={"sub.example.com": MAX_SUBDOMAIN_RETRIES}
    )
    process_mock.return_value = {
        "sub.example.com": [
            {"domain": "example.com", "subdomain": "sub", "record_type": "A",
             "destination": "RECORD NOT FOUND", "error": True},
        ]
    }

    assert main(["--force"]) == 1


def test_netcup_api_is_the_only_post_target_of_a_run(mocker):
    """Guard: the zone update only talks to the netcup endpoint."""
    _common_mocks(mocker, settings=dict(MOCK_SETTINGS, DISABLE_NEXTCLOUD_NGINX=True))
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    post_mock = mocker.patch(
        "requests.post",
        side_effect=[_login(mocker), _info(mocker, []), _ok(mocker)],
    )

    main()

    assert {call.kwargs["url"] for call in post_mock.call_args_list} == {NETCUP_API}


def test_main_exits_when_settings_path_is_a_directory(mocker, caplog):
    """Docker creates a directory when the mounted settings file is missing."""
    _settings_file_mocks(mocker)
    mocker.patch("builtins.open", side_effect=IsADirectoryError(21, "Is a directory"))

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert "Could not read settings file" in caplog.text
