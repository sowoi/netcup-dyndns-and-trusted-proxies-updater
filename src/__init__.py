"""Compatibility exports for the hyphenated updater script."""

from importlib import import_module
import sys


updateDynDns = import_module("src.netcup-dyndns")
sys.modules["src.updateDynDns"] = updateDynDns