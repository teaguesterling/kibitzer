"""Build the interceptor registry based on tool availability."""

from __future__ import annotations
import shutil
from kibitzer.interceptors.base import BaseInterceptor
from kibitzer.interceptors.blq import BlqInterceptor
from kibitzer.interceptors.jetsam import JetsamInterceptor
from kibitzer.interceptors.squackit import SquackitInterceptor

_PLUGINS: list[tuple[str, type[BaseInterceptor]]] = [
    ("blq", BlqInterceptor),
    ("jetsam", JetsamInterceptor),
    # squackit is the first-line code-search wrapper; gated on `which squackit`.
    ("squackit", SquackitInterceptor),
]


def build_registry() -> list[BaseInterceptor]:
    available = []
    for tool_name, plugin_cls in _PLUGINS:
        if shutil.which(tool_name) is not None:
            available.append(plugin_cls())
    return available
