# netcup-dyndns-and-trusted-proxies-updater
<!-- TOC -->
* [netcup-dyndns-and-trusted-proxies-updater](#netcup-dyndns-and-trusted-proxies-updater)
  * [Prerequisites](#prerequisites)
  * [Installation](#installation)
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
    "TRUSTED_PROXIES_POS": ""
}
```

API_PASSWORD: Your Netcup API password.  
API_KEY: Your Netcup API key.  
CUSTOMER_ID: Your Netcup customer ID.  
NETCUP_DOMAIN: The domain name(s) you want to update, separated by commas (e.g., example.com, example.net).  
NEXTCLOUD_PATH: The file path to your Nextcloud instance. 
TRUSTED_PROXIES_POS: The position in the TrustedProxies configuration where the new IP address should be added (e.g., the first, second, etc.).  

## Usage

To run the script periodically, set up a cron job or a systemd timer. 
This will ensure your IP address is regularly checked and updated.

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

## License
Licensed under the terms of GNU General Public License v3.0. See LICENSE file.
