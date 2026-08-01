"""Import adapter for the hyphenated executable script."""

import sys
from importlib import import_module

_implementation = import_module("src.netcup-dyndns")
main = _implementation.main


def __getattr__(name):
    return getattr(_implementation, name)


def cli():
    """Run the updater with command-line arguments from the installed command."""
    main(sys.argv[1:])
