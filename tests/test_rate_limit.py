import json
import time
from types import SimpleNamespace

import pytest
import requests

from src.netcup_dyndns import (
    NETCUP_API,
    NetcupLoginRateLimitError,
    get_rate_limit_backoff_minutes,
    handle_rate_limit_hit,
    is_connection_refused,
    is_login_rate_limited,
    main,
    process_subdomain,
    read_rate_limit_state,
    register_rate_limit_hit,
    send_ntfy_notification,
    write_rate_limit_state,
)

SETTINGS = {
    "API_PASSWORD": "password",
    "API_KEY": "api_key",
    "CUSTOMER_ID": "123",
    "NETCUP_DOMAIN": "sub.example.com",
    "DISABLE_NEXTCLOUD_NGINX": True,
    "NTFY_TOPIC": "dyndns-alerts",
}


def _connection_refused_error():
    """Build a requests.ConnectionError chained like the one requests raises
    when the TCP connection is refused."""
    try:
        try:
            raise ConnectionRefusedError(61, "Connection refused")
        except ConnectionRefusedError as refused:
            raise requests.exceptions.ConnectionError("Max retries exceeded") from refused
    except requests.exceptions.ConnectionError as error:
        return error


def _response(mocker, payload=None, status_code=200):
    response = mocker.MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


# --- get_rate_limit_backoff_minutes ----------------------------------------


def test_backoff_minutes_default_when_missing():
    assert get_rate_limit_backoff_minutes({}) == [10, 30, 60]


def test_backoff_minutes_from_list():
    assert get_rate_limit_backoff_minutes({"RATE_LIMIT_BACKOFF_MINUTES": [5, 15]}) == [5, 15]


def test_backoff_minutes_from_comma_separated_string():
    settings = {"RATE_LIMIT_BACKOFF_MINUTES": " 1, 2.5 ,3 "}
    assert get_rate_limit_backoff_minutes(settings) == [1, 2.5, 3]


@pytest.mark.parametrize("value", [[], "", [10, 0], [-5], ["abc"], 10, None])
def test_backoff_minutes_default_for_invalid_value(value):
    settings = {"RATE_LIMIT_BACKOFF_MINUTES": value}
    assert get_rate_limit_backoff_minutes(settings) == [10, 30, 60]


# --- rate-limit detection -------------------------------------------------


def test_is_connection_refused_detects_chained_connection_refused_error():
    assert is_connection_refused(_connection_refused_error()) is True


def test_is_connection_refused_detects_message():
    error = requests.exceptions.ConnectionError("[Errno 111] Connection refused")
    assert is_connection_refused(error) is True


def test_is_connection_refused_false_for_other_errors():
    assert is_connection_refused(requests.exceptions.Timeout("read timed out")) is False


@pytest.mark.parametrize(
    "response",
    [
        {"status": "error", "longmessage": "Too many login attempts."},
        {"status": "error", "shortmessage": "API rate limit reached"},
        {"status": "error", "longmessage": "Connection Refused"},
    ],
)
def test_is_login_rate_limited_detects_limit_messages(response):
    assert is_login_rate_limited(response) is True


@pytest.mark.parametrize(
    "response",
    [
        {"status": "success", "longmessage": "too many"},
        {"status": "error", "longmessage": "The login was not successful."},
        {"status": "error"},
        None,
    ],
)
def test_is_login_rate_limited_false_otherwise(response):
    assert is_login_rate_limited(response) is False


# --- rate-limit state cache -----------------------------------------------


def test_read_rate_limit_state_empty_when_missing(tmp_path):
    assert read_rate_limit_state(cache_dir=tmp_path) == {}


def test_write_and_read_rate_limit_state_roundtrip(tmp_path):
    state = {"failures": 2, "next_attempt": 1234.5, "notified": False}
    write_rate_limit_state(state, cache_dir=tmp_path / "cache")
    assert read_rate_limit_state(cache_dir=tmp_path / "cache") == state


def test_write_empty_rate_limit_state_removes_file(tmp_path):
    write_rate_limit_state({"failures": 1, "next_attempt": 1.0, "notified": False}, cache_dir=tmp_path)
    write_rate_limit_state({}, cache_dir=tmp_path)
    assert not (tmp_path / "rate_limit.json").exists()


