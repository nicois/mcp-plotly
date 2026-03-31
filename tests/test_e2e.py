"""End-to-end tests that run real Plotly code in Podman containers.

These tests require a working Podman installation and will build/pull
container images on first run. They are slow by nature.

Skip with: pytest -m "not e2e"
"""

import asyncio
import os

import pytest

from mcp_plotly.container import ensure_image, run_plot
from mcp_plotly.js_container import ensure_js_image, run_vegalite, run_observable


pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _set_env(tmp_path, monkeypatch):
    """Point output and URL prefix at a temp directory for every test."""
    monkeypatch.setenv("MCP_PLOTLY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_PLOTLY_URL_PREFIX", "https://test.example.com/plots")


@pytest.fixture(scope="module")
def _image_ready():
    """Build the container image once for the entire module."""
    asyncio.run(ensure_image())


@pytest.mark.asyncio
async def test_generate_png(_image_ready):
    """Generate a simple bar chart as PNG."""
    code = 'fig = px.bar(x=["a", "b", "c"], y=[1, 2, 3])'
    result = await run_plot(code, output_format="png", timeout=120)

    assert result.success, f"Plot failed: {result.stderr}"
    assert len(result.files) == 1
    assert result.files[0].endswith(".png")
    assert os.path.getsize(result.files[0]) > 0


@pytest.mark.asyncio
async def test_generate_html(_image_ready):
    """Generate a simple scatter plot as HTML."""
    code = "fig = px.scatter(x=[1, 2, 3, 4], y=[10, 11, 12, 13])"
    result = await run_plot(code, output_format="html", timeout=120)

    assert result.success, f"Plot failed: {result.stderr}"
    assert len(result.files) == 1
    assert result.files[0].endswith(".html")
    with open(result.files[0]) as f:
        content = f.read()
    assert "plotly" in content.lower()


@pytest.mark.asyncio
async def test_generate_both_formats(_image_ready):
    """Generate a plot in both PNG and HTML formats."""
    code = "fig = go.Figure(data=go.Bar(x=[1, 2, 3], y=[4, 5, 6]))"
    result = await run_plot(code, output_format="both", timeout=120)

    assert result.success, f"Plot failed: {result.stderr}"
    assert len(result.files) == 2
    extensions = {os.path.splitext(f)[1] for f in result.files}
    assert extensions == {".html", ".png"}


@pytest.mark.asyncio
async def test_invalid_code_returns_failure(_image_ready):
    """Code that raises an exception should return success=False."""
    code = "raise ValueError('intentional error')"
    result = await run_plot(code, output_format="png", timeout=120)

    assert not result.success
    assert "intentional error" in result.stderr


@pytest.mark.asyncio
async def test_output_dir_is_unique(_image_ready):
    """Each run should write to its own output directory."""
    code = 'fig = px.bar(x=["a"], y=[1])'
    result1 = await run_plot(code, output_format="png", timeout=120)
    result2 = await run_plot(code, output_format="png", timeout=120)

    assert result1.success and result2.success
    assert result1.output_dir != result2.output_dir


@pytest.mark.asyncio
async def test_server_create_plot_tool(_image_ready, monkeypatch):
    """End-to-end through the MCP tool handler."""
    from mcp_plotly.server import create_plotly_plot, OutputFormat

    result = await create_plotly_plot(
        code="fig = px.line(x=[1, 2, 3], y=[3, 1, 2])",
        output_format=OutputFormat.png,
        timeout=120,
    )

    assert "Plot generated successfully" in result
    assert "https://test.example.com/plots/" in result


# --- JS container setup ---


@pytest.fixture(scope="module")
def _js_image_ready():
    """Build the JS container image once for the entire module."""
    asyncio.run(ensure_js_image())


# --- Vega-Lite tests ---


@pytest.mark.asyncio
async def test_vegalite_svg(_js_image_ready):
    """Generate a simple Vega-Lite bar chart as SVG."""
    import json

    spec = json.dumps(
        {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": [{"a": "A", "b": 28}, {"a": "B", "b": 55}]},
            "mark": "bar",
            "encoding": {
                "x": {"field": "a", "type": "nominal"},
                "y": {"field": "b", "type": "quantitative"},
            },
        }
    )
    result = await run_vegalite(spec, output_format="svg", timeout=120)
    assert result.success, f"Vega-Lite failed: {result.stderr}"
    assert len(result.files) == 1
    assert result.files[0].endswith(".svg")
    with open(result.files[0]) as f:
        assert "<svg" in f.read()


