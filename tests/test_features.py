import json
import time

import pytest
import requests

from src.netcup_dyndns import (
    MAX_SUBDOMAIN_RETRIES,
    check_health,
    get_bool_setting,
    main,
    parse_cli_args,
    process_zone,
    read_last_success,
    write_last_success,
)

SETTINGS = {
    "API_PASSWORD": "password",
    "API_KEY": "api_key",
    "CUSTOMER_ID": "123",
    "NETCUP_DOMAIN": "sub.example.com",
    "NEXTCLOUD_PATH": "/var/www/nextcloud",
    "TRUSTED_PROXIES_POS": "0",
}
ENTRIES = [("sub.example.com", "sub")]


def _response(mocker, payload):
    response = mocker.MagicMock()
    response.json.return_value = payload
    return response


def _netcup(mocker, records, update_status="success"):
    """Fake netcup endpoint; returns the list of sent requests."""
    sent = []

    def fake_post(url, json=None, **kwargs):
        sent.append(json)
        payloads = {
            "login": {"status": "success", "responsedata": {"apisessionid": "s"}},
            "infoDnsRecords": {"status": "success", "responsedata": {"dnsrecords": records}},
            "updateDnsRecords": {"status": update_status},
        }
        return _response(mocker, payloads.get(json["action"], {"status": "success"}))

    mocker.patch("requests.post", side_effect=fake_post)
    return sent


def _ip_responses(mocker, ipv4="1.2.3.4", ipv6="::1"):
    mocker.patch(
        "requests.get",
        side_effect=lambda url, **kwargs: _response(
            mocker, {"ip": ipv6 if "6" in url.split("//")[1].split(".")[0] else ipv4}
        ),
    )


def _write_settings(tmp_path, **overrides):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(dict(SETTINGS, **overrides)), encoding="utf-8")
    return ["--settings-file", str(settings_file), "--cache-dir", str(tmp_path / "cache")]


def _actions(sent):
    return [request["action"] for request in sent]


OUTDATED = [
    {"id": "1", "hostname": "sub", "type": "A", "destination": "9.9.9.9"},
    {"id": "2", "hostname": "sub", "type": "AAAA", "destination": "::9"},
]


# --- get_bool_setting -------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), ("true", True), ("True", True), (" yes ", True),
     ("1", True), ("on", True), ("false", False), ("0", False), ("", False), (None, False)],
)
def test_get_bool_setting(value, expected):
    assert get_bool_setting({"KEY": value}, "KEY") is expected


def test_get_bool_setting_default():
    assert get_bool_setting({}, "KEY") is False
    assert get_bool_setting({}, "KEY", default=True) is True


def test_disable_nextcloud_nginx_from_string_false_keeps_nextcloud_enabled(tmp_path, mocker):
    """A secret file with "false" no longer disables the Nextcloud integration."""
    argv = _write_settings(tmp_path, DISABLE_NEXTCLOUD_NGINX="false")
    _ip_responses(mocker)
    _netcup(mocker, OUTDATED)
    nginx_mock = mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")

    assert main(argv) == 0
    nginx_mock.assert_called_once()


# --- --dry-run --------------------------------------------------------------------


def test_dry_run_reads_records_but_changes_nothing(tmp_path, mocker, caplog):
    argv = _write_settings(tmp_path)
    _ip_responses(mocker)
    sent = _netcup(mocker, OUTDATED)
    nginx_mock = mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    ntfy_mock = mocker.patch("src.updateDynDns.send_ntfy_notification")

    with caplog.at_level("INFO"):
        assert main([*argv, "--dry-run"]) == 0

    assert _actions(sent) == ["login", "infoDnsRecords", "logout"]
    nginx_mock.assert_not_called()
    ntfy_mock.assert_not_called()
    assert not (tmp_path / "cache").exists()
    assert "1.2.3.4 (would update)" in caplog.text
    assert "::1 (would update)" in caplog.text
    assert "would set trusted proxy 0 to ::1" in caplog.text
    assert "Dry run finished" in caplog.text


def test_dry_run_does_not_create_settings_file(tmp_path, mocker):
    mocker.patch("requests.get")
    settings_file = tmp_path / "missing.json"

    with pytest.raises(SystemExit) as exc_info:
        main(["--dry-run", "--settings-file", str(settings_file)])

    assert exc_info.value.code == 1
    assert not settings_file.exists()


