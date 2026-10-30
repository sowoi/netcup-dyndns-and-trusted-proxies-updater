import io

import pytest
import requests

from src.netcup_dyndns import (
    IPV4_APIS,
    colors_enabled,
    fetch_public_ip,
    group_domains_by_zone,
    parse_domain_entry,
    read_trusted_proxy_ip,
    split_domain,
    write_trusted_proxy_ip,
)

# --- parse_domain_entry -------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("sub.example.com", ("sub", "example.com")),
        (" sub.example.com ", ("sub", "example.com")),
        ("sub.example.com.", ("sub", "example.com")),
        ("example.com", ("@", "example.com")),
        # Same split as before 1.5.0, so ccSLD zones keep working.
        ("www.example.co.uk", ("www", "example.co.uk")),
        ("a.b.example.com", ("a", "b.example.com")),
        ("a.b@example.com", ("a.b", "example.com")),
        ("www@example.co.uk", ("www", "example.co.uk")),
        ("@example.co.uk", ("@", "example.co.uk")),
        ("a.b@example.co.uk", ("a.b", "example.co.uk")),
        ("*.example.com", ("*", "example.com")),
    ],
)
def test_parse_domain_entry(entry, expected):
    assert parse_domain_entry(entry) == expected


@pytest.mark.parametrize(
    "entry", ["localhost", "", "sub..example.com", "www@localhost", "www@", ".example.com"]
)
def test_parse_domain_entry_rejects_invalid_entries(entry):
    with pytest.raises(ValueError):
        parse_domain_entry(entry)


def test_split_domain_falls_back_for_invalid_entries():
    assert split_domain("localhost") == ("localhost", "localhost")
    assert split_domain("sub.example.com") == ("sub", "example.com")


def test_group_domains_by_zone():
    zones, invalid = group_domains_by_zone(
        ["a.example.com", "example.com", "app.example.net", "localhost", "www@example.co.uk"]
    )
    assert zones == {
        "example.com": [("a.example.com", "a"), ("example.com", "@")],
        "example.net": [("app.example.net", "app")],
        "example.co.uk": [("www@example.co.uk", "www")],
    }
    assert invalid == ["localhost"]


# --- fetch_public_ip ------------------------------------------------------------


def _response(mocker, payload=None, text=""):
    response = mocker.MagicMock()
    if payload is None:
        response.json.side_effect = ValueError("no json")
    else:
        response.json.return_value = payload
    response.text = text
    return response


def test_fetch_public_ip_uses_first_valid_answer(mocker):
    get_mock = mocker.patch("requests.get", return_value=_response(mocker, {"ip": "1.2.3.4"}))

    assert fetch_public_ip(IPV4_APIS, 4) == "1.2.3.4"
    get_mock.assert_called_once_with(url=IPV4_APIS[0], timeout=mocker.ANY)


def test_fetch_public_ip_reads_plain_text_answers(mocker):
    mocker.patch("requests.get", return_value=_response(mocker, text="2001:db8::1\n"))
    assert fetch_public_ip(["https://ipv6.example"], 6) == "2001:db8::1"


def test_fetch_public_ip_skips_wrong_family_and_errors(mocker):
    mocker.patch(
        "requests.get",
        side_effect=[
            requests.exceptions.Timeout("slow"),
            _response(mocker, {"ip": "::1"}),
            _response(mocker, {"ip": "5.6.7.8"}),
        ],
    )
    assert fetch_public_ip(["a", "b", "c"], 4) == "5.6.7.8"


def test_fetch_public_ip_returns_none_when_all_providers_fail(mocker):
    bad_status = _response(mocker, {"ip": "1.2.3.4"})
    bad_status.raise_for_status.side_effect = requests.exceptions.HTTPError("503")
    mocker.patch("requests.get", side_effect=[bad_status, _response(mocker, text="garbage")])
    assert fetch_public_ip(["a", "b"], 4) is None


# --- colors_enabled -------------------------------------------------------------


class _Tty(io.StringIO):
    def isatty(self):
        return True


def test_colors_enabled_on_terminal():
    assert colors_enabled(_Tty(), env={}) is True


def test_colors_disabled_without_terminal():
    assert colors_enabled(io.StringIO(), env={}) is False


def test_colors_disabled_by_no_color():
    assert colors_enabled(_Tty(), env={"NO_COLOR": "1"}) is False


def test_colors_enabled_with_empty_no_color():
    assert colors_enabled(_Tty(), env={"NO_COLOR": ""}) is True


# --- trusted proxy cache --------------------------------------------------------


def test_trusted_proxy_cache_roundtrip(tmp_path):
    assert read_trusted_proxy_ip(cache_dir=tmp_path / "cache") is None
    write_trusted_proxy_ip("2001:db8::1", cache_dir=tmp_path / "cache")
    assert read_trusted_proxy_ip(cache_dir=tmp_path / "cache") == "2001:db8::1"
