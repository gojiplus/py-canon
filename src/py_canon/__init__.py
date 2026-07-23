"""py-canon: shared configuration for the fleet's Python packages."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("py-canon")
except PackageNotFoundError:  # pragma: no cover - not installed
    __version__ = "0.0.0"
