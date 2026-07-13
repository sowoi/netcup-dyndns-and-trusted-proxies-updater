import logging
import sys
import threading
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


# default .settings.json values
settings_file_path = ".settings.json"
DEFAULT_PARALLEL_PROCESSES = 2
DEFAULT_IP_MODE = "both"
VALID_IP_MODES = {"ipv4", "ipv6", "both"}

default_settings = {
    "API_PASSWORD": "",
    "API_KEY": "",
    "CUSTOMER_ID": "",
    "NETCUP_DOMAIN": "",
    "NEXTCLOUD_PATH": "",
    "TRUSTED_PROXIES_POS": "",
    "PARALLEL_PROCESSES": DEFAULT_PARALLEL_PROCESSES,
    "IP_MODE": DEFAULT_IP_MODE,
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
        ipv4_cache = (cache_path / "ipv4_cache.txt").read_text()
        ipv6_cache = (cache_path / "ipv6_cache.txt").read_text()
    except FileNotFoundError:
        pass
    return ipv4_cache, ipv6_cache


# Function to write IP addresses to cache
def write_cached_ips(ipv4, ipv6=None, cache_dir=cache_dir):
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    (cache_path / "ipv4_cache.txt").write_text(ipv4)
    (cache_path / "ipv6_cache.txt").write_text(ipv6)


# Validates values in settings.json
def validate_settings(settings):
    required_keys = [
        "API_PASSWORD",
        "API_KEY",
        "CUSTOMER_ID",
        "NETCUP_DOMAIN",
        "NEXTCLOUD_PATH",
        "TRUSTED_PROXIES_POS",
    ]
    for key in required_keys:
        if key not in settings:
            raise KeyError(f"The key {key} is missing in the configuration file.")
        if not settings[key]:
            raise ValueError(
                f"The key {key} cannot be empty. Please fill in the missing value in the .settings.json file."
            )


def get_parallel_processes(settings):
    """Return the configured number of parallel DNS-update workers (default: 2)."""
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
                    "  - {subdomain:<12} {record_type:<5} -> {destination}".format(
                        **record
                    )
                )
    return "\n".join(lines)


