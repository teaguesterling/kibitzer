"""FastMCP server — delegates to KibitzerSession."""

from __future__ import annotations

import json

from kibitzer.session import KibitzerSession

_session: KibitzerSession | None = None


def _get_session() -> KibitzerSession:
    global _session
    if _session is None:
        _session = KibitzerSession()
        _session.load()
    return _session


def change_tool_mode(mode: str, reason: str | None = None, project_dir=None):
    """For direct Python callers and test compatibility."""
    if project_dir is not None:
        session = KibitzerSession(project_dir=project_dir)
        session.load()
        result = session.change_mode(mode, reason=reason or "")
        session.save()
        return result
    session = _get_session()
    result = session.change_mode(mode, reason=reason or "")
    session.save()
    return result


def get_feedback(status=True, suggestions=True, intercepts=True, project_dir=None):
    """For direct Python callers and test compatibility."""
    if project_dir is not None:
        session = KibitzerSession(project_dir=project_dir)
        session.load()
        return session.get_feedback(status, suggestions, intercepts)
    session = _get_session()
    return session.get_feedback(status, suggestions, intercepts)


def create_mcp_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "kibitzer",
        instructions=(
            "Kibitzer watches your tool calls and suggests structured alternatives. "
            "Use ChangeToolMode to switch between modes (free, implement, test, "
            "docs, explore, review). Use GetFeedback to check status, "
            "get coaching suggestions, and see intercepted patterns."
        ),
    )

    @mcp.tool()
    def ChangeToolMode(mode: str, reason: str = "") -> str:
        """Switch kibitzer mode to change which file paths are writable.

        Args:
            mode: Target mode (free, implement, test, docs, explore, review)
            reason: Optional reason for the switch
        """
        result = change_tool_mode(mode, reason=reason)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def GetFeedback(
        status: bool = True,
        suggestions: bool = True,
        intercepts: bool = True,
    ) -> str:
        """Get kibitzer feedback: current status, coaching suggestions, and intercepted patterns."""
        result = get_feedback(status=status, suggestions=suggestions, intercepts=intercepts)
        return json.dumps(result, indent=2)

    return mcp