def test_write_empty_rate_limit_state_without_file(tmp_path):
    write_rate_limit_state({}, cache_dir=tmp_path / "missing")
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("content", ["not json", "[]", '{"failures": 1}', '{"failures": "x", "next_attempt": 1}'])
def test_read_rate_limit_state_empty_for_invalid_file(tmp_path, content):
    (tmp_path / "rate_limit.json").write_text(content)
    assert read_rate_limit_state(cache_dir=tmp_path) == {}


# --- backoff schedule -----------------------------------------------------


def test_register_rate_limit_hit_follows_backoff_steps_and_repeats_last():
    state = {}
    delays = []
    for _ in range(5):
        state, delay = register_rate_limit_hit(state, [10, 30, 60], now=1000)
        delays.append(delay)
    assert delays == [10, 30, 60, 60, 60]
    assert state == {"failures": 5, "next_attempt": 1000 + 60 * 60, "notified": False}


def test_handle_rate_limit_hit_does_not_notify_during_backoff_steps(mocker):
    ntfy_mock = mocker.patch("src.updateDynDns.send_ntfy_notification")
    state = {}
    for _ in range(3):
        state = handle_rate_limit_hit(state, SETTINGS, "refused", now=0)
    ntfy_mock.assert_not_called()
    assert state["failures"] == 3
    assert state["next_attempt"] == 60 * 60


def test_handle_rate_limit_hit_notifies_once_after_last_backoff_step(mocker):
    ntfy_mock = mocker.patch("src.updateDynDns.send_ntfy_notification", return_value=True)
    state = {"failures": 3, "next_attempt": 0, "notified": False}

    state = handle_rate_limit_hit(state, SETTINGS, "refused", now=0)
    ntfy_mock.assert_called_once()
    assert "100 minutes" in ntfy_mock.call_args.args[2]
    assert state["notified"] is True

    handle_rate_limit_hit(state, SETTINGS, "refused", now=0)
    ntfy_mock.assert_called_once()


def test_handle_rate_limit_hit_retries_notification_when_sending_failed(mocker):
    ntfy_mock = mocker.patch("src.updateDynDns.send_ntfy_notification", return_value=False)
    state = {"failures": 3, "next_attempt": 0, "notified": False}

    state = handle_rate_limit_hit(state, SETTINGS, "refused", now=0)
    state = handle_rate_limit_hit(state, SETTINGS, "refused", now=0)

    assert ntfy_mock.call_count == 2
    assert state["notified"] is False


def test_handle_rate_limit_hit_uses_configured_backoff(mocker):
    ntfy_mock = mocker.patch("src.updateDynDns.send_ntfy_notification", return_value=True)
    settings = dict(SETTINGS, RATE_LIMIT_BACKOFF_MINUTES=[1])

    state = handle_rate_limit_hit({}, settings, "refused", now=0)
    assert state["next_attempt"] == 60
    ntfy_mock.assert_not_called()

    handle_rate_limit_hit(state, settings, "refused", now=0)
    ntfy_mock.assert_called_once()


def test_handle_rate_limit_hit_logs_connection_refused(mocker, caplog):
    mocker.patch("src.updateDynDns.send_ntfy_notification")
    with caplog.at_level("INFO"):
        handle_rate_limit_hit({}, SETTINGS, "refused", now=0)
    assert "Connection Refused" in caplog.text
    assert "in 10 minutes" in caplog.text


# --- send_ntfy_notification -----------------------------------------------


def test_send_ntfy_notification_skipped_without_topic(mocker):
    post_mock = mocker.patch("requests.post")
    assert send_ntfy_notification({"NTFY_TOPIC": ""}, "title", "message") is False
    post_mock.assert_not_called()


def test_send_ntfy_notification_posts_to_default_server(mocker):
    post_mock = mocker.patch("requests.post")

    assert send_ntfy_notification({"NTFY_TOPIC": "alerts"}, "Title", "Hello") is True

    post_mock.assert_called_once()
    assert post_mock.call_args.args[0] == "https://ntfy.sh/alerts"
    assert post_mock.call_args.kwargs["data"] == b"Hello"
    headers = post_mock.call_args.kwargs["headers"]
    assert headers == {"Title": "Title", "Priority": "high", "Tags": "warning"}


