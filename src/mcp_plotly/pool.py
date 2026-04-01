"""Generic container pool for running code in isolated Podman containers."""

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60
DEFAULT_MEMORY = "1024m"
MAX_OUTPUT_AGE_SECONDS = 24 * 60 * 60


@dataclass
class PlotResult:
    """Result of a plot execution."""

    success: bool
    output_dir: str
    files: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


@dataclass
class _HotContainer:
    """A pre-started container with imports done, waiting for work on a socket."""

    proc: asyncio.subprocess.Process
    comm_dir: str
    socket_path: str
    output_base_dir: str


def get_output_base_dir() -> Path:
    """Return the base output directory, respecting MCP_PLOTLY_OUTPUT_DIR env var."""
    env_dir = os.environ.get("MCP_PLOTLY_OUTPUT_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".mcp-plotly" / "output"


def get_url_prefix() -> str:
    """Return the URL prefix with trailing slash ensured.

    Raises RuntimeError if MCP_PLOTLY_URL_PREFIX is not set.
    """
    prefix = os.environ.get("MCP_PLOTLY_URL_PREFIX")
    if not prefix:
        raise RuntimeError(
            "MCP_PLOTLY_URL_PREFIX environment variable is required but not set"
        )
    return prefix.rstrip("/") + "/"


async def _run_cmd(cmd: list[str], timeout: int | None = None) -> tuple[int, str, str]:
    """Run a command asynchronously and return (returncode, stdout, stderr)."""
    logger.debug("Running command: %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning("Command timed out after %ds, killing process", timeout)
        proc.kill()
        await proc.communicate()
        return -1, "", f"Command timed out after {timeout}s"

    return (
        proc.returncode or 0,
        stdout_bytes.decode(errors="replace"),
        stderr_bytes.decode(errors="replace"),
    )


def cleanup_old_outputs(base_dir: Path) -> None:
    """Remove output subdirectories older than MAX_OUTPUT_AGE_SECONDS."""
    if not base_dir.exists():
        return
    cutoff = time.time() - MAX_OUTPUT_AGE_SECONDS
    for entry in base_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry)
                logger.info("Cleaned up old output: %s", entry.name)
        except OSError as e:
            logger.debug("Failed to clean up %s: %s", entry.name, e)


def file_location(file_path: str) -> str:
    """Convert a file path to a URL using MCP_PLOTLY_URL_PREFIX.

    The URL is constructed by replacing the output base directory prefix
    with the URL prefix. For example, if the base dir is ~/.mcp-plotly/output
    and the URL prefix is https://example.com/plots/, then:
      ~/.mcp-plotly/output/20260331_143022_a1b2c3/plot.png
    becomes:
      https://example.com/plots/20260331_143022_a1b2c3/plot.png
    """
    url_prefix = get_url_prefix()
    base_dir = str(get_output_base_dir())
    if file_path.startswith(base_dir):
        relative = file_path[len(base_dir) :].lstrip("/")
        return url_prefix + relative
    return file_path


class ContainerPool:
    """Pool of hot containers for running code in isolation with pre-loaded imports."""

    def __init__(
        self,
        image_name: str,
        containerfile: str,
        worker_script: str,
        script_filename: str,
    ):
        """Initialize the container pool.

        Args:
            image_name: Name for the container image
            containerfile: Containerfile content
            worker_script: Worker script content
            script_filename: Name for the worker script file (e.g., "script.py")
        """
        self.image_name = image_name
        self.containerfile = containerfile
        self.worker_script = worker_script
        self.script_filename = script_filename
        # Lazy init locks to avoid Python 3.14 issues with Lock creation outside event loop
        self._image_lock: asyncio.Lock | None = None
        self._hot_lock: asyncio.Lock | None = None
        self._next_container: asyncio.Task[_HotContainer] | None = None
        self._worker_script_path: str | None = None

    def _get_image_lock(self) -> asyncio.Lock:
        """Get or create the image lock (lazy initialization)."""
        if self._image_lock is None:
            self._image_lock = asyncio.Lock()
        return self._image_lock

    def _get_hot_lock(self) -> asyncio.Lock:
        """Get or create the hot container lock (lazy initialization)."""
        if self._hot_lock is None:
            self._hot_lock = asyncio.Lock()
        return self._hot_lock

    async def _kill_orphans(self) -> None:
        """Kill any containers left over from a previous server run."""
        rc, stdout, _ = await _run_cmd(
            ["podman", "ps", "-q", f"--filter=label=mcp-plotly={self.image_name}"],
        )
        if rc == 0 and stdout.strip():
            ids = stdout.strip().split()
            logger.info(
                "Killing %d orphaned container(s) for '%s'", len(ids), self.image_name
            )
            await _run_cmd(["podman", "kill", *ids])

    async def ensure_image(self) -> None:
        """Build the container image if it doesn't already exist.

        Uses a lock to prevent concurrent builds from racing.
        """
        async with self._get_image_lock():
            await self._kill_orphans()
            rc, _, _ = await _run_cmd(["podman", "image", "exists", self.image_name])
            if rc == 0:
                logger.debug("Container image '%s' already exists", self.image_name)
                return

            logger.info("Building container image '%s'", self.image_name)
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".Containerfile",
                delete=False,
                prefix=f"mcp_{self.image_name}_",
            ) as f:
                f.write(self.containerfile)
                containerfile_path = f.name

            try:
                rc, stdout, stderr = await _run_cmd(
                    [
                        "podman",
                        "build",
                        "-t",
                        self.image_name,
                        "-f",
                        containerfile_path,
                        "/tmp",
                    ],
                    timeout=300,
                )
                if rc != 0:
                    logger.error("Image build failed: %s", stderr)
                    raise RuntimeError(
                        f"Failed to build container image:\n{stderr}\n{stdout}"
                    )
                logger.info("Container image '%s' built successfully", self.image_name)
            finally:
                try:
                    os.unlink(containerfile_path)
                except OSError:
                    pass

    def _get_worker_script_path(self) -> str:
        """Return path to the worker script, writing it to a temp file on first call."""
        if self._worker_script_path is None:
            suffix = os.path.splitext(self.script_filename)[1]
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=suffix,
                delete=False,
                prefix=f"mcp_{self.image_name}_worker_",
            ) as f:
                f.write(self.worker_script)
                self._worker_script_path = f.name
            os.chmod(self._worker_script_path, 0o644)
        return self._worker_script_path

    async def _start_hot_container(self, base_output_dir: Path) -> _HotContainer:
        """Start a container with pre-imported packages, waiting on a Unix socket."""
        await self.ensure_image()

        script_path = self._get_worker_script_path()
        comm_dir = tempfile.mkdtemp(prefix=f"mcp_{self.image_name}_comm_")
        socket_path = os.path.join(comm_dir, "worker.sock")
        base_output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "podman",
            "run",
            "--rm",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/root",
            "--network=none",
            f"--memory={DEFAULT_MEMORY}",
            f"--label=mcp-plotly={self.image_name}",
            "-v",
            f"{script_path}:/work/{self.script_filename}:ro,z",
            "-v",
            f"{comm_dir}:/work/comm:rw,z",
            "-v",
            f"{base_output_dir!s}:/work/output:rw,z",
            self.image_name,
        ]

        logger.debug("Starting hot container: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for the worker to signal readiness (imports done, socket listening)
        ready_path = os.path.join(comm_dir, "ready")
        start = time.monotonic()
        while time.monotonic() - start < 60:
            if os.path.exists(ready_path):
                elapsed = time.monotonic() - start
                logger.info("Hot container ready (%.1fs)", elapsed)
                return _HotContainer(
                    proc=proc,
                    comm_dir=comm_dir,
                    socket_path=socket_path,
                    output_base_dir=str(base_output_dir),
                )
            if proc.returncode is not None:
                stderr = ""
                if proc.stderr:
                    stderr = (await proc.stderr.read()).decode(errors="replace")
                shutil.rmtree(comm_dir, ignore_errors=True)
                raise RuntimeError(f"Hot container exited before ready: {stderr}")
            await asyncio.sleep(0.05)

        # Timed out
        proc.kill()
        await proc.communicate()
        shutil.rmtree(comm_dir, ignore_errors=True)
        raise RuntimeError("Hot container did not become ready within 60s")

    async def _acquire_hot_container(self, base_output_dir: Path) -> _HotContainer:
        """Get a pre-heated container, or start a fresh one if none is available."""
        # Take the pre-heated container if available
        async with self._get_hot_lock():
            task = self._next_container
            self._next_container = None

        if task is not None:
            try:
                hot = await task
                if hot.output_base_dir == str(base_output_dir):
                    return hot
                # Output dir changed (e.g. in tests), discard stale container
                logger.debug("Discarding pre-heated container (output dir changed)")
                hot.proc.kill()
                await hot.proc.communicate()
                shutil.rmtree(hot.comm_dir, ignore_errors=True)
            except (Exception, asyncio.CancelledError):
                logger.debug("Pre-heated container unavailable, starting fresh")

        return await self._start_hot_container(base_output_dir)

    async def _schedule_preheat(self, base_output_dir: Path) -> None:
        """Schedule a new hot container to start in the background."""
        async with self._get_hot_lock():
            # If there's already a pre-heated container queued, don't start another
            if self._next_container is not None:
                return
            try:
                self._next_container = asyncio.create_task(
                    self._start_hot_container(base_output_dir)
                )
                logger.debug("Scheduled pre-heat for next container")
            except RuntimeError:
                # Event loop is closed or not running
                pass

    async def shutdown(self) -> None:
        """Kill any pre-heated container and clean up resources."""
        task = self._next_container
        self._next_container = None
        if task is not None:
            task.cancel()
            try:
                hot = await task
                hot.proc.kill()
                await hot.proc.communicate()
                shutil.rmtree(hot.comm_dir, ignore_errors=True)
                logger.info("Shut down pre-heated container for '%s'", self.image_name)
            except (asyncio.CancelledError, Exception):
                pass
        if self._worker_script_path:
            try:
                os.unlink(self._worker_script_path)
            except OSError:
                pass
            self._worker_script_path = None

    async def run(
        self,
        request: dict,
        timeout: int,
        output_base_dir: Path,
    ) -> PlotResult:
        """Run code in a container and return the result.

        Args:
            request: Request dict with tool-specific fields
            timeout: Maximum execution time in seconds
            output_base_dir: Base directory for outputs

        Returns:
            PlotResult with success status, files, and stdout/stderr
        """
        logger.info("Running code in container (timeout=%d)", timeout)

        cleanup_old_outputs(output_base_dir)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        run_id = uuid.uuid4().hex[:6]
        output_subdir = f"{timestamp}_{run_id}"
        output_dir = output_base_dir / output_subdir

        hot = await self._acquire_hot_container(output_base_dir)
        try:
            # Add output_subdir to the request
            request_with_subdir = {**request, "output_subdir": output_subdir}
            request_json = json.dumps(request_with_subdir)

            reader, writer = await asyncio.open_unix_connection(hot.socket_path)
            writer.write(request_json.encode())
            await writer.drain()
            writer.write_eof()

            response_data = await asyncio.wait_for(reader.read(), timeout=timeout)
            writer.close()
            await writer.wait_closed()

            if not response_data:
                return PlotResult(
                    success=False,
                    output_dir=str(output_dir),
                    stderr="Container exited without sending a response",
                )

            response = json.loads(response_data)
            logger.debug("Container response: success=%s", response["success"])

            files = []
            if output_dir.exists():
                files = sorted(str(output_dir / f) for f in os.listdir(output_dir))

            return PlotResult(
                success=response["success"],
                output_dir=str(output_dir),
                files=files,
                stdout=response.get("stdout", ""),
                stderr=response.get("stderr", ""),
            )
        except asyncio.TimeoutError:
            logger.warning("Code execution timed out after %ds", timeout)
            hot.proc.kill()
            await hot.proc.communicate()
            return PlotResult(
                success=False,
                output_dir=str(output_dir),
                stderr=f"Execution timed out after {timeout}s",
            )
        except Exception as e:
            logger.error("Error communicating with container: %s", e)
            hot.proc.kill()
            await hot.proc.communicate()
            return PlotResult(
                success=False,
                output_dir=str(output_dir),
                stderr=f"Container communication error: {e}",
            )
        finally:
            # Wait for the container process to finish (it exits after one request)
            try:
                await asyncio.wait_for(hot.proc.communicate(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
            shutil.rmtree(hot.comm_dir, ignore_errors=True)
            await self._schedule_preheat(output_base_dir)
