"""Kibitzer — watches agent tool calls and suggests structured alternatives."""

__version__ = "0.2.1"

from kibitzer.session import CallResult, KibitzerSession

__all__ = ["KibitzerSession", "CallResult", "__version__"]
