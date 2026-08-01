"""Import adapter for the hyphenated executable script."""

from importlib import import_module
import sys


main = import_module("src.netcup-dyndns").main


def cli():
    """Run the updater with command-line arguments from the installed command."""
    main(sys.argv[1:])
