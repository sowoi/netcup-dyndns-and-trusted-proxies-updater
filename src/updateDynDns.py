import argparse
import logging
import os
import sys
import requests
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm


class TqdmLoggingHandler(logging.Handler):
    """Routes log records through tqdm.write so they don't clobber an active progress bar."""

    def emit(self, record):
        try:
            msg = self.format(record)
            tqdm.write(msg)
        except Exception:
            self.handleError(record)


class ColorFormatter(logging.Formatter):
    """Colors ERROR (and above) log records red so failures stand out."""

    RED = "\033[91m"
    RESET = "\033[0m"

    def format(self, record):
        message = super().format(record)
        if record.levelno >= logging.ERROR:
            return f"{self.RED}{message}{self.RESET}"
        return message


_handler = TqdmLoggingHandler()
_handler.setFormatter(
    ColorFormatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
)
logging.basicConfig(level=logging.INFO, handlers=[_handler])
logger = logging.getLogger(__name__)

conf = ".settings.json"
cache_dir = ".temp"
# URLs to APIs
NETCUP_API = "https://ccp.netcup.net/run/webservice/servers/endpoint.php?JSON"
IPV4_API = "https://api.ipify.org?format=json"
IPV6_API = "https://api6.ipify.org?format=json"

RED_COLOR = "\033[91m"
RESET_COLOR = "\033[0m"

# default .settings.json values
settings_file_path = ".settings.json"
DEFAULT_PARALLEL_PROCESSES = 1  # Standardmäßig sequentiell (1 Worker)
DEFAULT_IP_MODE = "both"
VALID_IP_MODES = {"ipv4", "ipv6", "both"}
MAX_SUBDOMAIN_RETRIES = 5
failed_domains_cache_file = "failed_domains.json"

default_settings = {
    "API_PASSWORD": "",
    "API_KEY": "",
    "CUSTOMER_ID": "",
    "NETCUP_DOMAIN": "",
    "NEXTCLOUD_PATH": "",
    "TRUSTED_PROXIES_POS": "",
    "PARALLEL_PROCESSES": DEFAULT_PARALLEL_PROCESSES,
    "IP_MODE": DEFAULT_IP_MODE,
    "DISABLE_NEXTCLOUD_NGINX": False,
}


def create_settings_file_if_not_exists(file_path, default_content):
    settings_file_path = Path(file_path)
    if not settings_file_path.exists():
        with open(file_path, "w") as f:
            json.dump(default_content, f, indent=4)
        logger.info("Settings file created at %s", file_path)
    else:
        logger.debug("Settings file already exists at %s", file_path)


# Function to read IP addresses from cache
def read_cached_ips(ipv4_cache=None, ipv6_cache=None, cache_dir=cache_dir):
    cache_path = Path(cache_dir)
    try:
        ipv4_cache = (cache_path / "ipv4_cache.txt").read_text() or None
        ipv6_cache = (cache_path / "ipv6_cache.txt").read_text() or None
    except FileNotFoundError:
        pass
    return ipv4_cache, ipv6_cache


# Function to write IP addresses to cache
def write_cached_ips(ipv4, ipv6=None, cache_dir=cache_dir):
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    # Cache files always store a string; unavailable addresses (e.g. no IPv6
    # connectivity) are written as an empty string and read back as None.
    (cache_path / "ipv4_cache.txt").write_text(ipv4 or "")
    (cache_path / "ipv6_cache.txt").write_text(ipv6 or "")


# Function to read the per-subdomain failure/retry counters from cache
def read_failed_domains(cache_dir=cache_dir):
    """Return a dict mapping a failed subdomain (e.g. "sub.example.com") to the
    number of consecutive failed update attempts recorded for it. Returns an
    empty dict if the cache file is missing or unreadable/invalid.
    """
    cache_path = Path(cache_dir) / failed_domains_cache_file
    try:
        data = json.loads(cache_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(domain): int(count)
        for domain, count in data.items()
        if isinstance(count, (int, float))
    }


# Function to write the per-subdomain failure/retry counters to cache
def write_failed_domains(failed_domains, cache_dir=cache_dir):
    """Persist the per-subdomain failure/retry counters to cache."""
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    (cache_path / failed_domains_cache_file).write_text(json.dumps(failed_domains))