def test_dry_run_on_refused_login_keeps_backoff_state(tmp_path, mocker, caplog):
    argv = _write_settings(tmp_path, DISABLE_NEXTCLOUD_NGINX=True, NTFY_TOPIC="t")
    _ip_responses(mocker)
    mocker.patch(
        "requests.post",
        return_value=_response(mocker, {"status": "error", "longmessage": "Too many logins"}),
    )
    ntfy_mock = mocker.patch("src.updateDynDns.send_ntfy_notification")

    with caplog.at_level("INFO"):
        assert main([*argv, "--dry-run"]) == 1

    assert "CONNECTION REFUSED - retry after" in caplog.text
    assert not (tmp_path / "cache").exists()
    ntfy_mock.assert_not_called()


def test_dry_run_passed_to_process_zone(tmp_path, mocker):
    argv = _write_settings(tmp_path, DISABLE_NEXTCLOUD_NGINX=True)
    _ip_responses(mocker)
    process_mock = mocker.patch("src.updateDynDns.process_zone", return_value={})

    main([*argv, "--dry-run"])

    assert process_mock.call_args.kwargs["dry_run"] is True


# --- CREATE_MISSING_RECORDS -----------------------------------------------------


def test_missing_records_are_reported_by_default(mocker):
    sent = _netcup(mocker, [])
    results = process_zone("example.com", ENTRIES, SETTINGS, "1.2.3.4", "::1")

    assert "updateDnsRecords" not in _actions(sent)
    assert [r["destination"] for r in results["sub.example.com"]] == [
        "RECORD NOT FOUND",
        "RECORD NOT FOUND",
    ]


def test_missing_records_are_created_when_enabled(mocker):
    sent = _netcup(mocker, [{"id": "1", "hostname": "sub", "type": "A", "destination": "1.2.3.4"}])
    settings = dict(SETTINGS, CREATE_MISSING_RECORDS=True)

    results = process_zone("example.com", ENTRIES, settings, "1.2.3.4", "::1")

    update = next(r for r in sent if r["action"] == "updateDnsRecords")
    assert update["param"]["dnsrecordset"]["dnsrecords"] == [
        {"hostname": "sub", "type": "AAAA", "destination": "::1"}
    ]
    destinations = [r["destination"] for r in results["sub.example.com"]]
    assert destinations == ["1.2.3.4 (unchanged)", "::1 (created)"]
    assert not any(r["error"] for r in results["sub.example.com"])


def test_created_records_fail_with_the_update(mocker):
    _netcup(mocker, [], update_status="error")
    settings = dict(SETTINGS, CREATE_MISSING_RECORDS="true")

    results = process_zone("example.com", ENTRIES, settings, "1.2.3.4", None)

    assert results["sub.example.com"][0]["destination"] == "UPDATE FAILED"
    assert results["sub.example.com"][0]["error"] is True


def test_dry_run_shows_records_that_would_be_created(mocker):
    sent = _netcup(mocker, [])
    settings = dict(SETTINGS, CREATE_MISSING_RECORDS=True, IP_MODE="ipv4")

    results = process_zone("example.com", ENTRIES, settings, "1.2.3.4", None, dry_run=True)

    assert "updateDnsRecords" not in _actions(sent)
    assert results["sub.example.com"][0]["destination"] == "1.2.3.4 (would create)"


def test_create_missing_records_cli_flag():
    assert parse_cli_args(["--create-missing-records"]).create_missing_records is True
    assert parse_cli_args(["--no-create-missing-records"]).create_missing_records is False
    assert parse_cli_args([]).create_missing_records is None


# --- -v / -q --------------------------------------------------------------------


def test_verbose_and_quiet_are_mutually_exclusive(capsys):
    with pytest.raises(SystemExit):
        parse_cli_args(["-v", "-q"])


def test_verbose_logs_debug_messages(tmp_path, mocker, caplog):
    argv = _write_settings(tmp_path, DISABLE_NEXTCLOUD_NGINX=True)
    _ip_responses(mocker)
    _netcup(mocker, OUTDATED)

    with caplog.at_level("DEBUG"):
        main([*argv, "-v"])

    assert "DNS records of example.com: sub A 9.9.9.9" in caplog.text
    assert "Sending 2 changed DNS record(s)" in caplog.text


def test_quiet_hides_info_but_shows_failed_summary(tmp_path, mocker, caplog):
    argv = _write_settings(tmp_path, DISABLE_NEXTCLOUD_NGINX=True)
    _ip_responses(mocker)
    _netcup(mocker, OUTDATED, update_status="error")

    with caplog.at_level("DEBUG"):
        assert main([*argv, "-q"]) == 1

    assert "Current public IPv4 address" not in caplog.text
    assert "UPDATE FAILED" in caplog.text
    assert all(record.levelname in ("WARNING", "ERROR") for record in caplog.records)


