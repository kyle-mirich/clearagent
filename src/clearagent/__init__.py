from importlib.metadata import PackageNotFoundError, version

from clearagent.create import create_agent
from clearagent.tool import tool

try:
    __version__ = version("clearagent")
except PackageNotFoundError:  # pragma: no cover - only used from an unpackaged source tree
    __version__ = "0+unknown"

__all__ = ["__version__", "create_agent", "tool"]