# Validates values in settings.json
def validate_settings(settings):
    required_keys = [
        "API_PASSWORD",
        "API_KEY",
        "CUSTOMER_ID",
        "NETCUP_DOMAIN",
    ]
    if not settings.get("DISABLE_NEXTCLOUD_NGINX", False):
        required_keys.extend(["NEXTCLOUD_PATH", "TRUSTED_PROXIES_POS"])

    for key in required_keys:
        if key not in settings:
            raise KeyError(f"The key {key} is missing in the configuration file.")
        if not settings[key]:
            raise ValueError(
                f"The key {key} cannot be empty. Please fill in the missing value in the .settings.json file."
            )


SECRET_FILE_ENV_SUFFIX = "_FILE"
DEFAULT_OPENBAO_SECRET_PATH = "secret/data/netcup-dyndns"


def fetch_openbao_secrets(env=None):
    """Optionally fetch a KV v2 secret from an OpenBAO (or Vault-compatible) server.

    Entirely opt-in and controlled via environment variables:
      OPENBAO_ADDR         - base URL, e.g. https://openbao.example.com:8200
      OPENBAO_TOKEN        - auth token (or OPENBAO_TOKEN_FILE to read it from a file)
      OPENBAO_SECRET_PATH  - KV v2 path, e.g. secret/data/netcup-dyndns (default shown)

    Returns a dict of settings keys to override, limited to known settings keys.
    Returns {} if OPENBAO_ADDR is not configured or the request/parsing fails.
    """
    env = os.environ if env is None else env
    addr = env.get("OPENBAO_ADDR")
    if not addr:
        return {}

    token = env.get("OPENBAO_TOKEN")
    token_file = env.get("OPENBAO_TOKEN_FILE")
    if not token and token_file:
        try:
            token = Path(token_file).read_text().strip()
        except OSError as e:
            logger.warning("Could not read OpenBAO token file %s: %s", token_file, e)
            return {}

    if not token:
        logger.warning(
            "OPENBAO_ADDR is set but no OPENBAO_TOKEN or OPENBAO_TOKEN_FILE was provided."
        )
        return {}

    secret_path = env.get("OPENBAO_SECRET_PATH", DEFAULT_OPENBAO_SECRET_PATH)
    url = f"{addr.rstrip('/')}/v1/{secret_path.lstrip('/')}"

    try:
        response = requests.get(url, headers={"X-Vault-Token": token}, timeout=5)
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.RequestException as e:
        logger.warning("Could not fetch secrets from OpenBAO at %s: %s", url, e)
        return {}
    except ValueError as e:
        logger.warning("Invalid JSON response from OpenBAO at %s: %s", url, e)
        return {}

    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    # KV v2 nests the actual secret data under an extra "data" key.
    if isinstance(data.get("data"), dict):
        data = data["data"]

    return {key: value for key, value in data.items() if key in default_settings}


def apply_file_secret_overrides(settings, env=None):
    """Override settings values from `<KEY>_FILE` environment variables pointing at
    mounted secret files (Docker secrets / Kubernetes secrets / OpenBAO Agent
    injector convention). Silently skipped for keys without a corresponding
    `<KEY>_FILE` variable set.
    """
    env = os.environ if env is None else env
    for key in default_settings:
        file_path = env.get(f"{key}{SECRET_FILE_ENV_SUFFIX}")
        if not file_path:
            continue
        try:
            settings[key] = Path(file_path).read_text().strip()
        except OSError as e:
            logger.warning(
                "Could not read secret file %s for %s: %s", file_path, key, e
            )
            continue
        logger.info("Overrode setting %s from secret file %s", key, file_path)
    return settings


def apply_secret_overrides(settings, env=None):
    """Apply optional runtime secret overrides on top of the values loaded from
    `.settings.json`. Both mechanisms are opt-in and disabled by default:

      1. Direct retrieval of a KV v2 secret from an OpenBAO (or Vault-compatible)
         server (see `fetch_openbao_secrets`).
      2. `<KEY>_FILE` environment variables pointing at mounted secret files (see
         `apply_file_secret_overrides`), which take precedence over OpenBAO since
         they are applied last.
    """
    settings.update(fetch_openbao_secrets(env))
    apply_file_secret_overrides(settings, env)
    return settings