def test_quiet_successful_run_logs_nothing(tmp_path, mocker, caplog):
    argv = _write_settings(tmp_path, DISABLE_NEXTCLOUD_NGINX=True)
    _ip_responses(mocker)
    _netcup(mocker, OUTDATED)

    with caplog.at_level("DEBUG"):
        assert main([*argv, "--quiet"]) == 0

    assert caplog.records == []


# --- ntfy notifications ---------------------------------------------------------


def test_ip_change_notification(tmp_path, mocker):
    argv = _write_settings(
        tmp_path, DISABLE_NEXTCLOUD_NGINX=True, NTFY_TOPIC="t", NTFY_NOTIFY_IP_CHANGE=True
    )
    _ip_responses(mocker)
    _netcup(mocker, OUTDATED)
    ntfy_mock = mocker.patch("src.updateDynDns.send_ntfy_notification", return_value=True)

    assert main(argv) == 0

    ntfy_mock.assert_called_once()
    title, message = ntfy_mock.call_args.args[1:3]
    assert title == "netcup DynDNS: IP address changed"
    assert "IPv4: none -> 1.2.3.4" in message
    assert "IPv6: none -> ::1" in message
    assert "The DNS records were updated." in message


def test_ip_change_notification_reports_failures(tmp_path, mocker):
    argv = _write_settings(
        tmp_path, DISABLE_NEXTCLOUD_NGINX=True, NTFY_TOPIC="t", NTFY_NOTIFY_IP_CHANGE=True
    )
    _ip_responses(mocker)
    _netcup(mocker, OUTDATED, update_status="error")
    ntfy_mock = mocker.patch("src.updateDynDns.send_ntfy_notification", return_value=True)

    main(argv)

    assert "could not be updated" in ntfy_mock.call_args.args[2]


def test_no_ip_change_notification_by_default_or_for_force(tmp_path, mocker):
    argv = _write_settings(tmp_path, DISABLE_NEXTCLOUD_NGINX=True, NTFY_TOPIC="t")
    _ip_responses(mocker)
    _netcup(mocker, OUTDATED)
    ntfy_mock = mocker.patch("src.updateDynDns.send_ntfy_notification", return_value=True)

    main(argv)
    ntfy_mock.assert_not_called()

    # Same IPs again with --force and notifications enabled: no IP change.
    _ip_responses(mocker)
    main([*argv, "--force", "--ntfy-notify-ip-change"])
    ntfy_mock.assert_not_called()


def test_exhausted_domain_notification_is_sent_once(tmp_path, mocker):
    argv = _write_settings(
        tmp_path, DISABLE_NEXTCLOUD_NGINX=True, NTFY_TOPIC="t", NTFY_NOTIFY_FAILURES=True
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "failed_domains.json").write_text(
        json.dumps({"sub.example.com": MAX_SUBDOMAIN_RETRIES - 1}), encoding="utf-8"
    )
    (cache / "ipv4_cache.txt").write_text("1.2.3.4", encoding="utf-8")
    (cache / "ipv6_cache.txt").write_text("::1", encoding="utf-8")
    _ip_responses(mocker)
    _netcup(mocker, [])
    ntfy_mock = mocker.patch("src.updateDynDns.send_ntfy_notification", return_value=True)

    assert main(argv) == 1

    ntfy_mock.assert_called_once()
    assert ntfy_mock.call_args.args[1] == "netcup DynDNS: DNS update failing"
    assert "sub.example.com" in ntfy_mock.call_args.args[2]

    # Already exhausted: --force retries it, but no second notification.
    _ip_responses(mocker)
    main([*argv, "--force"])
    ntfy_mock.assert_called_once()


def test_no_failure_notification_by_default(tmp_path, mocker):
    argv = _write_settings(tmp_path, DISABLE_NEXTCLOUD_NGINX=True, NTFY_TOPIC="t")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "failed_domains.json").write_text(
        json.dumps({"sub.example.com": MAX_SUBDOMAIN_RETRIES - 1}), encoding="utf-8"
    )
    _ip_responses(mocker)
    _netcup(mocker, [])
    ntfy_mock = mocker.patch("src.updateDynDns.send_ntfy_notification", return_value=True)

    main(argv)

    ntfy_mock.assert_not_called()


# --- health check -----------------------------------------------------------------


