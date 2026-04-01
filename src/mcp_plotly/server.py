"""MCP server for generating visualizations in isolated containers."""

import hashlib
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
from mcp_plotly.pool import PlotResult, file_location, get_output_base_dir

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
        "Tools return URLs to generated image files (PNG). "
        "After a successful plot, you MUST display the PNG image inline to the user "
        "using markdown: ![Plot description](THE_URL_FROM_THE_RESULT). "
        "Extract the image URL from the tool result and embed it. "
        "NEVER show just a link or raw URL — the user expects to see the chart directly. "
        "Each result includes a Reference ID. To fix or modify a plot, use revise_plot "
        "with the reference and the complete updated code instead of re-calling the original tool."
    ),
    lifespan=lifespan,
)


class OutputFormat(str, Enum):
    png = "png"
    html = "html"
    both = "both"


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _compute_ref(code: str) -> str:
    """Return first 12 hex chars of sha256 of the code string."""
    return hashlib.sha256(code.encode()).hexdigest()[:12]


def _save_metadata(output_dir: str, tool_type: str, code: str, ref: str) -> None:
    """Write _meta.json into the output directory."""
    meta_path = Path(output_dir) / "_meta.json"
    meta_path.write_text(json.dumps({"tool_type": tool_type, "code": code, "ref": ref}))


def _lookup_by_ref(ref: str) -> dict | None:
    """Scan output directories for a _meta.json matching the given ref."""
    base_dir = get_output_base_dir()
    if not base_dir.exists():
        return None
    for entry in sorted(base_dir.iterdir(), reverse=True):
        meta_path = entry / "_meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                if meta.get("ref") == ref:
                    return meta
            except (json.JSONDecodeError, OSError):
                continue
    return None


def _format_result(
    result: PlotResult, tool_name: str = "Plot", ref: str | None = None
) -> str:
    """Format a successful or failed PlotResult into a tool response string."""
    if not result.success:
        logger.error("%s generation failed (stderr=%s)", tool_name, result.stderr)
    if result.success and result.files:
        format_names = {
            "png": "PNG image",
            "html": "Interactive HTML",
        }
        file_lines = []
        for f in result.files:
            path = Path(f)
            suffix = path.suffix.lstrip(".")
            fmt = format_names.get(suffix, suffix)
            size = _format_size(path.stat().st_size)
            file_lines.append(f"  - {file_location(f)} ({fmt}, {size})")
        ref_line = f"\nReference: {ref}" if ref else ""
        return (
            "Plot generated successfully.\n\n"
            "Files:\n" + "\n".join(file_lines) + "\n\n"
            f"Output directory: {file_location(result.output_dir)}" + ref_line
        )

    parts = ["Plot generation failed."]
    if ref:
        parts.append(f"\nReference: {ref}")
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
    ref = _compute_ref(code)
    _save_metadata(result.output_dir, "plotly", code, ref)
    return _format_result(result, "Plotly", ref=ref)


@mcp.tool()
async def create_vegalite_plot(
    spec: str,
    timeout: int = 60,
) -> str:
    """Create a Vega-Lite visualization from a JSON specification.

    PREFER create_observable_plot for most visualizations. Only use this tool when you
    have an existing Vega-Lite spec or need Vega-Lite-specific features.

    Provide a complete Vega-Lite JSON spec as a string. The server compiles
    it to Vega, renders it, and exports as PNG.

    Data must be inline (the container has no network access).

    Example spec:
    {"$schema": "https://vega.github.io/schema/vega-lite/v5.json",
     "data": {"values": [{"a": "A", "b": 28}, {"a": "B", "b": 55}]},
     "mark": "bar",
     "encoding": {"x": {"field": "a", "type": "nominal"},
                   "y": {"field": "b", "type": "quantitative"}}}

    Args:
        spec: A Vega-Lite specification as a JSON string.
        timeout: Maximum execution time in seconds (default: 60).

    Returns:
        URLs of generated files. Display images inline with ![description](url).
    """
    logger.info("Creating Vega-Lite plot (timeout=%d)", timeout)
    try:
        json.loads(spec)
    except json.JSONDecodeError as e:
        return f"Invalid JSON in spec: {e}"

    result: PlotResult = await run_vegalite(
        spec=spec,
        output_format="png",
        timeout=timeout,
    )
    ref = _compute_ref(spec)
    _save_metadata(result.output_dir, "vegalite", spec, ref)
    return _format_result(result, "Vega-Lite", ref=ref)


@mcp.tool()
async def create_observable_plot(
    code: str,
    timeout: int = 60,
) -> str:
    """Create an Observable Plot visualization — the DEFAULT and PREFERRED tool for charts.

    Use this tool for all visualizations unless you specifically need Python data processing
    (pandas, numpy, scipy) or Plotly-specific features (interactive HTML, 3D plots).

    RULES:
    - The code MUST contain exactly ONE Plot.plot() call as the final expression.
    - You MUST pass `document` as an option to Plot.plot() for rendering.
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
        timeout: Maximum execution time in seconds (default: 60).

    Returns:
        URLs of generated files. Display images inline with ![description](url).
    """
    logger.info("Creating Observable plot (timeout=%d)", timeout)
    result: PlotResult = await run_observable(
        code=code,
        output_format="png",
        timeout=timeout,
    )
    ref = _compute_ref(code)
    _save_metadata(result.output_dir, "observable", code, ref)
    return _format_result(result, "Observable Plot", ref=ref)


@mcp.tool()
async def revise_plot(
    previous_ref: str,
    new_code: str,
    timeout: int = 60,
) -> str:
    """Revise a previously submitted plot by providing updated code.

    Use this when you need to fix or modify a plot you already created.
    Provide the reference ID from a previous plot result and the complete
    new code/spec. The tool automatically detects the plot type from the
    stored metadata.

    Args:
        previous_ref: Reference ID from a previous plot result (12-char hex string).
        new_code: The complete new code or spec to execute.
        timeout: Maximum execution time in seconds (default: 60).

    Returns:
        The previous code for context, new plot results, and a new reference ID.
        Display images inline with ![description](url).
    """
    meta = _lookup_by_ref(previous_ref)
    if meta is None:
        return (
            f"Reference '{previous_ref}' not found. "
            "It may have expired (outputs are kept for 24 hours) "
            "or the reference ID may be incorrect."
        )

    tool_type = meta["tool_type"]
    previous_code = meta["code"]

    if tool_type == "plotly":
        result = await run_plot(code=new_code, output_format="both", timeout=timeout)
        tool_name = "Plotly"
    elif tool_type == "vegalite":
        try:
            json.loads(new_code)
        except json.JSONDecodeError as e:
            return f"Invalid JSON in revised spec: {e}"
        result = await run_vegalite(spec=new_code, output_format="png", timeout=timeout)
        tool_name = "Vega-Lite"
    elif tool_type == "observable":
        result = await run_observable(
            code=new_code, output_format="png", timeout=timeout
        )
        tool_name = "Observable Plot"
    else:
        return f"Unknown tool type in stored metadata: {tool_type}"

    new_ref = _compute_ref(new_code)
    _save_metadata(result.output_dir, tool_type, new_code, new_ref)

    parts = [
        f"Previous code (ref {previous_ref}):\n```\n{previous_code}\n```\n",
        _format_result(result, tool_name, ref=new_ref),
    ]
    return "\n".join(parts)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