CLI_ARGUMENT_TO_SETTINGS_KEY = {
    "api_password": "API_PASSWORD",
    "api_key": "API_KEY",
    "customer_id": "CUSTOMER_ID",
    "netcup_domain": "NETCUP_DOMAIN",
    "nextcloud_path": "NEXTCLOUD_PATH",
    "trusted_proxies_pos": "TRUSTED_PROXIES_POS",
    "parallel_processes": "PARALLEL_PROCESSES",
    "ip_mode": "IP_MODE",
    "disable_nextcloud_nginx": "DISABLE_NEXTCLOUD_NGINX",
}


def build_arg_parser():
    """Build the command-line argument parser.

    Every option corresponds to a key in `.settings.json`. When provided, a
    command-line argument always takes precedence over both the settings
    file and any secret provider overrides, since CLI overrides are applied
    last. Running with -h/--help prints all available options and exits.
    """
    parser = argparse.ArgumentParser(
        prog="updateDynDns",
        description=(
            "Updates Netcup DNS A/AAAA records with the host's current public IP "
            "address(es) and, optionally, the Nextcloud trusted_proxies "
            "configuration. Any option given here overrides the corresponding "
            "value from .settings.json (and any secret provider)."
        ),
    )
    parser.add_argument(
        "--api-password",
        dest="api_password",
        default=None,
        help="Netcup API password. Overrides API_PASSWORD.",
    )
    parser.add_argument(
        "--api-key",
        dest="api_key",
        default=None,
        help="Netcup API key. Overrides API_KEY.",
    )
    parser.add_argument(
        "--customer-id",
        dest="customer_id",
        default=None,
        help="Netcup customer ID. Overrides CUSTOMER_ID.",
    )
    parser.add_argument(
        "--netcup-domain",
        dest="netcup_domain",
        default=None,
        help=(
            "Comma-separated domain(s) to update, e.g. "
            "'example.com,example.net'. Overrides NETCUP_DOMAIN."
        ),
    )
    parser.add_argument(
        "--nextcloud-path",
        dest="nextcloud_path",
        default=None,
        help="Path to the Nextcloud installation. Overrides NEXTCLOUD_PATH.",
    )
    parser.add_argument(
        "--trusted-proxies-pos",
        dest="trusted_proxies_pos",
        default=None,
        help=(
            "Position in the trusted_proxies configuration to update. "
            "Overrides TRUSTED_PROXIES_POS."
        ),
    )
    parser.add_argument(
        "--parallel-processes",
        dest="parallel_processes",
        type=int,
        default=None,
        help="Number of parallel DNS-update workers. Overrides PARALLEL_PROCESSES.",
    )
    parser.add_argument(
        "--ip-mode",
        dest="ip_mode",
        choices=sorted(VALID_IP_MODES),
        default=None,
        help="Which IP types to update: 'both' (default), 'ipv4', or 'ipv6'. Overrides IP_MODE.",
    )
    parser.add_argument(
        "--disable-nextcloud-nginx",
        dest="disable_nextcloud_nginx",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Disable the Nextcloud OCC / Nginx reload tasks (use "
            "--no-disable-nextcloud-nginx to force-enable them). "
            "Overrides DISABLE_NEXTCLOUD_NGINX."
        ),
    )
    return parser


def parse_cli_args(argv=None):
    """Parse command-line arguments. Passing -h/--help exits the process
    after printing usage information for all available options."""
    return build_arg_parser().parse_args(argv)


def apply_cli_overrides(settings, args):
    """Apply command-line argument overrides on top of `.settings.json` and any
    secret provider overrides. Only explicitly provided (non-None) arguments
    are applied, and they always take precedence since they are applied last.
    """
    for arg_name, settings_key in CLI_ARGUMENT_TO_SETTINGS_KEY.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            settings[settings_key] = value
    return settings


