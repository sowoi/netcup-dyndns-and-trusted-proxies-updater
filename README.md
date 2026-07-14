# netcup-dyndns-and-trusted-proxies-updater
<!-- TOC -->
* [netcup-dyndns-and-trusted-proxies-updater](#netcup-dyndns-and-trusted-proxies-updater)
  * [Prerequisites](#prerequisites)
  * [Installation](#installation)
  * [Docker Installation (alternative)](#docker-installation-alternative)
  * [Providing Secrets at Runtime](#providing-secrets-at-runtime)
  * [Usage](#usage)
  * [Configuration](#configuration)
  * [Contributing](#contributing)
  * [License](#license)
<!-- TOC -->

This script is designed for users with a dynamic dual-stack address, using Netcup as their provider. 
It automatically updates your IPv6 address in the Trusted Proxy configuration for Nextcloud.

The script checks the current IPv4/IPv6 address of the host and updates the corresponding values at Netcup if necessary.
Additionally, it updates the Trusted Proxies configuration in Nextcloud via the OCC CLI.

## Prerequisites
This script needs [uv](https://github.com/astral-sh/uv).

## Installation

1. Clone this repository to your local machine:
```
git clone <repository-url>
```

2. Run the script using the uv command:
```
uv run src/updateDynDns.py
```
The first run will create a settings.json file and a temp folder in your project directory.
3. Configure the settings.json file with the following parameters:

```Json
{
    "API_PASSWORD": "",
    "API_KEY": "",
    "CUSTOMER_ID": "",
    "NETCUP_DOMAIN": "",
    "NEXTCLOUD_PATH": "",
    "TRUSTED_PROXIES_POS": "",
    "PARALLEL_PROCESSES": 1,
    "IP_MODE": "both",
    "DISABLE_NEXTCLOUD_NGINX": false
}
```

API_PASSWORD: Your Netcup API password.  
API_KEY: Your Netcup API key.  
CUSTOMER_ID: Your Netcup customer ID.  
NETCUP_DOMAIN: The domain name(s) you want to update, separated by commas (e.g., example.com, example.net).  
NEXTCLOUD_PATH: The file path to your Nextcloud instance (required unless DISABLE_NEXTCLOUD_NGINX is true).
TRUSTED_PROXIES_POS: The position in the TrustedProxies configuration where the new IP address should be added (e.g., the first, second, etc.; required unless DISABLE_NEXTCLOUD_NGINX is true).
PARALLEL_PROCESSES: Number of parallel threads for DNS updates (Default: 1 for sequential execution. Values > 1 enable the ThreadPoolExecutor).
IP_MODE: Determines which IP types to update. Options are "both" (default), "ipv4", or "ipv6".
DISABLE_NEXTCLOUD_NGINX: Set to true to disable all Nextcloud OCC and Nginx reload tasks. Useful if you only want to use the script as a pure DynDNS client (Default: false).

## Docker Installation (alternative)

Instead of installing `uv` and Python locally, you can run this project as a lightweight
Docker container. The provided `Dockerfile` uses a multi-stage build: `uv` and dependency
resolution happen only in a throw-away build stage, and the final runtime image is based
on `python:3.13-slim` with just the resolved virtual environment and application code —
no `uv`, compilers, or other build tools are present in the image you actually run.

1. Create and fill in your `.settings.json` file (see [Configuration](#configuration) for
   the available keys), then make sure it is readable by the container:
   ```
   chmod 644 .settings.json
   ```

2. Build and start the container with Docker Compose:
   ```
   docker compose up -d --build
   ```
   This mounts `.settings.json` read-write into the container and persists the cached IP
   addresses in a named volume (`dyndns-cache`) across restarts. By default, the container
   checks for IP changes every `UPDATE_INTERVAL_SECONDS` (300s/5 minutes); adjust this in
   `docker-compose.yml` as needed.

   Alternatively, to trigger a single run (e.g. from a host cron job or systemd timer)
   instead of running continuously:
   ```
   docker compose run --rm -e RUN_ONCE=true netcup-dyndns-updater
   ```

3. **Nextcloud/Nginx integration caveat:** `nginx_trusted_proxies_configuration` shells out
   to `sudo`, `php occ`, and `systemctl restart nginx` on the host. These are not available
   inside the minimal container, so set `DISABLE_NEXTCLOUD_NGINX: true` in `.settings.json`
   when running via Docker unless you specifically bind-mount those host binaries into a
   privileged container (not recommended). Running purely as a DynDNS client works out of
   the box.

## Providing Secrets at Runtime

As an alternative to storing credentials in plain text in `.settings.json`, the script
supports two optional, opt-in mechanisms to override settings at runtime. Both are
disabled unless explicitly configured via environment variables, and neither requires any
additional dependency.

**1. Secret files (Docker/Kubernetes secrets convention)**

Set an environment variable named `<KEY>_FILE` pointing at a file whose contents will be
read (and trimmed) to override the corresponding setting, e.g.:
```
API_PASSWORD_FILE=/run/secrets/api_password
API_KEY_FILE=/run/secrets/api_key
CUSTOMER_ID_FILE=/run/secrets/customer_id
```
This works with Docker Compose `secrets:`, Kubernetes `Secret` volumes, or any tool that
projects a secret's value into a file (including an OpenBAO Agent injector sidecar).

**2. Direct retrieval from an OpenBAO (or Vault-compatible) server**

Configure the following environment variables to have the script fetch a KV v2 secret at
startup and use its values to override matching settings keys (`API_PASSWORD`, `API_KEY`,
`CUSTOMER_ID`, etc.):
```
OPENBAO_ADDR=https://openbao.example.internal:8200
OPENBAO_TOKEN=s.xxxxxxxxxxxxxxxxxxxx      # or OPENBAO_TOKEN_FILE=/run/secrets/openbao_token
OPENBAO_SECRET_PATH=secret/data/netcup-dyndns   # optional, this is the default
```
The secret is expected at `<OPENBAO_ADDR>/v1/<OPENBAO_SECRET_PATH>` in the standard KV v2
response shape (`{"data": {"data": {...}}}`). Only keys already recognized by
`.settings.json` are applied; unknown keys in the secret are ignored.

If both mechanisms are configured, secret-file overrides take precedence over OpenBAO
values, since they are applied last. Neither mechanism is required — you can continue to
configure everything directly in `.settings.json`.

## Usage

To run the script periodically, set up a cron job or a systemd timer. 
This will ensure your IP address is regularly checked and updated.

While updating DNS records, a progress bar is shown. Its total is derived from the
number of subdomains listed in NETCUP_DOMAIN (each subdomain accounts for one A and
one AAAA record), and it advances by one step for every record that is successfully
updated, reaching 100% once all A and AAAA records for every configured subdomain
have been updated:

```
Updating DNS records: 100%|██████████| 4/4 records
```

Regular progress is logged to stdout with timestamps and log levels (INFO/WARNING).
Errors are logged in red (ERROR) so failures stand out immediately.

When multiple domains or subdomains are configured, the script prints a summary at
the end grouped by domain instead of a flat sequential list, e.g.:

```
example.com
  - sub          A     -> 1.2.3.4
  - sub          AAAA  -> ::1
  - www          A     -> 1.2.3.4
example.net
  - app          A     -> 1.2.3.4
```

## Configuration

Upon the first execution, the script creates a settings.json file and a temp folder.

The API identifiers for Netcup (API key, password, and customer ID) must be configured in the settings.json file.
In the NETCUP_DOMAIN field, list all desired domain names separated by commas (e.g., example.com, example.net).
NEXTCLOUD_PATH should point to the directory where your Nextcloud instance is located.
TRUSTED_PROXIES_POS specifies the position in the TrustedProxies configuration where the new IP address should be inserted.

## Contributing

To ensure proper code formatting, run the following command:
```
uv run --dev ruff check
```

To run tests:
```
uv run --dev pytest
```

To run tests with a coverage report:
```
uv run --dev pytest --cov=src --cov-report=term-missing
```

## License
Licensed under the terms of GNU General Public License v3.0. See LICENSE file.
