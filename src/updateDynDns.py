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


# default .sttings.json values
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
    """Group updated DNS records by domain for a concise, readable report.

    ``updated_records`` is a list of dicts with keys: domain, subdomain,
    record_type, destination. Returns a multi-line string such as::

        example.com
          - sub        A     -> 1.2.3.4
          - sub        AAAA  -> ::1
        example.net
          - www        A     -> 1.2.3.4
    """
    if not updated_records:
        return "No DNS records were updated."

    records_by_domain = {}
    for record in updated_records:
        records_by_domain.setdefault(record["domain"], []).append(record)

    lines = []
    for domain, records in records_by_domain.items():
        lines.append(domain)
        for record in records:
            lines.append(
                "  - {subdomain:<12} {record_type:<5} -> {destination}".format(**record)
            )
    return "\n".join(lines)


def _update_dns_record(domain, customer_id, api_key, api_session_id, item, subdomain, destination):
    """Send an updateDnsRecords request for a single A/AAAA record.

    Returns True on success. Logs and returns False on failure.
    """
    updateDnsRecordsRequest = {
        "action": "updateDnsRecords",
        "param": {
            "domainname": domain,
            "customernumber": customer_id,
            "apikey": api_key,
            "apisessionid": api_session_id,
            "dnsrecordset": {
                "dnsrecords": [
                    {
                        "id": item["id"],
                        "hostname": subdomain,
                        "type": item["type"],
                        "destination": destination,
                    }
                ]
            },
        },
    }

    updateDnsRecordsResponse = requests.post(
        url=NETCUP_API, json=updateDnsRecordsRequest
    ).json()
    if updateDnsRecordsResponse["status"] != "success":
        logger.error(
            "Could not update %s record for %s.%s", item["type"], subdomain, domain
        )
        return False
    return True


def _update_domain_dns_records(
    domain, ip_mode, IPv4, IPv6, API_PASSWORD, API_KEY, CUSTOMER_ID
):
    """Log in, look up, and update the A/AAAA records for a single configured domain entry.

    Runs as a unit of work in the parallel worker pool: each domain entry gets
    its own Netcup API login/logout session, so sessions never need to be
    shared across threads. Returns a list of updated-record dicts on success,
    or None if a step failed (the failure is already logged).
    """
    split = domain.split(".")
    SUBDOMAIN, DOMAIN = split[0], ".".join(split[1:])

    loginRequest = {
        "action": "login",
        "param": {
            "customernumber": CUSTOMER_ID,
            "apikey": API_KEY,
            "apipassword": API_PASSWORD,
        },
    }

    loginResponse = requests.post(url=NETCUP_API, json=loginRequest).json()
    if loginResponse["status"] != "success":
        logger.error("Could not login at netcup API server for %s", domain)
        return None

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
        if infoDnsRecordsResponse["status"] != "success":
            logger.error("Could not retrieve DNS records for %s", DOMAIN)
            return None

        dnsRecords = infoDnsRecordsResponse["responsedata"]["dnsrecords"]

        updated_records = []
        for item in dnsRecords:
            if item["hostname"] != SUBDOMAIN:
                continue

            if item["type"] == "A" and ip_mode in ("both", "ipv4") and IPv4 is not None:
                if not _update_dns_record(
                    DOMAIN, CUSTOMER_ID, API_KEY, apiSessionId, item, SUBDOMAIN, IPv4
                ):
                    return None
                updated_records.append(
                    {
                        "domain": DOMAIN,
                        "subdomain": SUBDOMAIN,
                        "record_type": "A",
                        "destination": IPv4,
                    }
                )

            if item["type"] == "AAAA" and ip_mode in ("both", "ipv6") and IPv6 is not None:
                if not _update_dns_record(
                    DOMAIN, CUSTOMER_ID, API_KEY, apiSessionId, item, SUBDOMAIN, IPv6
                ):
                    return None
                updated_records.append(
                    {
                        "domain": DOMAIN,
                        "subdomain": SUBDOMAIN,
                        "record_type": "AAAA",
                        "destination": IPv6,
                    }
                )

        return updated_records
    finally:
        logoutRequest = {
            "action": "logout",
            "param": {
                "customernumber": CUSTOMER_ID,
                "apikey": API_KEY,
                "apisessionid": apiSessionId,
            },
        }
        logoutResponse = requests.post(url=NETCUP_API, json=logoutRequest).json()
        if logoutResponse["status"] != "success":
            # Updates (if any) already succeeded; a failed logout only leaves
            # a stale Netcup session behind, so it is logged but non-fatal.
            logger.error("Could not log out from netcup API server for %s", domain)