def main():
    create_settings_file_if_not_exists(settings_file_path, default_settings)
    cached_ipv4, cached_ipv6 = read_cached_ips()

    with open(conf) as fp:
        settings = json.load(fp)
        validate_settings(settings)

    IPv4 = requests.get(url=IPV4_API).json()["ip"]
    logger.info("Current public IPv4 address: %s", IPv4)

    try:
        IPv6 = requests.get(url=IPV6_API).json()["ip"]
        logger.info("Current public IPv6 address: %s", IPv6)
    except requests.exceptions.RequestException as e:
        IPv6 = None
        logger.warning("No IPv6 address found. IPv6 cache will not be written: %s", e)

    if IPv4 == cached_ipv4 and IPv6 == cached_ipv6:
        logger.info("IP addresses have not changed. No update necessary.")
        sys.exit(0)

    write_cached_ips(IPv4, IPv6)

    try:
        with open(conf) as fp:
            settings = json.load(fp)
            try:
                validate_settings(settings)
                API_PASSWORD = settings["API_PASSWORD"]
                API_KEY = settings["API_KEY"]
                CUSTOMER_ID = settings["CUSTOMER_ID"]
                NETCUP_DOMAIN = settings["NETCUP_DOMAIN"]
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

    nginx_trusted_proxies_configuration(NEXTCLOUD_PATH, TRUSTED_PROXIES_POS, IPv6)

    domains_list = [domain.strip() for domain in NETCUP_DOMAIN.split(",")]
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

    domain_dict = {}
    updated_records = []

    RED = "\033[91m"
    RESET = "\033[0m"

    for domain in domains_list:
        try:
            split = domain.split(".")
            if len(split) < 2:
                logger.error("Invalid domain format: %s", domain)
                progress_bar.update(2)
                continue

            SUBDOMAIN, DOMAIN = split[0], ".".join(split[1:])

            domain_dict.setdefault(DOMAIN, []).append(SUBDOMAIN)
            subdomain_list = domain_dict[DOMAIN]

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
                logger.error("HTTP Error during login for %s: %s", domain, e)
                updated_records.append(
                    {
                        "domain": DOMAIN,
                        "subdomain": SUBDOMAIN,
                        "record_type": "A/AAAA",
                        "destination": f"{RED}LOGIN FAILED{RESET}",
                    }
                )
                progress_bar.update(2)
                continue

            if loginResponse.get("status") != "success":
                logger.error("Could not login at netcup API server for %s", domain)
                updated_records.append(
                    {
                        "domain": DOMAIN,
                        "subdomain": SUBDOMAIN,
                        "record_type": "A/AAAA",
                        "destination": f"{RED}LOGIN REFUSED{RESET}",
                    }
                )
                progress_bar.update(2)
                continue

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

                infoDnsRecordsResponse = requests.post(
                    url=NETCUP_API, json=infoDnsRecordsRequest
                ).json()
                if infoDnsRecordsResponse.get("status") != "success":
                    logger.error("Could not retrieve DNS records for %s", DOMAIN)
                    updated_records.append(
                        {
                            "domain": DOMAIN,
                            "subdomain": SUBDOMAIN,
                            "record_type": "A/AAAA",
                            "destination": f"{RED}FETCH RECORDS FAILED{RESET}",
                        }
                    )
                    progress_bar.update(2)
                    continue

                dnsRecords = infoDnsRecordsResponse["responsedata"]["dnsrecords"]

                for item in dnsRecords:
                    if item["hostname"] != SUBDOMAIN:
                        continue

                    if item["type"] == "A":
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

                        updateDnsRecordsResponse = requests.post(
                            url=NETCUP_API, json=updateDnsRecordsRequest
                        ).json()
                        if updateDnsRecordsResponse.get("status") != "success":
                            logger.error(
                                "Could not update A record for %s.%s", SUBDOMAIN, DOMAIN
                            )
                            updated_records.append(
                                {
                                    "domain": DOMAIN,
                                    "subdomain": SUBDOMAIN,
                                    "record_type": "A",
                                    "destination": f"{RED}UPDATE FAILED{RESET}",
                                }
                            )
                        else:
                            updated_records.append(
                                {
                                    "domain": DOMAIN,
                                    "subdomain": SUBDOMAIN,
                                    "record_type": "A",
                                    "destination": IPv4,
                                }
                            )
                        progress_bar.update(1)

                    if item["type"] == "AAAA":
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

                        updateDnsRecordsResponse = requests.post(
                            url=NETCUP_API, json=updateDnsRecordsRequest
                        ).json()
                        if updateDnsRecordsResponse.get("status") != "success":
                            logger.error(
                                "Could not update AAAA record for %s.%s",
                                SUBDOMAIN,
                                DOMAIN,
                            )
                            updated_records.append(
                                {
                                    "domain": DOMAIN,
                                    "subdomain": SUBDOMAIN,
                                    "record_type": "AAAA",
                                    "destination": f"{RED}UPDATE FAILED{RESET}",
                                }
                            )
                        else:
                            updated_records.append(
                                {
                                    "domain": DOMAIN,
                                    "subdomain": SUBDOMAIN,
                                    "record_type": "AAAA",
                                    "destination": IPv6,
                                }
                            )
                        progress_bar.update(1)

            finally:
                logoutRequest = {
                    "action": "logout",
                    "param": {
                        "customernumber": CUSTOMER_ID,
                        "apikey": API_KEY,
                        "apisessionid": apiSessionId,
                    },
                }
                requests.post(url=NETCUP_API, json=logoutRequest)

        except Exception as ex:
            logger.error("Unexpected error processing %s: %s", domain, ex)
            progress_bar.update(2)
            continue

    progress_bar.close()

    logger.info(
        "DNS update process finished. Summary:\n%s",
        format_update_summary(updated_records),
    )


if __name__ == "__main__":
    main()
