# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

When a pull request is merged into `main`, the section matching the version in
`pyproject.toml` is used as the notes of the GitHub release.

## [1.3.0] - 2026-09-25

### Added
- `--force` command-line flag to update the DNS records of all configured domains
  without considering the cached IPv4/IPv6 addresses. This also retries domains
  that have exhausted their retry budget.
- `CHANGELOG.md`, used as the source for GitHub release notes.

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

[1.3.0]: https://github.com/sowoi/netcup-dyndns-and-trusted-proxies-updater/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/sowoi/netcup-dyndns-and-trusted-proxies-updater/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/sowoi/netcup-dyndns-and-trusted-proxies-updater/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/sowoi/netcup-dyndns-and-trusted-proxies-updater/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/sowoi/netcup-dyndns-and-trusted-proxies-updater/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/sowoi/netcup-dyndns-and-trusted-proxies-updater/releases/tag/v0.1.0
