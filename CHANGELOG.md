# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

When a pull request is merged into `main`, the section matching the version in
`pyproject.toml` is used as the notes of the GitHub release.

## [1.5.0] - 2026-10-30

### Added
- Support for Python 3.15; CI now tests on Python 3.13, 3.14 and 3.15.
- Zone apex: an entry with two labels such as `example.com` updates the `@` records.
- Explicit `host@zone` entries for multi-label host names and the apex of zones with
  more than two labels, e.g. `a.b@example.com` or `@example.co.uk`. Entries such as
  `sub.example.com` and `www.example.co.uk` are split as before.
- Fallback IP provider (icanhazip) if ipify does not answer; every answer is validated
  as an IPv4/IPv6 address.
- Exit code 1 when a DNS record or the trusted-proxies update failed, no public IP
  address could be determined, or the settings are invalid.
- `trusted_proxy.txt` in the cache directory records the IPv6 address last written to
  the Nextcloud trusted proxies.

### Changed
- One netcup API session per DNS zone instead of one per subdomain, and A and AAAA
  records are sent in a single update request. This reduces the number of logins and
  the risk of hitting the netcup login limit.
- Records that already point at the current address are no longer sent to netcup.
- DNS records are updated before the Nextcloud trusted proxies; the IP cache is written
  after the DNS update.
- The trusted proxies are updated only when the IPv6 address changed since it was last
  applied (or with `--force`), no longer on every retry run.
- nginx is reloaded (`systemctl reload nginx`) instead of restarted.
- `IP_MODE` also controls which addresses are looked up: `ipv6` mode no longer queries
  (or depends on) the public IPv4 address.
- No ANSI color codes and no progress bar when the output is not a terminal or
  `NO_COLOR` is set.
- The settings file is read once per run (and OpenBAO queried once).
- All files are read and written as UTF-8 explicitly.
- Docker image based on Python 3.15 (Debian trixie) and uv 0.13.
- Updated dependencies and tooling: setuptools >= 84.2.1 (build), pytest 9.2.0, ruff 0.17.3, virtualenv 21.15.1,
  platformdirs 4.14.0 and others; GitHub Actions `actions/checkout@v7` and
  `astral-sh/setup-uv@v10`; pre-commit hooks uv 0.13.6 and ruff 0.17.3.

### Fixed
- DNS records were never updated after an IP change if the OCC call or the nginx
  restart failed, because the new IPs were already cached and the run aborted.
- Without a public IPv6 address, `None` was written to the Nextcloud trusted proxies.
- HTTP requests to netcup and the IP providers had no timeout and could hang forever.
- A failing IPv4 lookup crashed the run.
- Missing or invalid settings (for example on the first run) ended with a traceback
  instead of an error message.
- `--version` and every run of the Docker image crashed because the image contained
  neither `pyproject.toml` nor package metadata.

## [1.4.1] - 2026-10-26

### Changed
- Updated locked dependencies (ruff 0.17.2, pre-commit 4.7.0, pytest-mock 3.17.0,
  certifi 2026.10.23 and others).

## [1.4.0] - 2026-09-30

### Added
- Detection of login limits of the netcup API (refused connection, HTTP 429, or an
  error message about too many logins). A `Connection Refused` error is logged and
  the run stops logging in for the remaining domains.
- Login backoff: the next attempt takes place no sooner than 10, then 30, then 60
  minutes later, configurable via `RATE_LIMIT_BACKOFF_MINUTES`. Runs inside the
  waiting time exit without contacting any API; `--force` ignores the backoff.
- ntfy notification when the login is still refused after the last backoff step, and
  an all-clear notification once logins work again. Configured via `NTFY_SERVER`,
  `NTFY_TOPIC` and `NTFY_TOKEN` (secret-file and OpenBAO overrides supported).
- `--rate-limit-backoff-minutes`, `--ntfy-server`, `--ntfy-topic` and `--ntfy-token`
  command-line options.
- Tests for login-limit detection, the backoff schedule and ntfy notifications.

### Changed
- Bumped dev dependencies: pre-commit >= 4.6.2, ruff >= 0.16.9.

## [1.3.0] - 2026-09-25

### Added
- `--force` command-line flag to update the DNS records of all configured domains
  without considering the cached IPv4/IPv6 addresses. This also retries domains
  that have exhausted their retry budget.
- `CHANGELOG.md`, used as the source for GitHub release notes.
- README section on updating via pipx, uv and pip.

### Changed
- `uv.lock` is now tracked in the repository (required by the Docker build).
- Bumped dev dependencies: pre-commit >= 4.6.0, pytest-mock >= 3.15.1, ruff >= 0.15.21.

## [1.2.1] - 2026-08-01

### Added
- `--settings-file` and `--cache-dir` command-line options to configure the
  settings-file and cache-directory paths.
- `--show-paths` command-line option to print the resolved paths and exit.

## [1.2.0] - 2026-08-01

### Added
- Publish workflow to PyPI with trusted publishing and automatic GitHub releases.
- Dependabot updates for the uv package ecosystem.

### Changed
- Moved Docker assets to the `Docker` directory.
- Bumped dependencies: requests >= 2.34.2, tqdm >= 4.70.0, setuptools >= 83.0.0,
  pytest >= 9.1.1, pytest-cov >= 7.1.0.

## [1.1.0] - 2026-07-14

### Added
- Command-line arguments that override values from `.settings.json`.
- Retry logic for subdomains that failed to update.
- Option to disable the Nextcloud and Nginx configuration tasks.
- Dockerfile and secret provider configuration.

## [1.0.0] - 2026-07-13

### Changed
- Modernized the code base and refactored the Nginx configuration into a function.
- Replaced `os` with `pathlib`.

### Added
- Test suite, including tests for the IP cache.

### Fixed
- Request error handling.

## [0.1.0] - 2025-03-27

### Added
- Initial release: netcup DynDNS updater with Nextcloud trusted-proxies update.
- Settings validation before API requests.
- uv-based project setup with pytest and ruff CI checks.

[1.4.1]: https://github.com/sowoi/netcup-dyndns-and-trusted-proxies-updater/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/sowoi/netcup-dyndns-and-trusted-proxies-updater/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/sowoi/netcup-dyndns-and-trusted-proxies-updater/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/sowoi/netcup-dyndns-and-trusted-proxies-updater/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/sowoi/netcup-dyndns-and-trusted-proxies-updater/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/sowoi/netcup-dyndns-and-trusted-proxies-updater/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/sowoi/netcup-dyndns-and-trusted-proxies-updater/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/sowoi/netcup-dyndns-and-trusted-proxies-updater/releases/tag/v0.1.0
