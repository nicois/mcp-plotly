"""Tests for the MCP server module."""

import pytest

from mcp_plotly.server import mcp


@pytest.mark.asyncio
async def test_server_has_tools():
    """Verify that the expected tools are registered."""
    tools = mcp._tool_manager.list_tools()
    tool_names = {t.name for t in tools}
    assert tool_names == {
        "create_plotly_plot",
        "create_vegalite_plot",
        "create_observable_plot",
    }