def test_send_ntfy_notification_uses_custom_server_and_token(mocker):
    post_mock = mocker.patch("requests.post")
    settings = {
        "NTFY_SERVER": "https://ntfy.example.com/",
        "NTFY_TOPIC": "/alerts",
        "NTFY_TOKEN": "tk_secret",
    }

    send_ntfy_notification(settings, "Title", "Hello", priority="default", tags="ok")

    assert post_mock.call_args.args[0] == "https://ntfy.example.com/alerts"
    headers = post_mock.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer tk_secret"
    assert headers["Priority"] == "default"
    assert headers["Tags"] == "ok"


def test_send_ntfy_notification_returns_false_on_request_error(mocker):
    mocker.patch("requests.post", side_effect=requests.exceptions.ConnectionError("down"))
    assert send_ntfy_notification({"NTFY_TOPIC": "alerts"}, "Title", "Hello") is False


def test_send_ntfy_notification_returns_false_on_http_error(mocker):
    response = mocker.MagicMock()
    response.raise_for_status.side_effect = requests.exceptions.HTTPError("403")
    mocker.patch("requests.post", return_value=response)
    assert send_ntfy_notification({"NTFY_TOPIC": "alerts"}, "Title", "Hello") is False


# --- process_subdomain login ----------------------------------------------


def test_process_subdomain_raises_on_connection_refused(mocker):
    post_mock = mocker.patch("requests.post", side_effect=_connection_refused_error())
    with pytest.raises(NetcupLoginRateLimitError):
        process_subdomain("sub.example.com", SETTINGS, "1.2.3.4", "::1")
    # No logout without a session.
    assert post_mock.call_count == 1


def test_process_subdomain_raises_on_http_429(mocker):
    mocker.patch("requests.post", return_value=_response(mocker, status_code=429))
    with pytest.raises(NetcupLoginRateLimitError):
        process_subdomain("sub.example.com", SETTINGS, "1.2.3.4", "::1")


def test_process_subdomain_raises_on_login_limit_message(mocker):
    mocker.patch(
        "requests.post",
        return_value=_response(mocker, {"status": "error", "longmessage": "Too many logins"}),
    )
    with pytest.raises(NetcupLoginRateLimitError, match="Too many logins"):
        process_subdomain("sub.example.com", SETTINGS, "1.2.3.4", "::1")


def test_process_subdomain_other_connection_error_is_login_failed(mocker):
    mocker.patch("requests.post", side_effect=requests.exceptions.Timeout("timed out"))
    results, _ = process_subdomain("sub.example.com", SETTINGS, "1.2.3.4", "::1")
    assert "LOGIN FAILED" in results[0]["destination"]


def test_process_subdomain_wrong_credentials_is_login_refused(mocker):
    mocker.patch(
        "requests.post",
        return_value=_response(mocker, {"status": "error", "longmessage": "Invalid API key"}),
    )
    results, _ = process_subdomain("sub.example.com", SETTINGS, "1.2.3.4", "::1")
    assert "LOGIN REFUSED" in results[0]["destination"]


# --- main() with mocked helpers -------------------------------------------


def _main_mocks(mocker, rate_limit_state=None, cached_ips=(None, None), settings=None):
    mocker.patch("src.updateDynDns.create_settings_file_if_not_exists")
    mocker.patch("src.updateDynDns.read_cached_ips", return_value=cached_ips)
    mocker.patch("src.updateDynDns.write_cached_ips")
    mocker.patch("src.updateDynDns.read_failed_domains", return_value={})
    write_failed_mock = mocker.patch("src.updateDynDns.write_failed_domains")
    mocker.patch(
        "src.updateDynDns.read_rate_limit_state",
        return_value=dict(rate_limit_state) if rate_limit_state else {},
    )
    write_state_mock = mocker.patch("src.updateDynDns.write_rate_limit_state")
    mocker.patch(
        "builtins.open", mocker.mock_open(read_data=json.dumps(settings or SETTINGS))
    )
    ip_responses = [_response(mocker, {"ip": "1.2.3.4"}), _response(mocker, {"ip": "::1"})]
    get_mock = mocker.patch("requests.get", side_effect=ip_responses)
    ntfy_mock = mocker.patch("src.updateDynDns.send_ntfy_notification", return_value=True)
    return SimpleNamespace(
        write_failed=write_failed_mock,
        write_state=write_state_mock,
        get=get_mock,
        ntfy=ntfy_mock,
    )


