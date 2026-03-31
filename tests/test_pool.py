"""Tests for the container pool module."""

import pytest

from mcp_plotly.pool import file_location


class TestFileLocation:
    def test_returns_url(self, monkeypatch):
        monkeypatch.setenv("MCP_PLOTLY_OUTPUT_DIR", "/data/plots")
        monkeypatch.setenv("MCP_PLOTLY_URL_PREFIX", "https://example.com/plots")
        path = "/data/plots/20260331_143022_a1b2c3/plot.png"
        assert (
            file_location(path)
            == "https://example.com/plots/20260331_143022_a1b2c3/plot.png"
        )

    def test_trailing_slash_on_prefix_normalized(self, monkeypatch):
        monkeypatch.setenv("MCP_PLOTLY_OUTPUT_DIR", "/data/plots")
        monkeypatch.setenv("MCP_PLOTLY_URL_PREFIX", "https://example.com/plots/")
        path = "/data/plots/run1/plot.html"
        assert file_location(path) == "https://example.com/plots/run1/plot.html"

    def test_unrelated_path_returned_unchanged(self, monkeypatch):
        monkeypatch.setenv("MCP_PLOTLY_OUTPUT_DIR", "/data/plots")
        monkeypatch.setenv("MCP_PLOTLY_URL_PREFIX", "https://example.com/plots")
        path = "/tmp/other/file.png"
        assert file_location(path) == path

    def test_raises_when_prefix_not_set(self, monkeypatch):
        monkeypatch.delenv("MCP_PLOTLY_URL_PREFIX", raising=False)
        with pytest.raises(RuntimeError, match="MCP_PLOTLY_URL_PREFIX"):
            file_location("/some/path")
