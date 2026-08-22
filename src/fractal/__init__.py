"""Fractal continuous-improvement system."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("fractal-system")
except PackageNotFoundError:
    __version__ = "0.1.0a1"

SYSTEM_VERSION = "0.1.0-alpha.1"

__all__ = ["SYSTEM_VERSION", "__version__"]