def test_main_skips_run_during_backoff(mocker, caplog):
    mocks = _main_mocks(
        mocker, {"failures": 1, "next_attempt": time.time() + 600, "notified": False}
    )
    process_mock = mocker.patch("src.updateDynDns.process_subdomain")

    with caplog.at_level("INFO"), pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 0
    assert "Connection Refused" in caplog.text
    mocks.get.assert_not_called()
    process_mock.assert_not_called()


def test_main_force_ignores_backoff(mocker, caplog):
    _main_mocks(
        mocker, {"failures": 1, "next_attempt": time.time() + 600, "notified": False}
    )
    process_mock = mocker.patch("src.updateDynDns.process_subdomain", return_value=([], 2))

    with caplog.at_level("INFO"):
        main(["--force"])

    process_mock.assert_called_once()
    assert "despite the login backoff" in caplog.text


def test_main_retries_all_domains_after_backoff_despite_unchanged_ips(mocker):
    mocks = _main_mocks(
        mocker,
        {"failures": 1, "next_attempt": time.time() - 1, "notified": False},
        cached_ips=("1.2.3.4", "::1"),
        settings=dict(SETTINGS, NETCUP_DOMAIN="a.example.com,b.example.com"),
    )
    process_mock = mocker.patch("src.updateDynDns.process_subdomain", return_value=([], 2))

    main([])

    assert sorted(call.args[0] for call in process_mock.call_args_list) == [
        "a.example.com",
        "b.example.com",
    ]
    # Logins work again: the backoff state is cleared without an all-clear
    # notification because nothing was reported yet.
    mocks.write_state.assert_called_once_with({}, cache_dir=mocker.ANY)
    mocks.ntfy.assert_not_called()


def test_main_sends_all_clear_after_notified_rate_limit(mocker):
    mocks = _main_mocks(
        mocker, {"failures": 4, "next_attempt": time.time() - 1, "notified": True}
    )
    mocker.patch("src.updateDynDns.process_subdomain", return_value=([], 2))

    main([])

    mocks.write_state.assert_called_once_with({}, cache_dir=mocker.ANY)
    mocks.ntfy.assert_called_once()
    assert mocks.ntfy.call_args.kwargs["priority"] == "default"


def test_main_does_not_write_state_without_rate_limit(mocker):
    mocks = _main_mocks(mocker)
    mocker.patch("src.updateDynDns.process_subdomain", return_value=([], 2))

    main([])

    mocks.write_state.assert_not_called()


def test_main_rate_limit_stops_sequential_run_and_records_backoff(mocker, caplog):
    mocks = _main_mocks(
        mocker, settings=dict(SETTINGS, NETCUP_DOMAIN="a.example.com,b.example.com")
    )
    process_mock = mocker.patch(
        "src.updateDynDns.process_subdomain",
        side_effect=NetcupLoginRateLimitError("Connection refused"),
    )

    with caplog.at_level("INFO"):
        main([])

    # The second domain is not tried: its login would be refused as well.
    process_mock.assert_called_once()
    state = mocks.write_state.call_args.args[0]
    assert state["failures"] == 1
    assert state["next_attempt"] == pytest.approx(time.time() + 600, abs=5)
    # Refused logins do not count against the per-domain retry budget.
    assert mocks.write_failed.call_args.args[0] == {}
    assert caplog.text.count("CONNECTION REFUSED - retry after") == 2
    mocks.ntfy.assert_not_called()


def test_main_rate_limit_keeps_results_of_domains_updated_before(mocker, caplog):
    mocks = _main_mocks(
        mocker, settings=dict(SETTINGS, NETCUP_DOMAIN="a.example.com,b.example.com")
    )
    ok = [{"domain": "example.com", "subdomain": "a", "record_type": "A", "destination": "1.2.3.4"}]
    mocker.patch(
        "src.updateDynDns.process_subdomain",
        side_effect=[(ok, 2), NetcupLoginRateLimitError("Connection refused")],
    )

    with caplog.at_level("INFO"):
        main([])

    assert "1.2.3.4" in caplog.text
    assert caplog.text.count("CONNECTION REFUSED - retry after") == 1
    assert mocks.write_state.call_args.args[0]["failures"] == 1