@pytest.mark.asyncio
async def test_vegalite_png(_js_image_ready):
    """Generate a Vega-Lite chart as PNG."""
    import json

    spec = json.dumps(
        {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": [{"x": 1, "y": 2}, {"x": 3, "y": 4}]},
            "mark": "point",
            "encoding": {
                "x": {"field": "x", "type": "quantitative"},
                "y": {"field": "y", "type": "quantitative"},
            },
        }
    )
    result = await run_vegalite(spec, output_format="png", timeout=120)
    assert result.success, f"Vega-Lite PNG failed: {result.stderr}"
    assert len(result.files) == 1
    assert result.files[0].endswith(".png")
    assert os.path.getsize(result.files[0]) > 0


@pytest.mark.asyncio
async def test_vegalite_both_formats(_js_image_ready):
    """Generate Vega-Lite chart in both SVG and PNG."""
    import json

    spec = json.dumps(
        {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": [{"a": 1, "b": 2}]},
            "mark": "bar",
            "encoding": {
                "x": {"field": "a", "type": "quantitative"},
                "y": {"field": "b", "type": "quantitative"},
            },
        }
    )
    result = await run_vegalite(spec, output_format="both", timeout=120)
    assert result.success, f"Vega-Lite both failed: {result.stderr}"
    assert len(result.files) == 2
    extensions = {os.path.splitext(f)[1] for f in result.files}
    assert extensions == {".svg", ".png"}


# --- Observable Plot tests ---


@pytest.mark.asyncio
async def test_observable_svg(_js_image_ready):
    """Generate an Observable Plot as SVG."""
    code = 'Plot.plot({ document, marks: [Plot.dot([{x: 1, y: 2}, {x: 3, y: 4}], {x: "x", y: "y"})] })'
    result = await run_observable(code, output_format="svg", timeout=120)
    assert result.success, f"Observable Plot failed: {result.stderr}"
    assert len(result.files) == 1
    assert result.files[0].endswith(".svg")
    with open(result.files[0]) as f:
        assert "<svg" in f.read().lower()


@pytest.mark.asyncio
async def test_observable_png(_js_image_ready):
    """Generate an Observable Plot as PNG."""
    code = (
        'Plot.plot({ document, marks: [Plot.dot([{x: 1, y: 2}], {x: "x", y: "y"})] })'
    )
    result = await run_observable(code, output_format="png", timeout=120)
    assert result.success, f"Observable Plot PNG failed: {result.stderr}"
    assert len(result.files) == 1
    assert result.files[0].endswith(".png")
    assert os.path.getsize(result.files[0]) > 0


@pytest.mark.asyncio
async def test_observable_invalid_code(_js_image_ready):
    """Invalid JS code returns failure."""
    code = "(() => { throw new Error('intentional error'); })()"
    result = await run_observable(code, output_format="svg", timeout=120)
    assert not result.success
    assert "intentional error" in result.stderr


@pytest.mark.asyncio
async def test_vegalite_invalid_spec(_js_image_ready):
    """Valid JSON but invalid Vega-Lite spec returns failure."""
    import json as json_mod

    spec = json_mod.dumps({"mark": "nonexistent_type", "data": {"values": []}})
    result = await run_vegalite(spec, output_format="svg", timeout=120)
    assert not result.success


@pytest.mark.asyncio
async def test_server_observable_tool(_js_image_ready):
    """End-to-end through the MCP tool handler for Observable Plot."""
    from mcp_plotly.server import create_observable_plot, JsOutputFormat

    code = (
        'Plot.plot({ document, marks: [Plot.dot([{x: 1, y: 2}], {x: "x", y: "y"})] })'
    )
    result = await create_observable_plot(
        code=code, output_format=JsOutputFormat.svg, timeout=120
    )
    assert "Plot generated successfully" in result
    assert "https://test.example.com/plots/" in result


@pytest.mark.asyncio
async def test_server_vegalite_tool(_js_image_ready):
    """End-to-end through the MCP tool handler for Vega-Lite."""
    import json as json_mod
    from mcp_plotly.server import create_vegalite_plot, JsOutputFormat

    spec = json_mod.dumps(
        {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": [{"a": "X", "b": 10}]},
            "mark": "bar",
            "encoding": {
                "x": {"field": "a", "type": "nominal"},
                "y": {"field": "b", "type": "quantitative"},
            },
        }
    )
    result = await create_vegalite_plot(
        spec=spec, output_format=JsOutputFormat.svg, timeout=120
    )
    assert "Plot generated successfully" in result
    assert "https://test.example.com/plots/" in result
