"""Altruist post-assembly tester."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("altruist-tester")
except PackageNotFoundError:
    __version__ = "0.0.0"
