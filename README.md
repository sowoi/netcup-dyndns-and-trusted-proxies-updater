# netcup-dyndns-and-trusted-proxies-updater

This script is useful if you have a dynamic dual-stack address, use Netcup as your provider and want to automatically write your IPv6 address to the TrustedProxy configuration.

This script checks the current IPv4/IPv6 address of the host and updates the values at Netcup if necessary.
In addition, the new value is written to the Trusted Proxies in the Nextcloud configuration via the OCC CLI.

## General structure
After the first execution, the script creates a .settings.json file and the .temp folder.
API identifiers for Netrcup must be configured in the settings.json. In the domain list, all desired domain names can be entered as a comma-separated list.
Nextcloud Path is the path of the Nextcloud instance.
The position indicates the position of the desired address in the TrustedProxies configuration.