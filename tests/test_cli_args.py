import json

import pytest

from src.updateDynDns import (
    apply_cli_overrides,
    build_arg_parser,
    main,
    parse_cli_args,
)


# --- parse_cli_args / build_arg_parser -------------------------------------


def test_parse_cli_args_defaults_are_none_when_no_args_given():
    args = parse_cli_args([])
    assert args.api_password is None
    assert args.api_key is None
    assert args.customer_id is None
    assert args.netcup_domain is None
    assert args.nextcloud_path is None
    assert args.trusted_proxies_pos is None
    assert args.parallel_processes is None
    assert args.ip_mode is None
    assert args.disable_nextcloud_nginx is None


def test_parse_cli_args_parses_all_options():
    args = parse_cli_args(
        [
            "--api-password", "cli-password",
            "--api-key", "cli-key",
            "--customer-id", "cli-customer",
            "--netcup-domain", "sub.example.com,app.example.net",
            "--nextcloud-path", "/var/www/nextcloud",
            "--trusted-proxies-pos", "1",
            "--parallel-processes", "4",
            "--ip-mode", "ipv4",
            "--disable-nextcloud-nginx",
        ]
    )

    assert args.api_password == "cli-password"
    assert args.api_key == "cli-key"
    assert args.customer_id == "cli-customer"
    assert args.netcup_domain == "sub.example.com,app.example.net"
    assert args.nextcloud_path == "/var/www/nextcloud"
    assert args.trusted_proxies_pos == "1"
    assert args.parallel_processes == 4
    assert args.ip_mode == "ipv4"
    assert args.disable_nextcloud_nginx is True


def test_parse_cli_args_no_disable_nextcloud_nginx_sets_false():
    args = parse_cli_args(["--no-disable-nextcloud-nginx"])
    assert args.disable_nextcloud_nginx is False


def test_parse_cli_args_rejects_invalid_ip_mode():
    with pytest.raises(SystemExit):
        parse_cli_args(["--ip-mode", "bogus"])


def test_parse_cli_args_help_exits_with_zero(capsys):
    with pytest.raises(SystemExit) as exc_info:
        parse_cli_args(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "--api-password" in captured.out
    assert "--ip-mode" in captured.out
    assert "--disable-nextcloud-nginx" in captured.out


def test_build_arg_parser_returns_parser_with_expected_options():
    parser = build_arg_parser()
    option_strings = {
        action.option_strings[0]
        for action in parser._actions
        if action.option_strings
    }
    assert {
        "-h",
        "--api-password",
        "--api-key",
        "--customer-id",
        "--netcup-domain",
        "--nextcloud-path",
        "--trusted-proxies-pos",
        "--parallel-processes",
        "--ip-mode",
        "--disable-nextcloud-nginx",
    }.issubset(option_strings)


# --- apply_cli_overrides ----------------------------------------------------


def test_apply_cli_overrides_only_overrides_provided_arguments():
    settings = {"API_PASSWORD": "from-settings", "API_KEY": "from-settings-key"}
    args = parse_cli_args(["--api-password", "from-cli"])

    result = apply_cli_overrides(settings, args)

    assert result["API_PASSWORD"] == "from-cli"
    assert result["API_KEY"] == "from-settings-key"
    assert result is settings


def test_apply_cli_overrides_noop_without_any_cli_args():
    settings = {"API_PASSWORD": "from-settings"}
    args = parse_cli_args([])

    result = apply_cli_overrides(settings, args)

    assert result == {"API_PASSWORD": "from-settings"}


def test_apply_cli_overrides_overrides_boolean_disable_flag():
    settings = {"DISABLE_NEXTCLOUD_NGINX": False}
    args = parse_cli_args(["--disable-nextcloud-nginx"])

    result = apply_cli_overrides(settings, args)

    assert result["DISABLE_NEXTCLOUD_NGINX"] is True


# --- end-to-end: CLI args override .settings.json via main() ---------------


MOCK_SETTINGS = {
    "API_PASSWORD": "settings-password",
    "API_KEY": "settings-key",
    "CUSTOMER_ID": "settings-customer",
    "NETCUP_DOMAIN": "settings-domain.example.com",
    "NEXTCLOUD_PATH": "/var/www/nextcloud",
    "TRUSTED_PROXIES_POS": "0",
    "DISABLE_NEXTCLOUD_NGINX": True,
}


def _mock_ip_get_responses(mocker, ipv4="1.2.3.4", ipv6="::1"):
    ipv4_response = mocker.MagicMock()
    ipv4_response.json.return_value = {"ip": ipv4}
    ipv6_response = mocker.MagicMock()
    ipv6_response.json.return_value = {"ip": ipv6}
    return [ipv4_response, ipv6_response]


def test_main_cli_args_override_settings_json_domain(mocker):
    """A --netcup-domain CLI argument should take precedence over NETCUP_DOMAIN
    from .settings.json when processing subdomains."""
    mocker.patch("src.updateDynDns.create_settings_file_if_not_exists")
    mocker.patch("src.updateDynDns.read_cached_ips", return_value=(None, None))
    mocker.patch("src.updateDynDns.write_cached_ips")
    mocker.patch("src.updateDynDns.read_failed_domains", return_value={})
    mocker.patch("src.updateDynDns.write_failed_domains")
    mocker.patch(
        "builtins.open", mocker.mock_open(read_data=json.dumps(MOCK_SETTINGS))
    )
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")

    process_subdomain_mock = mocker.patch(
        "src.updateDynDns.process_subdomain", return_value=([], 2)
    )

    main(["--netcup-domain", "cli-domain.example.org"])

    process_subdomain_mock.assert_called_once()
    called_domain = process_subdomain_mock.call_args.args[0]
    assert called_domain == "cli-domain.example.org"


def test_main_cli_disable_nextcloud_nginx_overrides_settings(mocker):
    """--no-disable-nextcloud-nginx should force-enable the Nextcloud/Nginx step
    even though .settings.json has DISABLE_NEXTCLOUD_NGINX set to true."""
    mocker.patch("src.updateDynDns.create_settings_file_if_not_exists")
    mocker.patch("src.updateDynDns.read_cached_ips", return_value=(None, None))
    mocker.patch("src.updateDynDns.write_cached_ips")
    mocker.patch("src.updateDynDns.read_failed_domains", return_value={})
    mocker.patch("src.updateDynDns.write_failed_domains")
    mocker.patch(
        "builtins.open", mocker.mock_open(read_data=json.dumps(MOCK_SETTINGS))
    )
    mocker.patch("requests.get", side_effect=_mock_ip_get_responses(mocker))
    nginx_mock = mocker.patch("src.updateDynDns.nginx_trusted_proxies_configuration")
    mocker.patch("src.updateDynDns.process_subdomain", return_value=([], 2))

    main(["--no-disable-nextcloud-nginx"])

    nginx_mock.assert_called_once_with("/var/www/nextcloud", "0", "::1")
