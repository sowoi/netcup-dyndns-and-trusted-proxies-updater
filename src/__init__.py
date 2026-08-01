"""Compatibility exports for the hyphenated updater script."""

import sys
from importlib import import_module

updateDynDns = import_module("src.netcup-dyndns")
sys.modules["src.updateDynDns"] = updateDynDns