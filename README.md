# mcp-plotly

An MCP server that generates data visualizations (Plotly, Vega-Lite, Observable Plot) inside isolated [Podman](https://podman.io/) containers. Designed to be run via `uvx` from an MCP router.

## Requirements

- Python 3.14+
- [Podman](https://podman.io/) installed and available on `PATH`
- [uv](https://docs.astral.sh/uv/) for running the server

## Quick start

```bash
MCP_PLOTLY_URL_PREFIX=https://example.com/plots/ uvx mcp-plotly
```

Or install and run locally:

```bash
uv sync
MCP_PLOTLY_URL_PREFIX=https://example.com/plots/ uv run mcp-plotly
```

The container image (`mcp-plotly`) is built automatically on first use.

## MCP tools

### `create_plot`

Runs Python code that creates a Plotly figure in an isolated container.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `code` | string | *(required)* | Python code that creates a Plotly figure assigned to `fig` |
| `output_format` | `"png"` \| `"html"` \| `"both"` | `"both"` | Output format(s) |
| `timeout` | int | `60` | Max execution time in seconds |

The following imports are provided automatically: `plotly.graph_objects` (as `go`), `plotly.express` (as `px`), `pandas` (as `pd`), `numpy` (as `np`).

Available packages: plotly, kaleido, pandas, numpy, scipy, scikit-learn.

### `create_vegalite_plot`

Renders a [Vega-Lite](https://vega.github.io/vega-lite/) JSON specification into SVG and/or PNG.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `spec` | string | *(required)* | A complete Vega-Lite specification as a JSON string |
| `output_format` | `"svg"` \| `"png"` \| `"both"` | `"both"` | Output format(s) |
| `timeout` | int | `60` | Max execution time in seconds |

### `create_observable_plot`

Runs JavaScript code that produces an [Observable Plot](https://observablehq.com/plot/) element in an isolated container.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `code` | string | *(required)* | JavaScript code whose last expression evaluates to a `Plot.plot({document, ...})` call |
| `output_format` | `"svg"` \| `"png"` \| `"both"` | `"both"` | Output format(s) |
| `timeout` | int | `60` | Max execution time in seconds |

Available in the namespace: `Plot` (Observable Plot), `document` (jsdom).

## Configuration

| Environment variable | Description | Default |
|---|---|---|
| `MCP_PLOTLY_URL_PREFIX` | **(required)** URL prefix for plot file URLs in responses | — |
| `MCP_PLOTLY_OUTPUT_DIR` | Directory where plots are written | `~/.mcp-plotly/output/` |

`MCP_PLOTLY_URL_PREFIX` must be set and should point to a web server serving the output directory. All tool responses return URLs rather than filesystem paths. For example, with `MCP_PLOTLY_URL_PREFIX=https://example.com/plots/`:

```
~/.mcp-plotly/output/20260331_143022_a1b2c3/plot.png
  -> https://example.com/plots/20260331_143022_a1b2c3/plot.png
```

## Container details

Each invocation runs in a fresh Podman container with:

- **Network isolation**: `--network=none` (no network access)
- **Read-only filesystem**: `--read-only` with a tmpfs for `/tmp`
- **Memory limit**: 1024 MB
- **Configurable timeout**: default 60 seconds

Two container images are used:

- `mcp-plotly`: Python + Plotly/kaleido/pandas/numpy/scipy/scikit-learn + Chromium
- `mcp-plotly-js`: Node.js 22 + vega/vega-lite + Observable Plot + jsdom + sharp

To force a rebuild of the container images:

```bash
podman rmi mcp-plotly mcp-plotly-js
```

## License

[MIT](LICENSE)
