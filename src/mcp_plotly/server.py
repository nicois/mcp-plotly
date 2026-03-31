"""MCP server for generating visualizations in isolated containers."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_plotly.container import ensure_image, run_plot, shutdown
from mcp_plotly.js_container import (
    ensure_js_image,
    run_observable,
    run_vegalite,
    shutdown_js,
)
from mcp_plotly.pool import PlotResult, file_location

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
    """Build both container images at startup."""
    logger.info("Building container images...")
    await ensure_image()
    await ensure_js_image()
    logger.info("Container images ready")
    try:
        yield
    finally:
        logger.info("Shutting down container pools...")
        await shutdown()
        await shutdown_js()
        logger.info("Container pools shut down")


mcp = FastMCP(
    "mcp-plotly",
    instructions=(
        "Generate data visualizations in isolated containers. "
        "Tools return URLs to generated image files (PNG, SVG). "
        "After a successful plot, you MUST display the PNG or SVG image inline to the user "
        "using markdown: ![Plot description](THE_URL_FROM_THE_RESULT). "
        "Extract the image URL from the tool result and embed it. "
        "NEVER show just a link or raw URL — the user expects to see the chart directly."
    ),
    lifespan=lifespan,
)


class OutputFormat(str, Enum):
    png = "png"
    html = "html"
    both = "both"


class JsOutputFormat(str, Enum):
    svg = "svg"
    png = "png"
    both = "both"


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _format_result(result: PlotResult, tool_name: str = "Plot") -> str:
    """Format a successful or failed PlotResult into a tool response string."""
    if not result.success:
        logger.error("%s generation failed (stderr=%s)", tool_name, result.stderr)
    if result.success and result.files:
        format_names = {
            "png": "PNG image",
            "html": "Interactive HTML",
            "svg": "SVG image",
        }
        file_lines = []
        for f in result.files:
            path = Path(f)
            suffix = path.suffix.lstrip(".")
            fmt = format_names.get(suffix, suffix)
            size = _format_size(path.stat().st_size)
            file_lines.append(f"  - {file_location(f)} ({fmt}, {size})")
        return (
            "Plot generated successfully.\n\n"
            "Files:\n" + "\n".join(file_lines) + "\n\n"
            f"Output directory: {file_location(result.output_dir)}"
        )

    parts = ["Plot generation failed."]
    if result.stderr:
        parts.append(f"\nStderr:\n{result.stderr}")
    if result.stdout:
        parts.append(f"\nStdout:\n{result.stdout}")
    return "\n".join(parts)


@mcp.tool()
async def create_plotly_plot(
    code: str,
    output_format: OutputFormat = OutputFormat.both,
    timeout: int = 60,
) -> str:
    """Create a Plotly visualization by running Python code in an isolated container.

    PREFER create_observable_plot for most visualizations. Only use this tool when you
    need Python-specific data processing (pandas, numpy, scipy, scikit-learn) or Plotly-specific
    features like interactive HTML output or 3D plots.

    The code should create a Plotly figure assigned to a variable named `fig`.
    The following are already imported into your namespace: plotly.graph_objects (as go),
    plotly.express (as px), pandas (as pd), numpy (as np).

    The server will automatically export the figure in the requested format(s).
    The container has no network access; data must be inline.

    Do not include comments.

    Available packages: plotly, kaleido, pandas, numpy, scipy, scikit-learn.

    Args:
        code: Python code that creates a Plotly figure assigned to `fig`.
        output_format: Output format - "png", "html", or "both" (default: "both").
        timeout: Maximum execution time in seconds (default: 60).

    Returns:
        URLs of generated files. Display images inline with ![description](url).
    """
    logger.info("Creating plot (format=%s, timeout=%d)", output_format.value, timeout)
    result: PlotResult = await run_plot(
        code=code,
        output_format=output_format.value,
        timeout=timeout,
    )
    return _format_result(result, "Plotly")


@mcp.tool()
async def create_vegalite_plot(
    spec: str,
    output_format: JsOutputFormat = JsOutputFormat.both,
    timeout: int = 60,
) -> str:
    """Create a Vega-Lite visualization from a JSON specification.

    PREFER create_observable_plot for most visualizations. Only use this tool when you
    have an existing Vega-Lite spec or need Vega-Lite-specific features.

    Provide a complete Vega-Lite JSON spec as a string. The server compiles
    it to Vega, renders it, and exports as SVG and/or PNG.

    Data must be inline (the container has no network access).

    Example spec:
    {"$schema": "https://vega.github.io/schema/vega-lite/v5.json",
     "data": {"values": [{"a": "A", "b": 28}, {"a": "B", "b": 55}]},
     "mark": "bar",
     "encoding": {"x": {"field": "a", "type": "nominal"},
                   "y": {"field": "b", "type": "quantitative"}}}

    Args:
        spec: A Vega-Lite specification as a JSON string.
        output_format: Output format - "svg", "png", or "both" (default: "both").
        timeout: Maximum execution time in seconds (default: 60).

    Returns:
        URLs of generated files. Display images inline with ![description](url).
    """
    logger.info(
        "Creating Vega-Lite plot (format=%s, timeout=%d)", output_format.value, timeout
    )
    try:
        json.loads(spec)
    except json.JSONDecodeError as e:
        return f"Invalid JSON in spec: {e}"

    result: PlotResult = await run_vegalite(
        spec=spec,
        output_format=output_format.value,
        timeout=timeout,
    )
    return _format_result(result, "Vega-Lite")


@mcp.tool()
async def create_observable_plot(
    code: str,
    output_format: JsOutputFormat = JsOutputFormat.both,
    timeout: int = 60,
) -> str:
    """Create an Observable Plot visualization — the DEFAULT and PREFERRED tool for charts.

    Use this tool for all visualizations unless you specifically need Python data processing
    (pandas, numpy, scipy) or Plotly-specific features (interactive HTML, 3D plots).

    RULES:
    - The code MUST contain exactly ONE Plot.plot() call as the final expression.
    - You MUST pass `document` as an option to Plot.plot() for SVG rendering.
    - Do NOT include comments in the code.
    - The container has no network access; all data must be inline.

    IMPORTANT - Observable Plot is a PLOTTING library, not a general-purpose data library.
    It does NOT provide utility functions like range(), linspace(), etc.
    Use plain JavaScript to prepare data (Array.from, map, loops, etc.).

    Available in your namespace: `Plot` (Observable Plot library), `document` (jsdom).
    Plot provides: Plot.plot(), Plot.dot(), Plot.line(), Plot.barY(), Plot.barX(),
    Plot.areaY(), Plot.text(), Plot.ruleX(), Plot.ruleY(), Plot.rectY(), Plot.cell(),
    Plot.hexbin(), and other mark types. See https://observablehq.com/plot/marks

    Note: text measurement is approximate in server-side rendering.

    Single-line example:
    Plot.plot({ document, marks: [Plot.dot([{x: 1, y: 2}, {x: 3, y: 4}], {x: "x", y: "y"})] })

    Multi-line example (note: use plain JS for data, final expression is Plot.plot()):
    const data = Array.from({length: 21}, (_, i) => ({x: i - 10, y: (i - 10) ** 2}))
    Plot.plot({ document, marks: [Plot.line(data, {x: "x", y: "y"})] })

    Args:
        code: JavaScript code whose final expression is a single Plot.plot({document, ...}) call.
        output_format: Output format - "svg", "png", or "both" (default: "both").
        timeout: Maximum execution time in seconds (default: 60).

    Returns:
        URLs of generated files. Display images inline with ![description](url).
    """
    logger.info(
        "Creating Observable plot (format=%s, timeout=%d)", output_format.value, timeout
    )
    result: PlotResult = await run_observable(
        code=code,
        output_format=output_format.value,
        timeout=timeout,
    )
    return _format_result(result, "Observable Plot")


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
