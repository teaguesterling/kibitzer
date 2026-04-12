"""Kibitzer — watches agent tool calls and suggests structured alternatives."""

__version__ = "0.3.2"

from kibitzer.failure_modes import ALL_MODES as FAILURE_MODES
from kibitzer.session import CallResult, KibitzerSession

__all__ = ["KibitzerSession", "CallResult", "FAILURE_MODES", "__version__"]
