"""Tests for the MCP server module."""

import json

import pytest

from mcp_plotly.server import (
    _apply_patches,
    _compute_ref,
    _lookup_by_ref,
    _save_metadata,
    mcp,
)


@pytest.mark.asyncio
async def test_server_has_tools():
    """Verify that the expected tools are registered."""
    tools = mcp._tool_manager.list_tools()
    tool_names = {t.name for t in tools}
    assert tool_names == {
        "create_plotly_plot",
        "create_vegalite_plot",
        "create_observable_plot",
        "revise_plot",
    }


class TestComputeRef:
    def test_deterministic(self):
        assert _compute_ref("hello") == _compute_ref("hello")

    def test_different_inputs(self):
        assert _compute_ref("hello") != _compute_ref("world")

    def test_length(self):
        assert len(_compute_ref("anything")) == 12

    def test_hex_chars(self):
        ref = _compute_ref("test")
        assert all(c in "0123456789abcdef" for c in ref)


class TestApplyPatches:
    def test_single_patch(self):
        code = "const x = 1\nPlot.plot({document})"
        result = _apply_patches(code, [["x = 1", "x = 2"]])
        assert result == "const x = 2\nPlot.plot({document})"

    def test_multiple_patches(self):
        code = "Plot.dot(data, {x: 'a', y: 'b'})"
        result = _apply_patches(code, [["dot", "line"], ["'a'", "'x'"], ["'b'", "'y'"]])
        assert result == "Plot.line(data, {x: 'x', y: 'y'})"

    def test_patch_not_found(self):
        with pytest.raises(ValueError, match="not found in code"):
            _apply_patches("hello", [["missing", "replacement"]])

    def test_invalid_patch_format(self):
        with pytest.raises(ValueError, match="must be \\[old, new\\]"):
            _apply_patches("hello", [["only_one_element"]])

    def test_replaces_first_occurrence_only(self):
        code = "aaa"
        result = _apply_patches(code, [["a", "b"]])
        assert result == "baa"


class TestMetadata:
    def test_save_and_lookup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_PLOTLY_OUTPUT_DIR", str(tmp_path))
        output_dir = tmp_path / "20260402_120000_abc123"
        output_dir.mkdir()

        _save_metadata(
            str(output_dir), "observable", "Plot.plot({document})", "aabbccddeeff"
        )

        meta = _lookup_by_ref("aabbccddeeff")
        assert meta is not None
        assert meta["tool_type"] == "observable"
        assert meta["code"] == "Plot.plot({document})"
        assert meta["ref"] == "aabbccddeeff"

    def test_lookup_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_PLOTLY_OUTPUT_DIR", str(tmp_path))
        assert _lookup_by_ref("nonexistent00") is None

    def test_lookup_empty_base_dir(self, tmp_path, monkeypatch):
        nonexistent = tmp_path / "does_not_exist"
        monkeypatch.setenv("MCP_PLOTLY_OUTPUT_DIR", str(nonexistent))
        assert _lookup_by_ref("anything0000") is None

    def test_save_metadata_content(self, tmp_path):
        output_dir = tmp_path / "test_dir"
        output_dir.mkdir()
        _save_metadata(str(output_dir), "vegalite", '{"mark": "bar"}', "112233445566")

        meta_path = output_dir / "_meta.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta == {
            "tool_type": "vegalite",
            "code": '{"mark": "bar"}',
            "ref": "112233445566",
        }
