"""Podman container management for running Plotly code in isolation."""

import logging

from mcp_plotly.pool import (
    ContainerPool,
    PlotResult,
    get_output_base_dir,
)

logger = logging.getLogger(__name__)

IMAGE_NAME = "mcp-plotly"
DEFAULT_TIMEOUT = 60

CONTAINERFILE_CONTENT = """\
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

# Install Chromium for kaleido (Plotly PNG export)
RUN apt-get update && \\
    apt-get install -y --no-install-recommends chromium && \\
    rm -rf /var/lib/apt/lists/*

# Point kaleido at the system Chromium
ENV KALEIDO_BROWSER_PATH=/usr/bin/chromium

# Install common data science and plotting packages into the system Python
RUN uv pip install --system \\
    plotly \\
    kaleido \\
    pandas \\
    numpy \\
    scipy \\
    scikit-learn

WORKDIR /work

ENTRYPOINT ["python", "/work/script.py"]
"""

WORKER_SCRIPT = """\
import json
import os
import socket
import sys
import traceback
from io import StringIO

# Pre-import heavy packages (this is the whole point of the hot container)
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import numpy as np

SOCKET_PATH = "/work/comm/worker.sock"


def main():
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(SOCKET_PATH)
    sock.listen(1)

    # Signal readiness to the host
    with open("/work/comm/ready", "w") as f:
        f.write("ready")

    conn, _ = sock.accept()
    try:
        # Read full request (client signals EOF via shutdown(SHUT_WR))
        chunks = []
        while True:
            data = conn.recv(65536)
            if not data:
                break
            chunks.append(data)

        request = json.loads(b"".join(chunks).decode())
        response = execute(request)
        conn.sendall(json.dumps(response).encode())
    finally:
        conn.close()
        sock.close()


def execute(request):
    code = request["code"]
    output_format = request["output_format"]
    output_subdir = request["output_subdir"]

    output_dir = os.path.join("/work/output", output_subdir)
    os.makedirs(output_dir, exist_ok=True)

    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = stdout_buf = StringIO()
    sys.stderr = stderr_buf = StringIO()

    try:
        namespace = {"go": go, "px": px, "pd": pd, "np": np, "os": os}
        exec(code, namespace)
        fig = namespace.get("fig")

        if fig is None:
            return {
                "success": False,
                "stdout": stdout_buf.getvalue(),
                "stderr": "No variable named 'fig' was created by the code.",
            }

        if output_format in ("png", "both"):
            fig.write_image(os.path.join(output_dir, "plot.png"))
        if output_format in ("html", "both"):
            fig.write_html(os.path.join(output_dir, "plot.html"))

        return {
            "success": True,
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue(),
        }
    except Exception:
        return {
            "success": False,
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue() + traceback.format_exc(),
        }
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr


if __name__ == "__main__":
    main()
"""


def _wrap_code(user_code: str, output_format: str) -> str:
    """Wrap user code with imports and output-writing boilerplate."""
    lines = [
        "import plotly.graph_objects as go",
        "import plotly.express as px",
        "import pandas as pd",
        "import numpy as np",
        "import os",
        "",
        "# -- user code starts --",
        user_code,
        "# -- user code ends --",
        "",
        'output_dir = "/work/output"',
    ]

    if output_format in ("png", "both"):
        lines.append('fig.write_image(os.path.join(output_dir, "plot.png"))')
    if output_format in ("html", "both"):
        lines.append('fig.write_html(os.path.join(output_dir, "plot.html"))')

    lines.append('print("Plot generated successfully")')
    return "\n".join(lines)


# Initialize the container pool
_pool = ContainerPool(
    image_name=IMAGE_NAME,
    containerfile=CONTAINERFILE_CONTENT,
    worker_script=WORKER_SCRIPT,
    script_filename="script.py",
)


async def ensure_image() -> None:
    """Build the container image if it doesn't already exist."""
    await _pool.ensure_image()


async def shutdown() -> None:
    """Shut down the container pool."""
    await _pool.shutdown()


async def run_plot(
    code: str,
    output_format: str = "both",
    timeout: int = DEFAULT_TIMEOUT,
) -> PlotResult:
    """Run Plotly code in a container and return the result."""
    logger.info(
        "Running plot in container (format=%s, timeout=%d)", output_format, timeout
    )
    base_dir = get_output_base_dir()
    return await _pool.run(
        request={"code": code, "output_format": output_format},
        timeout=timeout,
        output_base_dir=base_dir,
    )
