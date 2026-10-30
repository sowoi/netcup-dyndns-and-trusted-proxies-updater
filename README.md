# netcup-dyndns-and-trusted-proxies-updater
<!-- TOC -->
* [netcup-dyndns-and-trusted-proxies-updater](#netcup-dyndns-and-trusted-proxies-updater)
  * [Prerequisites](#prerequisites)
  * [Installation](#installation)
  * [Updating](#updating)
  * [Command-Line Arguments](#command-line-arguments)
  * [Docker Installation (alternative)](#docker-installation-alternative)
  * [Providing Secrets at Runtime](#providing-secrets-at-runtime)
  * [Login Limits and ntfy Notifications](#login-limits-and-ntfy-notifications)
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
This script needs Python 3.13 or newer and is tested on Python 3.13, 3.14 and 3.15.

## Installation

Install the PyPI project with your preferred tool:
```
pipx install netcup-dyndns-and-trusted-proxies-updater
uv tool install netcup-dyndns-and-trusted-proxies-updater
pip install netcup-dyndns-and-trusted-proxies-updater
```

## Updating

Update to the latest release with the same tool you used for the installation:
```
pipx upgrade netcup-dyndns-and-trusted-proxies-updater
uv tool upgrade netcup-dyndns-and-trusted-proxies-updater
pip install --upgrade netcup-dyndns-and-trusted-proxies-updater
```

To install a specific version instead, for example to roll back:
```
pipx install --force netcup-dyndns-and-trusted-proxies-updater==1.4.1
uv tool install --force netcup-dyndns-and-trusted-proxies-updater==1.4.1
pip install netcup-dyndns-and-trusted-proxies-updater==1.4.1
```

Confirm the installed version afterwards with `netcup-dyndns --version`. Updating
does not touch your settings file or cache directory. See [CHANGELOG.md](CHANGELOG.md)
for the changes in each release.

If you run the script from a cloned repository, update it with:
```
git pull
uv sync
```

## Using an Installed Tool

All three installation methods provide the `netcup-dyndns` command. Confirm the
installed version and repository URL with:
```
netcup-dyndns --version
```

By default, the command creates `.settings.json` and `.temp` in the current
working directory. First inspect the paths it will use:
```
netcup-dyndns --show-paths
```

Run it once to create the settings file, edit the generated file with your
credentials and domains, then run the updater again:
```
netcup-dyndns
```

For a service, cron job, or any other setup where the current directory is not
stable, specify absolute paths explicitly:
```
netcup-dyndns \
  --settings-file /etc/netcup-dyndns/settings.json \
  --cache-dir /var/lib/netcup-dyndns
```
Use the same `--settings-file` and `--cache-dir` values for every run. The
cache directory stores the last observed IP addresses, retry state and login backoff.

You can also clone this repository to run it from source:
```
git clone <repository-url>
cd netcup-dyndns-and-trusted-proxies-updater
uv run src/netcup-dyndns.py
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
    "DISABLE_NEXTCLOUD_NGINX": false,
    "RATE_LIMIT_BACKOFF_MINUTES": [10, 30, 60],
    "NTFY_SERVER": "https://ntfy.sh",
    "NTFY_TOPIC": "",
    "NTFY_TOKEN": "",
    "NTFY_NOTIFY_IP_CHANGE": false,
    "NTFY_NOTIFY_FAILURES": false,
    "CREATE_MISSING_RECORDS": false
}
```

API_PASSWORD: Your Netcup API password.  
API_KEY: Your Netcup API key.  
CUSTOMER_ID: Your Netcup customer ID.  
NETCUP_DOMAIN: The host name(s) you want to update, separated by commas (e.g., sub.example.com, example.net). See [Domain Entries](#domain-entries).  
NEXTCLOUD_PATH: The file path to your Nextcloud instance (required unless DISABLE_NEXTCLOUD_NGINX is true).
TRUSTED_PROXIES_POS: The position in the TrustedProxies configuration where the new IP address should be added (e.g., the first, second, etc.; required unless DISABLE_NEXTCLOUD_NGINX is true).
PARALLEL_PROCESSES: Number of parallel threads for DNS updates (Default: 1 for sequential execution. Values > 1 enable the ThreadPoolExecutor).
IP_MODE: Determines which IP types to update. Options are "both" (default), "ipv4", or "ipv6".
DISABLE_NEXTCLOUD_NGINX: Set to true to disable all Nextcloud OCC and Nginx reload tasks. Useful if you only want to use the script as a pure DynDNS client (Default: false).
RATE_LIMIT_BACKOFF_MINUTES: Minimum waiting times in minutes between login attempts after netcup refused a login because of too many logins (Default: [10, 30, 60]). See [Login Limits and ntfy Notifications](#login-limits-and-ntfy-notifications).
NTFY_SERVER: The ntfy server used for notifications (Default: https://ntfy.sh).
NTFY_TOPIC: The ntfy topic that receives a notification when the login is still refused after the last backoff step. Leave empty to disable notifications (Default: empty).
NTFY_TOKEN: Optional ntfy access token for protected topics, sent as `Authorization: Bearer <token>` (Default: empty).
NTFY_NOTIFY_IP_CHANGE: Send a ntfy notification when the public IP address changed, with the old and new addresses and whether all DNS records were updated (Default: false).
NTFY_NOTIFY_FAILURES: Send a ntfy notification once when a domain still fails after all retries (Default: false). See [Notifications](#notifications).
CREATE_MISSING_RECORDS: Create A/AAAA records that do not exist yet for a configured host instead of reporting `RECORD NOT FOUND` (Default: false).

Boolean settings also accept the strings `"true"`/`"false"` (as well as `"1"`/`"0"`,
`"yes"`/`"no"`, `"on"`/`"off"`), e.g. when they come from a secret file.

Settings files created by an earlier version do not contain the newer keys, such as
`RATE_LIMIT_BACKOFF_MINUTES`, `NTFY_*` and `CREATE_MISSING_RECORDS`. The defaults apply
until you add them.

## Command-Line Arguments

Every setting can also be provided as a command-line argument. Command-line arguments
always take precedence over `.settings.json` (and any secret provider overrides, see
[Providing Secrets at Runtime](#providing-secrets-at-runtime)), which is useful for
one-off overrides or wiring the script into other tooling without editing the settings
file. Run with `--help` to see all available options:

```
netcup-dyndns --help
```

```
options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
  -v, --verbose         Show debug output, e.g. every netcup API request and
                        the found records.
  -q, --quiet           Show only warnings and errors. The summary is still
                        shown if a record failed.
  --dry-run             Show what would be updated without changing DNS
                        records, the Nextcloud configuration, the cache or
                        sending notifications. Logs in to netcup to read the
                        current records.
  --health-check [MAX_AGE_SECONDS]
                        Exit with 0 if the last successful run finished at
                        most MAX_AGE_SECONDS ago (default: 900), 1 otherwise,
                        without updating anything. Used as Docker HEALTHCHECK.
  --settings-file PATH  Path to the settings JSON file. Defaults to
                        .settings.json.
  --cache-dir PATH      Directory for temporary IP and retry cache files.
                        Defaults to .temp.
  --show-paths          Show the resolved settings-file and cache-directory
                        paths, then exit.
  --force               Force a DNS update of all domains, ignoring the cached
                        IPv4/IPv6 addresses.
  --api-password API_PASSWORD
                        Netcup API password. Overrides API_PASSWORD.
  --api-key API_KEY     Netcup API key. Overrides API_KEY.
  --customer-id CUSTOMER_ID
                        Netcup customer ID. Overrides CUSTOMER_ID.
  --netcup-domain NETCUP_DOMAIN
                        Comma-separated domain(s) to update, e.g.
                        'example.com,example.net'. Overrides NETCUP_DOMAIN.
  --nextcloud-path NEXTCLOUD_PATH
                        Path to the Nextcloud installation. Overrides
                        NEXTCLOUD_PATH.
  --trusted-proxies-pos TRUSTED_PROXIES_POS
                        Position in the trusted_proxies configuration to
                        update. Overrides TRUSTED_PROXIES_POS.
  --parallel-processes PARALLEL_PROCESSES
                        Number of parallel DNS-update workers. Overrides
                        PARALLEL_PROCESSES.
  --ip-mode {both,ipv4,ipv6}
                        Which IP types to update: 'both' (default), 'ipv4', or
                        'ipv6'. Overrides IP_MODE.
  --disable-nextcloud-nginx, --no-disable-nextcloud-nginx
                        Disable the Nextcloud OCC / Nginx reload tasks (use
                        --no-disable-nextcloud-nginx to force-enable them).
                        Overrides DISABLE_NEXTCLOUD_NGINX.
  --rate-limit-backoff-minutes MINUTES
                        Comma-separated minimum waiting times in minutes
                        between login attempts after netcup refused a login
                        because of too many logins, e.g. '10,30,60'. Overrides
                        RATE_LIMIT_BACKOFF_MINUTES.
  --ntfy-server NTFY_SERVER
                        ntfy server URL for notifications. Overrides
                        NTFY_SERVER.
  --ntfy-topic NTFY_TOPIC
                        ntfy topic to notify; empty disables notifications.
                        Overrides NTFY_TOPIC.
  --ntfy-token NTFY_TOKEN
                        ntfy access token for protected topics. Overrides
                        NTFY_TOKEN.
  --ntfy-notify-ip-change, --no-ntfy-notify-ip-change
                        Send a ntfy notification when the public IP address
                        changed. Overrides NTFY_NOTIFY_IP_CHANGE.
  --ntfy-notify-failures, --no-ntfy-notify-failures
                        Send a ntfy notification when a domain still fails
                        after all retries. Overrides NTFY_NOTIFY_FAILURES.
  --create-missing-records, --no-create-missing-records
                        Create missing A/AAAA records instead of reporting
                        them. Overrides CREATE_MISSING_RECORDS.
```

Example, overriding just the domain for a single run without touching `.settings.json`:
```
netcup-dyndns --netcup-domain example.com,example.net
```

### Dry Run

`--dry-run` shows what a run would change without changing anything:
```
netcup-dyndns --dry-run
netcup-dyndns --dry-run --force    # every domain, even if the IP did not change
```
A dry run determines the public addresses, logs in to netcup and reads the DNS records,
then lists every record as `(unchanged)`, `(would update)` or `(would create)`. It does
not send updates to netcup, does not run OCC or reload nginx, does not write the
settings file or any cache file, and does not send notifications. It follows the same
rules as a normal run, so without `--force` it exits early when the IP did not change.

### Log Output

`-v`/`--verbose` adds debug output, such as the paths in use, every netcup API session
and the records found in each zone. `-q`/`--quiet` shows only warnings and errors. A
successful run is then silent, while a run with failed records still prints the summary
(as a warning), which suits cron jobs that mail any output.

## Docker Installation (alternative)

Instead of installing `uv` and Python locally, you can run this project as a lightweight
Docker container. The provided `Docker/Dockerfile` uses a multi-stage build: `uv` and dependency
resolution happen only in a throw-away build stage, and the final runtime image is based
on `python:3.15-slim` with just the resolved virtual environment and application code —
no `uv`, compilers, or other build tools are present in the image you actually run.

1. Create and fill in your `.settings.json` file (see [Configuration](#configuration) for
   the available keys), then make sure it is readable by the container:
   ```
   chmod 644 .settings.json
   ```

2. Build and start the container with Docker Compose:
   ```
   docker compose -f Docker/docker-compose.yml up -d --build
   ```
   This mounts `.settings.json` read-write into the container and persists the cached IP
   addresses in a named volume (`dyndns-cache`) across restarts. By default, the container
   checks for IP changes every `UPDATE_INTERVAL_SECONDS` (300s/5 minutes); adjust this in
   `Docker/docker-compose.yml` as needed.

   Alternatively, to trigger a single run (e.g. from a host cron job or systemd timer)
   instead of running continuously:
   ```
   docker compose -f Docker/docker-compose.yml run --rm -e RUN_ONCE=true netcup-dyndns-updater
   ```

3. **Health check:** The image defines a Docker `HEALTHCHECK`. Every successful run
   records its time in `last_success.txt` in the cache directory, and the container is
   reported healthy while the last successful run is at most three update intervals
   (`UPDATE_INTERVAL_SECONDS`, 15 minutes by default) old. Failed runs, invalid
   settings and runs skipped because of the netcup login backoff do not count. Check
   it with `docker ps` or:
   ```
   docker inspect --format '{{.State.Health.Status}}' netcup-dyndns-updater
   ```
   Outside Docker, `netcup-dyndns --health-check [MAX_AGE_SECONDS]` performs the same
   check (default: 900 seconds) and exits with 0 or 1.

4. **Nextcloud/Nginx integration caveat:** `nginx_trusted_proxies_configuration` shells out
   to `sudo`, `php occ`, and `systemctl reload nginx` on the host. These are not available
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
configure everything directly in `.settings.json`. Command-line arguments (see
[Command-Line Arguments](#command-line-arguments)) are applied after both, and therefore
always take precedence over secret provider values as well.

## Login Limits and ntfy Notifications

The netcup API blocks logins for a while when there have been too many of them. The
script recognizes such a block when the login

* fails with a refused connection (`Connection refused`),
* is answered with HTTP status `429 Too Many Requests`, or
* is rejected with a netcup error message about too many logins or a reached limit.

In that case, the script logs a `Connection Refused` error, stops logging in for the
remaining domains of the run, and waits before the next attempt. With the default
`RATE_LIMIT_BACKOFF_MINUTES` of `[10, 30, 60]`:

| Refused login | Next attempt no sooner than |
|---------------|-----------------------------|
| 1st           | 10 minutes later            |
| 2nd           | 30 minutes later            |
| 3rd           | 60 minutes later            |
| 4th and later | 60 minutes later, and a ntfy notification is sent after the 4th |

Runs that start before the next attempt is due (for example from a cron job every five
minutes or the Docker loop) exit immediately without contacting any API. The waiting
times are minimums: the attempt happens on the first run after the waiting time has
passed. Once a login succeeds again, every configured domain is updated, the backoff is
reset, and, if a notification was sent, a second notification reports that logins work
again. Refused logins do not count against the per-domain retry budget.

Notifications are sent only if `NTFY_TOPIC` is set. To use them:

1. Pick a hard-to-guess topic name on [ntfy.sh](https://ntfy.sh) (or on your own ntfy
   server) and subscribe to it in the ntfy app.
2. Set `NTFY_TOPIC` in `.settings.json`. Set `NTFY_SERVER` for a self-hosted server and
   `NTFY_TOKEN` if the topic requires authentication.

### Notifications

Besides the login-limit notification, two optional notifications use the same ntfy
topic:

* `NTFY_NOTIFY_IP_CHANGE`: after the public IP address changed, a notification lists
  the old and new addresses and whether all DNS records were updated. Runs with
  `--force` or retries without an IP change do not send it.
* `NTFY_NOTIFY_FAILURES`: when a domain fails for the fifth time in a row (and is no
  longer retried until the next IP change), a notification names it. It is sent once
  per domain, not on every following run.

`NTFY_TOKEN` can also be provided at runtime as described in
[Providing Secrets at Runtime](#providing-secrets-at-runtime), e.g. via `NTFY_TOKEN_FILE`.

The backoff state is stored in `rate_limit.json` in the cache directory. Running with
`--force` attempts a login even while the backoff is active; deleting `rate_limit.json`
resets the backoff completely.

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
On a terminal, errors are logged in red (ERROR) so failures stand out immediately. When
the output is not a terminal (cron mails, `docker logs`, systemd journal) or the
[`NO_COLOR`](https://no-color.org) environment variable is set, no ANSI color codes are
written and the progress bar is hidden.

The public addresses are determined via ipify, with icanhazip as fallback. Every answer
is validated as an IPv4 or IPv6 address before it is used. All HTTP requests time out
after 10 seconds. Only the address types needed for `IP_MODE` are looked up; the IPv6
address is also looked up while the Nextcloud integration is enabled, since the trusted
proxy is an IPv6 address.

Each run proceeds in this order:

1. Update the DNS records. All entries of one DNS zone share a single netcup API
   session (one login, one record lookup, one update request), which keeps the number
   of logins low. Records that already point at the current address are not sent to
   netcup and are listed as `(unchanged)` in the summary.
2. Save the new IP addresses to the cache.
3. Write the IPv6 address to the Nextcloud `trusted_proxies` configuration and reload
   nginx, but only if the IPv6 address differs from the one last applied (or with
   `--force`). If this step fails, the DNS update is kept and the step is retried on
   the next run. Without a public IPv6 address, the step is skipped with a warning.

**Exit codes:** The script exits with `0` when all records were updated (or nothing had
to be done, including runs skipped because of the login backoff). It exits with `1`
when the settings are missing or invalid, no public IP address could be determined, a
DNS record could not be updated (including refused logins and domains that exhausted
their retries), or the trusted-proxies update failed.

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
As long as required values are empty, each run exits with status 1 and names the
missing value.

The API identifiers for Netcup (API key, password, and customer ID) must be configured in the settings.json file.
In the NETCUP_DOMAIN field, list all desired domain names separated by commas (e.g., example.com, example.net).
NEXTCLOUD_PATH should point to the directory where your Nextcloud instance is located.
TRUSTED_PROXIES_POS specifies the position in the TrustedProxies configuration where the new IP address should be inserted.

### Domain Entries

Every entry in NETCUP_DOMAIN is split into a host name and a DNS zone (the domain in
your netcup account). By default, the first label is the host name and the rest is the
zone; an entry with only two labels updates the zone apex (`@`):

| Entry               | Host name | Zone            |
|---------------------|-----------|-----------------|
| `sub.example.com`   | `sub`     | `example.com`   |
| `www.example.co.uk` | `www`     | `example.co.uk` |
| `example.com`       | `@`       | `example.com`   |

For host names with more than one label, and for the apex of zones with more than two
labels, write the entry as `host@zone` (or `@zone` for the apex):

| Entry               | Host name | Zone            |
|---------------------|-----------|-----------------|
| `a.b@example.com`   | `a.b`     | `example.com`   |
| `@example.co.uk`    | `@`       | `example.co.uk` |

## Contributing

When bumping the version in `pyproject.toml`, add a matching `## [x.y.z] - YYYY-MM-DD`
section to [CHANGELOG.md](CHANGELOG.md). After the pull request is merged into `main`,
that section is published as the notes of the GitHub release.

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
