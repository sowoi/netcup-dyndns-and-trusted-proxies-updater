from src.updateDynDns import nginx_trusted_proxies_configuration
from unittest.mock import patch

def test_nginx_trusted_proxies_configuration():
    nextcloud_path = "/var/www/nextcloud"
    trusted_proxies_pos = "0"
    ipv6 = "::1"

    with patch("subprocess.run") as mock_run:
        nginx_trusted_proxies_configuration(nextcloud_path, trusted_proxies_pos, ipv6)
        mock_run.assert_any_call(
            [
                "sudo",
                "-u",
                "www-data",
                "php",
                f"{nextcloud_path}/occ",
                "config:system:set",
                "trusted_proxies",
                trusted_proxies_pos,
                f"--value={ipv6}",
            ],
            check=True,
        )

        mock_run.assert_any_call(["systemctl", "restart", "nginx"], check=True)

        assert mock_run.call_count == 2