def test_last_success_roundtrip(tmp_path):
    assert read_last_success(cache_dir=tmp_path) is None
    write_last_success(1234.5, cache_dir=tmp_path / "cache")
    assert read_last_success(cache_dir=tmp_path / "cache") == 1234.5


def test_last_success_invalid_file(tmp_path):
    (tmp_path / "last_success.txt").write_text("garbage", encoding="utf-8")
    assert read_last_success(cache_dir=tmp_path) is None


def test_check_health(tmp_path, capsys):
    assert check_health(900, cache_dir=tmp_path, now=1000) == 1
    assert "no successful run" in capsys.readouterr().out

    write_last_success(1000, cache_dir=tmp_path)
    assert check_health(900, cache_dir=tmp_path, now=1500) == 0
    assert "healthy" in capsys.readouterr().out
    assert check_health(900, cache_dir=tmp_path, now=2000) == 1
    assert "unhealthy" in capsys.readouterr().out


def test_successful_run_records_last_success(tmp_path, mocker):
    argv = _write_settings(tmp_path, DISABLE_NEXTCLOUD_NGINX=True)
    _ip_responses(mocker)
    _netcup(mocker, OUTDATED)

    before = time.time()
    assert main(argv) == 0

    assert read_last_success(cache_dir=tmp_path / "cache") >= before
    assert main([*argv, "--health-check"]) == 0
    assert main([*argv, "--health-check", "0"]) == 1


def test_unchanged_run_records_last_success(tmp_path, mocker):
    argv = _write_settings(tmp_path, DISABLE_NEXTCLOUD_NGINX=True)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "ipv4_cache.txt").write_text("1.2.3.4", encoding="utf-8")
    (cache / "ipv6_cache.txt").write_text("::1", encoding="utf-8")
    _ip_responses(mocker)

    with pytest.raises(SystemExit):
        main(argv)

    assert read_last_success(cache_dir=cache) is not None


def test_failed_run_does_not_record_last_success(tmp_path, mocker):
    argv = _write_settings(tmp_path, DISABLE_NEXTCLOUD_NGINX=True)
    _ip_responses(mocker)
    _netcup(mocker, OUTDATED, update_status="error")

    assert main(argv) == 1
    assert read_last_success(cache_dir=tmp_path / "cache") is None


def test_health_check_does_not_need_settings_or_network(tmp_path, mocker):
    get_mock = mocker.patch("requests.get")
    post_mock = mocker.patch("requests.post")

    assert main(["--cache-dir", str(tmp_path), "--health-check"]) == 1

    get_mock.assert_not_called()
    post_mock.assert_not_called()
    assert not (tmp_path / ".settings.json").exists()


def test_backoff_skip_does_not_record_last_success(tmp_path, mocker):
    argv = _write_settings(tmp_path, DISABLE_NEXTCLOUD_NGINX=True)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "rate_limit.json").write_text(
        json.dumps({"failures": 1, "next_attempt": time.time() + 600}), encoding="utf-8"
    )
    mocker.patch("requests.get", side_effect=requests.exceptions.ConnectionError)

    with pytest.raises(SystemExit):
        main(argv)

    assert read_last_success(cache_dir=cache) is None


def _ipv4_only(mocker):
    def fake_get(url, **kwargs):
        if "6" in url.split("//")[1].split(".")[0]:
            raise requests.exceptions.ConnectionError("Network is unreachable")
        return _response(mocker, {"ip": "1.2.3.4"})

    mocker.patch("requests.get", side_effect=fake_get)


def test_quiet_run_on_ipv4_only_host_is_silent(tmp_path, mocker, caplog):
    """Docker's default network has no IPv6: -q must not warn on every run."""
    argv = _write_settings(tmp_path, DISABLE_NEXTCLOUD_NGINX=True)
    _ipv4_only(mocker)
    _netcup(mocker, [{"id": "1", "hostname": "sub", "type": "A", "destination": "9.9.9.9"}])

    with caplog.at_level("DEBUG"):
        assert main([*argv, "-q"]) == 0

    assert caplog.records == []


def test_missing_ipv6_warns_once_when_trusted_proxy_needs_it(tmp_path, mocker, caplog):
    argv = _write_settings(tmp_path)
    _ipv4_only(mocker)
    _netcup(mocker, [{"id": "1", "hostname": "sub", "type": "A", "destination": "9.9.9.9"}])
    nginx_mock = mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")

    with caplog.at_level("DEBUG"):
        assert main([*argv, "-q"]) == 0

    assert [record.levelname for record in caplog.records] == ["WARNING"]
    assert "trusted proxies are skipped" in caplog.text
    nginx_mock.assert_not_called()
