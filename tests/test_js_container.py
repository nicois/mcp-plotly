"""Tests for the JS container module."""

import pytest

from mcp_plotly.js_container import JS_WORKER_SCRIPT


def test_worker_script_contains_vegalite_handler():
    assert "renderVegaLite" in JS_WORKER_SCRIPT


def test_worker_script_contains_observable_handler():
    assert "renderObservable" in JS_WORKER_SCRIPT


@pytest.mark.asyncio
async def test_run_vegalite_rejects_invalid_json():
    from mcp_plotly.js_container import run_vegalite

    with pytest.raises(ValueError):
        await run_vegalite("not valid json")