def test_main_rate_limit_in_parallel_mode(mocker, caplog):
    mocks = _main_mocks(
        mocker,
        settings=dict(
            SETTINGS, NETCUP_DOMAIN="a.example.com,b.example.com", PARALLEL_PROCESSES=2
        ),
    )
    mocker.patch(
        "src.updateDynDns.process_subdomain",
        side_effect=NetcupLoginRateLimitError("Connection refused"),
    )

    with caplog.at_level("INFO"):
        main([])

    assert mocks.write_state.call_args.args[0]["failures"] == 1
    assert mocks.write_failed.call_args.args[0] == {}
    assert caplog.text.count("CONNECTION REFUSED - retry after") == 2


# --- main() end-to-end with real cache files --------------------------------


def test_main_backoff_sequence_and_ntfy_notification(tmp_path, mocker):
    """Walks through 10/30/60 minute backoff steps with a mocked clock: runs
    inside a backoff window are skipped, the ntfy notification is sent once
    when the login is still refused after 60 minutes, and an all-clear is
    sent once logins work again."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(SETTINGS))
    cache_dir = tmp_path / "cache"
    argv = ["--settings-file", str(settings_file), "--cache-dir", str(cache_dir)]

    clock = [1_000_000.0]
    mocker.patch(
        "src.updateDynDns.time",
        SimpleNamespace(
            time=lambda: clock[0], strftime=time.strftime, localtime=time.localtime
        ),
    )
    mocker.patch(
        "requests.get",
        side_effect=lambda url, **kwargs: _response(
            mocker, {"ip": "::1" if "api6" in url else "1.2.3.4"}
        ),
    )

    netcup_available = [False]
    ntfy_messages = []

    def fake_post(url, json=None, **kwargs):
        if url != NETCUP_API:
            ntfy_messages.append(kwargs["headers"]["Title"])
            return _response(mocker)
        if not netcup_available[0]:
            raise _connection_refused_error()
        payloads = {
            "login": {"status": "success", "responsedata": {"apisessionid": "s"}},
            "infoDnsRecords": {
                "status": "success",
                "responsedata": {
                    "dnsrecords": [
                        {"id": "1", "hostname": "sub", "type": "A"},
                        {"id": "2", "hostname": "sub", "type": "AAAA"},
                    ]
                },
            },
        }
        return _response(mocker, payloads.get(json["action"], {"status": "success"}))

    mocker.patch("requests.post", side_effect=fake_post)

    def run_at(minutes):
        clock[0] = 1_000_000.0 + minutes * 60
        main(argv)
        return read_rate_limit_state(cache_dir=cache_dir)

    # First refused login: wait at least 10 minutes.
    assert run_at(0)["failures"] == 1
    # 5 minutes later the run is skipped.
    with pytest.raises(SystemExit):
        run_at(5)
    assert read_rate_limit_state(cache_dir=cache_dir)["failures"] == 1
    # 10 minutes later: refused again, wait at least 30 minutes.
    state = run_at(10)
    assert state["failures"] == 2
    assert state["next_attempt"] == clock[0] + 30 * 60
    with pytest.raises(SystemExit):
        run_at(35)
    # 30 minutes later: refused again, wait at least 60 minutes.
    state = run_at(40)
    assert state["failures"] == 3
    assert state["next_attempt"] == clock[0] + 60 * 60
    assert ntfy_messages == []
    # Still refused after 60 minutes: ntfy notification.
    state = run_at(100)
    assert state == {"failures": 4, "next_attempt": clock[0] + 3600, "notified": True}
    assert ntfy_messages == ["netcup DynDNS: Connection Refused"]
    # Still refused another 60 minutes later: no second notification.
    assert run_at(160)["failures"] == 5
    assert len(ntfy_messages) == 1
    # Refused logins never count against the retry budget.
    assert json.loads((cache_dir / "failed_domains.json").read_text()) == {}

    # Logins work again: records are updated, state cleared, all-clear sent.
    netcup_available[0] = True
    assert run_at(220) == {}
    assert not (cache_dir / "rate_limit.json").exists()
    assert ntfy_messages[-1] == "netcup DynDNS: login works again"
