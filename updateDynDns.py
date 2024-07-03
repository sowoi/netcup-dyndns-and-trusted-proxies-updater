import sys
import requests
import json
import os
import subprocess

conf = '.settings.json'
# URLs to APIs
NETCUP_API = "https://ccp.netcup.net/run/webservice/servers/endpoint.php?JSON"
IPV4_API = "https://api.ipify.org?format=json"
IPV6_API = "https://api6.ipify.org?format=json"


# default .sttings.json values
settings_file_path = '.settings.json'
default_settings = {
    "API_PASSWORD": "",
    "API_KEY": "",
    "CUSTOMER_ID": "",
    "NETCUP_DOMAIN": "",
    "NEXTCLOUD_PATH": "",
    "TRUSTED_PROXIES_POS": ""
}

def create_settings_file_if_not_exists(file_path, default_content):
    if not os.path.exists(file_path):
        with open(file_path, 'w') as f:
            json.dump(default_content, f, indent=4)
        print(f"Settings file created at {file_path}")
    else:
        print(f"Settings file already exists at {file_path}")

# Create the .settings.json file if it doesn't exist
create_settings_file_if_not_exists(settings_file_path, default_settings)


# Function to read IP addresses from cache
def read_cached_ips():
    ipv4_cache, ipv6_cache = None, None
    try:
        with open('.temp/ipv4_cache.txt', 'r') as f:
            ipv4_cache = f.read().strip()
        with open('.temp/ipv6_cache.txt', 'r') as f:
            ipv6_cache = f.read().strip()
    except FileNotFoundError:
        pass
    return ipv4_cache, ipv6_cache

# Function to write IP addresses to cache
def write_cached_ips(ipv4, ipv6=None):
    os.makedirs('.temp', exist_ok=True)
    with open('.temp/ipv4_cache.txt', 'w') as f:
        f.write(ipv4)
    with open('.temp/ipv6_cache.txt', 'w') as f:
        f.write(ipv6)

# Read cached IPs
cached_ipv4, cached_ipv6 = read_cached_ips()


# Validates values in settings.json
def validate_settings(settings):
    required_keys = ["API_PASSWORD", "API_KEY", "CUSTOMER_ID", "NETCUP_DOMAIN", "NEXTCLOUD_PATH", "TRUSTED_PROXIES_POS"]
    for key in required_keys:
        if key not in settings:
            raise KeyError(f"The key {key} is missing in the configuration file.")
        if not settings[key]:
            raise ValueError(f"The key {key} cannot be empty. Please fill in the missing value in the .settings.json file.")

with open(conf) as fp:
    settings = json.load(fp)


# Get public IPv4 address
IPv4 = requests.get(url=IPV4_API).json()["ip"]
print("IPv4 address: " + IPv4)

# Get public IPv6 address
try:
    IPv6 = requests.get(url=IPV6_API).json()["ip"]
    print("IPv6 address: " + IPv6)
except:
    IPv6 = None
    print("Warning: No IPv6 address found. IPv6 cache will not be written.")
    
# Check if IPs have changed
if IPv4 == cached_ipv4 and IPv6 == cached_ipv6:
    print("IP addresses have not changed. No update necessary.")
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
            print(f"Error: Key {e} is missing in .settings.json file.")
except FileNotFoundError:
    print(f"Error: {conf} file not found.")
except json.JSONDecodeError:
    print("Error: .settings.json is not a valid JSON document.")

subprocess.run(["sudo", "-u", "www-data", "php", f"{NEXTCLOUD_PATH}/occ", "config:system:set", "trusted_proxies", TRUSTED_PROXIES_POS, f"--value={IPv6}"], check=True\
)
subprocess.run(["systemctl", "restart", "nginx"], check=True)


domains_list = [domain.strip() for domain in NETCUP_DOMAIN.split(',')]

domain_dict = {}
for domain in domains_list:
  # Extract subdomain to update
  print(f"Updating DNS record for {domain}")

  split = domain.split(".")

  SUBDOMAIN, DOMAIN = split[0], '.'.join(split[1:])

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
        "apipassword": API_PASSWORD
    }
  }

  # Login to Netcup API
  loginResponse = requests.post(url=NETCUP_API, json=loginRequest).json()
  if loginResponse["status"] != "success":
    print("Could not login at netcup API server")
    exit(1)

  apiSessionId = loginResponse["responsedata"]["apisessionid"]

  # InfoDnsRecords Request
  infoDnsRecordsRequest = {
    "action": "infoDnsRecords",
    "param": {
        "domainname": DOMAIN,
        "customernumber": CUSTOMER_ID,
        "apikey": API_KEY,
        "apisessionid": apiSessionId
    }
  }

# Request DNS records for the specified domain
  infoDnsRecordsResponse = requests.post(url=NETCUP_API, json=infoDnsRecordsRequest).json()
  if infoDnsRecordsResponse["status"] != "success":
    print("Could not retrieve DNS records")
    exit(1)

  dnsRecords = infoDnsRecordsResponse["responsedata"]["dnsrecords"]

  # Search for the specify subdomain
  for index, item in enumerate(dnsRecords):
    if item["hostname"] in subdomain_list:
        hostname = item["hostname"]
        print(f"found {hostname} in subrecord list")
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
                        "dnsrecords": [{
                            "id": recordId,
                            "hostname": SUBDOMAIN,
                            "type": recordType,
                            "destination": IPv4
                        }]
                    }
                }
            }


            # Update DNS record
            updateDnsRecordsResponse = requests.post(url=NETCUP_API, json=updateDnsRecordsRequest).json()
            if updateDnsRecordsResponse["status"] != "success":
                print("Could not update IPv4 DNS record..")
                exit(1)

        if item["type"] == "AAAA":
            # Extract information
            recordId = item["id"]
            recordType = item["type"]

            # Update A record
            updateDnsRecordsRequest = {
                "action": "updateDnsRecords",
                "param": {
                    "domainname": DOMAIN,
                    "customernumber": CUSTOMER_ID,
                    "apikey": API_KEY,
                    "apisessionid": apiSessionId,
                    "dnsrecordset": {
                        "dnsrecords": [{
                            "id": recordId,
                            "hostname": SUBDOMAIN,
                            "type": recordType,
                            "destination": IPv6
                        }]
                    }
                }
            }

            print("Updating IPv6 record..")

            # Update DNS record
            updateDnsRecordsResponse = requests.post(url=NETCUP_API, json=updateDnsRecordsRequest).json()
            if updateDnsRecordsResponse["status"] != "success":
                print("Could not update IPv6 DNS record..")
                exit(1)

            print("Updating IPv6 record..")

             # Update DNS record

            # Update DNS record
            updateDnsRecordsResponse = requests.post(url=NETCUP_API, json=updateDnsRecordsRequest).json()
            if updateDnsRecordsResponse["status"] != "success":
                print("Could not update IPv6 DNS record..")
                exit(1)
            subdomain_list.remove(SUBDOMAIN)
            print(f"removing {SUBDOMAIN} from subdomain list")

logoutRequest = {
    "action": "logout",
    "param": {
        "customernumber": CUSTOMER_ID, 
        "apikey": API_KEY, 
         "apisessionid": apiSessionId
    }
}

logoutResponse = requests.post(url=NETCUP_API, json=logoutRequest).json()
if logoutResponse["status"] != "success":
    print("Could not log out from netcup API server")
    exit(1)

print("Successfully updated DNS record for '" + SUBDOMAIN + "." + DOMAIN + "'")