def get_parallel_processes(settings):
    """Return the configured number of parallel DNS-update workers (default: 1)."""
    value = settings.get("PARALLEL_PROCESSES", DEFAULT_PARALLEL_PROCESSES)
    try:
        value = int(value)
        if value < 1:
            raise ValueError
    except (TypeError, ValueError):
        logger.warning(
            "Invalid PARALLEL_PROCESSES value %r in settings; using default of %d.",
            value,
            DEFAULT_PARALLEL_PROCESSES,
        )
        return DEFAULT_PARALLEL_PROCESSES
    return value


def get_ip_mode(settings):
    """Return the configured IP update mode: 'ipv4', 'ipv6' or 'both' (default)."""
    value = str(settings.get("IP_MODE") or DEFAULT_IP_MODE).lower()
    if value not in VALID_IP_MODES:
        logger.warning(
            "Invalid IP_MODE %r in settings; using default of '%s'.",
            value,
            DEFAULT_IP_MODE,
        )
        return DEFAULT_IP_MODE
    return value


def check_endpoint_reachable(url, timeout=5):
    """Return True if a HEAD request to url succeeds, False otherwise."""
    try:
        requests.head(url, timeout=timeout)
        return True
    except requests.exceptions.RequestException:
        return False


def nginx_trusted_proxies_configuration(nextcloud_path, trusted_proxies_pos, ipv6):
    subprocess.run(
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
    subprocess.run(["systemctl", "restart", "nginx"], check=True)
    logger.info("Trusted proxy at position %s set to %s", trusted_proxies_pos, ipv6)
    logger.info("nginx restarted")


def format_update_summary(updated_records):
    if not updated_records:
        return "No DNS records were processed."

    records_by_domain = {}
    for record in updated_records:
        records_by_domain.setdefault(record["domain"], []).append(record)

    lines = []
    for domain, records in records_by_domain.items():
        lines.append(domain)
        for record in records:
            if "\033" in record["destination"]:
                lines.append(
                    f"  - {record['subdomain']} [{record['record_type']}] -> {record['destination']}"
                )
            else:
                lines.append(
                    "  - {subdomain:<12} {record_type:<5} -> {destination}".format(**record)
                )
    return "\n".join(lines)


def split_domain(domain_str):
    """Split a configured domain entry into (subdomain, domain), mirroring the
    logic used by process_subdomain. Falls back to (domain_str, domain_str)
    for entries without a subdomain part."""
    split = domain_str.split(".")
    if len(split) < 2:
        return domain_str, domain_str
    return split[0], ".".join(split[1:])


def build_exhausted_domain_entry(domain_str, max_retries=MAX_SUBDOMAIN_RETRIES):
    """Build a summary entry warning the admin that a subdomain has kept
    failing to update after exhausting its retry budget."""
    subdomain, domain = split_domain(domain_str)
    return {
        "domain": domain,
        "subdomain": subdomain,
        "record_type": "A/AAAA",
        "destination": (
            f"{RED_COLOR}CONFIG CHECK NEEDED - still failing after "
            f"{max_retries} attempts{RESET_COLOR}"
        ),
    }


def process_subdomain(domain_str, settings, IPv4, IPv6):
    """Processes a single subdomain. Safe to run in parallel threads."""
    RED = "\033[91m"
    RESET = "\033[0m"

    API_PASSWORD = settings["API_PASSWORD"]
    API_KEY = settings["API_KEY"]
    CUSTOMER_ID = settings["CUSTOMER_ID"]
    ip_mode = get_ip_mode(settings)

    results = []
    split = domain_str.split(".")
    if len(split) < 2:
        logger.error("Invalid domain format: %s", domain_str)
        return [{"domain": domain_str, "subdomain": domain_str, "record_type": "A/AAAA",
                 "destination": f"{RED}INVALID FORMAT{RESET}"}], 2

    SUBDOMAIN, DOMAIN = split[0], ".".join(split[1:])

    loginRequest = {
        "action": "login",
        "param": {
            "customernumber": CUSTOMER_ID,
            "apikey": API_KEY,
            "apipassword": API_PASSWORD,
        },
    }

    try:
        loginResponse = requests.post(url=NETCUP_API, json=loginRequest).json()
    except Exception as e:
        logger.error("HTTP Error during login for %s: %s", domain_str, e)
        return [{"domain": DOMAIN, "subdomain": SUBDOMAIN, "record_type": "A/AAAA",
                 "destination": f"{RED}LOGIN FAILED{RESET}"}], 2

    if loginResponse.get("status") != "success":
        logger.error("Could not login at netcup API server for %s", domain_str)
        return [{"domain": DOMAIN, "subdomain": SUBDOMAIN, "record_type": "A/AAAA",
                 "destination": f"{RED}LOGIN REFUSED{RESET}"}], 2

    apiSessionId = loginResponse["responsedata"]["apisessionid"]

    try:
        infoDnsRecordsRequest = {
            "action": "infoDnsRecords",
            "param": {
                "domainname": DOMAIN,
                "customernumber": CUSTOMER_ID,
                "apikey": API_KEY,
                "apisessionid": apiSessionId,
            },
        }

        infoDnsRecordsResponse = requests.post(url=NETCUP_API, json=infoDnsRecordsRequest).json()
        if infoDnsRecordsResponse.get("status") != "success":
            logger.error("Could not retrieve DNS records for %s", DOMAIN)
            return [{"domain": DOMAIN, "subdomain": SUBDOMAIN, "record_type": "A/AAAA",
                     "destination": f"{RED}FETCH RECORDS FAILED{RESET}"}], 2

        dnsRecords = infoDnsRecordsResponse["responsedata"]["dnsrecords"]

        a_updated = False
        aaaa_updated = False

        for item in dnsRecords:
            if item["hostname"] != SUBDOMAIN:
                continue

            if item["type"] == "A" and ip_mode in ("both", "ipv4") and IPv4 is not None:
                updateDnsRecordsRequest = {
                    "action": "updateDnsRecords",
                    "param": {
                        "domainname": DOMAIN,
                        "customernumber": CUSTOMER_ID,
                        "apikey": API_KEY,
                        "apisessionid": apiSessionId,
                        "dnsrecordset": {
                            "dnsrecords": [
                                {
                                    "id": item["id"],
                                    "hostname": SUBDOMAIN,
                                    "type": item["type"],
                                    "destination": IPv4,
                                }
                            ]
                        },
                    },
                }
                try:
                    updateDnsRecordsResponse = requests.post(url=NETCUP_API, json=updateDnsRecordsRequest).json()
                    if updateDnsRecordsResponse.get("status") != "success":
                        logger.error("Could not update A record for %s.%s", SUBDOMAIN, DOMAIN)
                        results.append({"domain": DOMAIN, "subdomain": SUBDOMAIN, "record_type": "A",
                                        "destination": f"{RED}UPDATE FAILED{RESET}"})
                    else:
                        results.append(
                            {"domain": DOMAIN, "subdomain": SUBDOMAIN, "record_type": "A", "destination": IPv4})
                except Exception as e:
                    logger.error("Error updating A record for %s.%s: %s", SUBDOMAIN, DOMAIN, e)
                    results.append({"domain": DOMAIN, "subdomain": SUBDOMAIN, "record_type": "A",
                                    "destination": f"{RED}HTTP ERROR{RESET}"})
                a_updated = True

            if item["type"] == "AAAA" and ip_mode in ("both", "ipv6") and IPv6 is not None:
                updateDnsRecordsRequest = {
                    "action": "updateDnsRecords",
                    "param": {
                        "domainname": DOMAIN,
                        "customernumber": CUSTOMER_ID,
                        "apikey": API_KEY,
                        "apisessionid": apiSessionId,
                        "dnsrecordset": {
                            "dnsrecords": [
                                {
                                    "id": item["id"],
                                    "hostname": SUBDOMAIN,
                                    "type": item["type"],
                                    "destination": IPv6,
                                }
                            ]
                        },
                    },
                }
                try:
                    updateDnsRecordsResponse = requests.post(url=NETCUP_API, json=updateDnsRecordsRequest).json()
                    if updateDnsRecordsResponse.get("status") != "success":
                        logger.error("Could not update AAAA record for %s.%s", SUBDOMAIN, DOMAIN)
                        results.append({"domain": DOMAIN, "subdomain": SUBDOMAIN, "record_type": "AAAA",
                                        "destination": f"{RED}UPDATE FAILED{RESET}"})
                    else:
                        results.append(
                            {"domain": DOMAIN, "subdomain": SUBDOMAIN, "record_type": "AAAA", "destination": IPv6})
                except Exception as e:
                    logger.error("Error updating AAAA record for %s.%s: %s", SUBDOMAIN, DOMAIN, e)
                    results.append({"domain": DOMAIN, "subdomain": SUBDOMAIN, "record_type": "AAAA",
                                    "destination": f"{RED}HTTP ERROR{RESET}"})
                aaaa_updated = True

        # Fallback falls Einträge nicht existieren
        if not a_updated and ip_mode in ("both", "ipv4"):
            results.append({"domain": DOMAIN, "subdomain": SUBDOMAIN, "record_type": "A",
                            "destination": f"{RED}RECORD NOT FOUND{RESET}"})
        if not aaaa_updated and ip_mode in ("both", "ipv6"):
            results.append({"domain": DOMAIN, "subdomain": SUBDOMAIN, "record_type": "AAAA",
                            "destination": f"{RED}RECORD NOT FOUND{RESET}"})

        return results, 2

    except Exception as ex:
        logger.error("Unexpected error processing %s: %s", domain_str, ex)
        return [{"domain": DOMAIN, "subdomain": SUBDOMAIN, "record_type": "A/AAAA",
                 "destination": f"{RED}UNEXPECTED ERROR{RESET}"}], 2
    finally:
        logoutRequest = {
            "action": "logout",
            "param": {
                "customernumber": CUSTOMER_ID,
                "apikey": API_KEY,
                "apisessionid": apiSessionId,
            },
        }
        try:
            requests.post(url=NETCUP_API, json=logoutRequest)
        except Exception:
            pass


def main(argv=None):
    args = parse_cli_args(argv if argv is not None else [])

    create_settings_file_if_not_exists(settings_file_path, default_settings)
    cached_ipv4, cached_ipv6 = read_cached_ips()
    failed_domains = read_failed_domains()

    with open(conf) as fp:
        settings = json.load(fp)
        settings = apply_secret_overrides(settings)
        settings = apply_cli_overrides(settings, args)
        validate_settings(settings)

    IPv4 = requests.get(url=IPV4_API).json()["ip"]
    logger.info("Current public IPv4 address: %s", IPv4)

    try:
        IPv6 = requests.get(url=IPV6_API).json()["ip"]
        logger.info("Current public IPv6 address: %s", IPv6)
    except requests.exceptions.RequestException as e:
        IPv6 = None
        logger.warning("No IPv6 address found. IPv6 cache will not be written: %s", e)

    ip_changed = not (IPv4 == cached_ipv4 and IPv6 == cached_ipv6)
    pending_retry_domains = {
        domain for domain, count in failed_domains.items() if count < MAX_SUBDOMAIN_RETRIES
    }
    exhausted_domains = sorted(
        domain for domain, count in failed_domains.items() if count >= MAX_SUBDOMAIN_RETRIES
    )

    if not ip_changed and not pending_retry_domains:
        logger.info("IP addresses have not changed. No update necessary.")
        if exhausted_domains:
            logger.warning(
                "%d subdomain(s) are still failing to update. Summary:\n%s",
                len(exhausted_domains),
                format_update_summary(
                    [build_exhausted_domain_entry(domain) for domain in exhausted_domains]
                ),
            )
        sys.exit(0)

    if ip_changed:
        write_cached_ips(IPv4, IPv6)
    else:
        logger.info(
            "IP addresses have not changed, but %d previously failed subdomain(s) "
            "will be retried: %s",
            len(pending_retry_domains),
            ", ".join(sorted(pending_retry_domains)),
        )

    try:
        with open(conf) as fp:
            settings = json.load(fp)
            settings = apply_secret_overrides(settings)
            settings = apply_cli_overrides(settings, args)
            try:
                validate_settings(settings)
                NETCUP_DOMAIN = settings["NETCUP_DOMAIN"]
                DISABLE_NEXTCLOUD_NGINX = settings.get("DISABLE_NEXTCLOUD_NGINX", False)

                if not DISABLE_NEXTCLOUD_NGINX:
                    NEXTCLOUD_PATH = settings["NEXTCLOUD_PATH"]
                    TRUSTED_PROXIES_POS = settings["TRUSTED_PROXIES_POS"]
            except KeyError as e:
                logger.error("Key %s is missing in .settings.json file.", e)
                sys.exit(1)
    except FileNotFoundError:
        logger.error("%s file not found.", conf)
        sys.exit(1)
    except json.JSONDecodeError:
        logger.error(".settings.json is not a valid JSON document.")
        sys.exit(1)

    if DISABLE_NEXTCLOUD_NGINX:
        logger.info("Nextcloud and Nginx configuration tasks are disabled via settings.")
    else:
        nginx_trusted_proxies_configuration(NEXTCLOUD_PATH, TRUSTED_PROXIES_POS, IPv6)

    configured_domains = [domain.strip() for domain in NETCUP_DOMAIN.split(",")]

    if ip_changed:
        # A real IP change always requires updating every configured domain,
        # including ones that previously failed or even exhausted their
        # retry budget - it's worth giving them another chance.
        domains_list = configured_domains
    else:
        # IP unchanged: only forcibly retry subdomains that previously failed
        # and have not yet exhausted their retry budget. Domains that already
        # exhausted their retries are left alone to avoid wasting API calls
        # until a real IP change happens again.
        domains_list = [
            domain for domain in configured_domains if domain in pending_retry_domains
        ]

    logger.info(
        "Updating DNS records for %d domain(s): %s",
        len(domains_list),
        ", ".join(domains_list),
    )

    total_records = len(domains_list) * 2
    progress_bar = tqdm(
        total=total_records,
        desc="Updating DNS records",
        unit="record",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} records",
    )

    updated_records = []
    domain_had_error = {}
    parallel_workers = get_parallel_processes(settings)

    if parallel_workers > 1:
        logger.info("Running parallel updating using ThreadPoolExecutor with %d workers.", parallel_workers)
        with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
            # Reiche alle Tasks an den ThreadPool ein
            future_to_domain = {
                executor.submit(process_subdomain, domain, settings, IPv4, IPv6): domain
                for domain in domains_list
            }
            # Sobald Threads fertig sind, hole die Ergebnisse ab
            for future in as_completed(future_to_domain):
                domain = future_to_domain[future]
                try:
                    res, count = future.result()
                    updated_records.extend(res)
                    domain_had_error[domain] = any(RED_COLOR in r["destination"] for r in res)
                except Exception as exc:
                    logger.error("%r generated an exception: %s", domain, exc)
                    # Falls der ganze Thread crasht, fügen wir einen Fehlereintrag hinzu
                    updated_records.append({
                        "domain": domain,
                        "subdomain": "Error",
                        "record_type": "A/AAAA",
                        "destination": f"{RED_COLOR}THREAD CRASHED{RESET_COLOR}"
                    })
                    domain_had_error[domain] = True
                progress_bar.update(2)
    else:
        logger.info("Running sequentially (PARALLEL_PROCESSES <= 1).")
        for domain in domains_list:
            res, count = process_subdomain(domain, settings, IPv4, IPv6)
            updated_records.extend(res)
            domain_had_error[domain] = any(RED_COLOR in r["destination"] for r in res)
            progress_bar.update(count)

    progress_bar.close()

    # Update the failure-retry cache: domains that succeeded are cleared,
    # domains that failed again have their counter incremented (capped at
    # MAX_SUBDOMAIN_RETRIES so they don't keep being force-retried forever).
    for domain, had_error in domain_had_error.items():
        if had_error:
            failed_domains[domain] = min(
                failed_domains.get(domain, 0) + 1, MAX_SUBDOMAIN_RETRIES
            )
        else:
            failed_domains.pop(domain, None)

    # Drop stale entries for domains no longer present in NETCUP_DOMAIN.
    failed_domains = {
        domain: count for domain, count in failed_domains.items() if domain in configured_domains
    }
    write_failed_domains(failed_domains)

    exhausted_domains = sorted(
        domain for domain, count in failed_domains.items() if count >= MAX_SUBDOMAIN_RETRIES
    )
    if exhausted_domains:
        updated_records.extend(
            build_exhausted_domain_entry(domain) for domain in exhausted_domains
        )

    logger.info(
        "DNS update process finished. Summary:\n%s",
        format_update_summary(updated_records),
    )


if __name__ == "__main__":
    main(sys.argv[1:])