def main():
    # Create the .settings.json file if it doesn't exist
    create_settings_file_if_not_exists(settings_file_path, default_settings)

    # Read cached IPs
    cached_ipv4, cached_ipv6 = read_cached_ips()

    with open(conf) as fp:
        settings = json.load(fp)
        validate_settings(settings)

    # Get public IPv4 address
    IPv4 = requests.get(url=IPV4_API).json()["ip"]
    logger.info("Current public IPv4 address: %s", IPv4)

    # Get public IPv6 address
    try:
        IPv6 = requests.get(url=IPV6_API).json()["ip"]
        logger.info("Current public IPv6 address: %s", IPv6)
    except requests.exceptions.RequestException as e:
        IPv6 = None
        logger.warning("No IPv6 address found. IPv6 cache will not be written: %s", e)

    # Check if IPs have changed
    if IPv4 == cached_ipv4 and IPv6 == cached_ipv6:
        logger.info("IP addresses have not changed. No update necessary.")
        sys.exit(0)

    # Save new IPs to cache
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

    # Each subdomain read from NETCUP_DOMAIN has an A and an AAAA record, so
    # the progress bar reaches 100% once both record types have been updated
    # for every configured subdomain.
    total_records = len(domains_list) * 2
    progress_bar = tqdm(
        total=total_records,
        desc="Updating DNS records",
        unit="record",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} records",
    )

    domain_dict = {}
    updated_records = []
    try:
        for domain in domains_list:
            split = domain.split(".")

            SUBDOMAIN, DOMAIN = split[0], ".".join(split[1:])

            if DOMAIN not in domain_dict:
                domain_dict[DOMAIN] = [SUBDOMAIN]
            else:
                domain_dict[DOMAIN].append(SUBDOMAIN)

            subdomain_list = domain_dict[DOMAIN]

            # Login request
            loginRequest = {
                "action": "login",
                "param": {
                    "customernumber": CUSTOMER_ID,
                    "apikey": API_KEY,
                    "apipassword": API_PASSWORD,
                },
            }

            # Login to Netcup API
            loginResponse = requests.post(url=NETCUP_API, json=loginRequest).json()
            if loginResponse["status"] != "success":
                logger.error("Could not login at netcup API server")
                sys.exit(1)

            apiSessionId = loginResponse["responsedata"]["apisessionid"]

            # InfoDnsRecords Request
            infoDnsRecordsRequest = {
                "action": "infoDnsRecords",
                "param": {
                    "domainname": DOMAIN,
                    "customernumber": CUSTOMER_ID,
                    "apikey": API_KEY,
                    "apisessionid": apiSessionId,
                },
            }

            # Request DNS records for the specified domain
            infoDnsRecordsResponse = requests.post(
                url=NETCUP_API, json=infoDnsRecordsRequest
            ).json()
            if infoDnsRecordsResponse["status"] != "success":
                logger.error("Could not retrieve DNS records for %s", DOMAIN)
                sys.exit(1)

            dnsRecords = infoDnsRecordsResponse["responsedata"]["dnsrecords"]

            # Search for the specify subdomain
            for index, item in enumerate(dnsRecords):
                if item["hostname"] in subdomain_list:
                    hostname = item["hostname"]
                    if item["type"] == "A":
                        # Extract information
                        recordId = item["id"]
                        recordType = item["type"]

                        # UpdateDnsRecord Request
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
                                            "id": recordId,
                                            "hostname": SUBDOMAIN,
                                            "type": recordType,
                                            "destination": IPv4,
                                        }
                                    ]
                                },
                            },
                        }

                        # Update DNS record
                        updateDnsRecordsResponse = requests.post(
                            url=NETCUP_API, json=updateDnsRecordsRequest
                        ).json()
                        if updateDnsRecordsResponse["status"] != "success":
                            logger.error(
                                "Could not update A record for %s.%s", hostname, DOMAIN
                            )
                            sys.exit(1)
                        updated_records.append(
                            {
                                "domain": DOMAIN,
                                "subdomain": hostname,
                                "record_type": "A",
                                "destination": IPv4,
                            }
                        )
                        progress_bar.update(1)

                    if item["type"] == "AAAA":
                        # Extract information
                        recordId = item["id"]
                        recordType = item["type"]

                        # Update AAAA record
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
                                            "id": recordId,
                                            "hostname": SUBDOMAIN,
                                            "type": recordType,
                                            "destination": IPv6,
                                        }
                                    ]
                                },
                            },
                        }

                        # Update DNS record
                        updateDnsRecordsResponse = requests.post(
                            url=NETCUP_API, json=updateDnsRecordsRequest
                        ).json()
                        if updateDnsRecordsResponse["status"] != "success":
                            logger.error(
                                "Could not update AAAA record for %s.%s", hostname, DOMAIN
                            )
                            sys.exit(1)
                        updated_records.append(
                            {
                                "domain": DOMAIN,
                                "subdomain": hostname,
                                "record_type": "AAAA",
                                "destination": IPv6,
                            }
                        )
                        progress_bar.update(1)
                        subdomain_list.remove(SUBDOMAIN)

    finally:
        progress_bar.close()

    logoutRequest = {
        "action": "logout",
        "param": {
            "customernumber": CUSTOMER_ID,
            "apikey": API_KEY,
            "apisessionid": apiSessionId,
        },
    }

    logoutResponse = requests.post(url=NETCUP_API, json=logoutRequest).json()
    if logoutResponse["status"] != "success":
        logger.error("Could not log out from netcup API server")
        sys.exit(1)

    logger.info(
        "Successfully updated %d DNS record(s):\n%s",
        len(updated_records),
        format_update_summary(updated_records),
    )


if __name__ == "__main__":
    main()
