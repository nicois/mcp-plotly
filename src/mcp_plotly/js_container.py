"""Podman container management for running Vega-Lite and Observable Plot in isolation."""

import json
import logging

from mcp_plotly.pool import ContainerPool, PlotResult, get_output_base_dir

logger = logging.getLogger(__name__)

JS_IMAGE_NAME = "mcp-plotly-js"

JS_CONTAINERFILE_CONTENT = """\
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

RUN apt-get update && \\
    apt-get install -y --no-install-recommends ca-certificates curl fontconfig fonts-dejavu-core && \\
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \\
    apt-get install -y --no-install-recommends nodejs && \\
    rm -rf /var/lib/apt/lists/* && \\
    fc-cache -fv

WORKDIR /work

RUN npm install --save \
    vega \
    vega-lite \
    @observablehq/plot \
    jsdom \
    sharp && \
    npm pkg set type=module

ENTRYPOINT ["node", "/work/script.js"]
"""

JS_WORKER_SCRIPT = """\
import { createServer } from 'net';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';

import * as vega from 'vega';
import * as vegaLite from 'vega-lite';
import * as Plot from '@observablehq/plot';
import { JSDOM } from 'jsdom';
import sharp from 'sharp';

const SOCKET_PATH = '/work/comm/worker.sock';

function main() {
    const server = createServer({ allowHalfOpen: true }, (conn) => {
        const chunks = [];
        conn.on('data', (chunk) => chunks.push(chunk));
        conn.on('end', () => {
            const request = JSON.parse(Buffer.concat(chunks).toString());
            execute(request).then((response) => {
                conn.end(JSON.stringify(response));
                server.close();
            }).catch((e) => {
                conn.end(JSON.stringify({
                    success: false, stdout: '', stderr: e.stack || String(e),
                }));
                server.close();
            });
        });
    });

    server.listen(SOCKET_PATH, () => {
        writeFileSync('/work/comm/ready', 'ready');
    });
}

async function execute(request) {
    const { type, output_format, output_subdir } = request;
    const outputDir = join('/work/output', output_subdir);
    mkdirSync(outputDir, { recursive: true });

    try {
        let svg;
        if (type === 'vegalite') {
            svg = await renderVegaLite(request.spec);
        } else if (type === 'observable') {
            svg = renderObservable(request.code);
        } else {
            return { success: false, stdout: '', stderr: 'Unknown type: ' + type };
        }

        await sharp(Buffer.from(svg)).png().toFile(
            join(outputDir, 'plot.png')
        );

        return { success: true, stdout: '', stderr: '' };
    } catch (e) {
        return { success: false, stdout: '', stderr: e.stack || String(e) };
    }
}

function fixSvg(svg) {
    let fixed = svg.replaceAll('system-ui', "'DejaVu Sans'");
    if (fixed.startsWith('<svg') && !fixed.includes('xmlns=')) {
        fixed = fixed.replace('<svg', '<svg xmlns="http://www.w3.org/2000/svg"');
    }
    return fixed;
}

async function renderVegaLite(spec) {
    const vlSpec = typeof spec === 'string' ? JSON.parse(spec) : spec;
    const vegaSpec = vegaLite.compile(vlSpec).spec;
    const view = new vega.View(vega.parse(vegaSpec), { renderer: 'none' });
    return fixSvg(await view.toSVG());
}

function renderObservable(code) {
    // Use new Function() instead of vm.createContext to avoid cross-realm issues
    // where Array.isArray() etc. break across VM context boundaries.
    // Security isolation is already provided by the Podman container.
    const { document } = new JSDOM('<!DOCTYPE html><html><body></body></html>').window;
    // Insert "return" before the last Plot.plot() call so it works with
    // both single-line and multiline expressions (unlike last-line splitting).
    // We avoid eval() because Observable Plot internally uses new Function()
    // to compile accessors, and those lose access to Plot's module scope in eval.
    const idx = code.lastIndexOf('Plot.plot(');
    if (idx === -1) {
        throw new Error('Code must contain a Plot.plot() call as the final expression.');
    }
    const body = code.slice(0, idx) + 'return ' + code.slice(idx);
    const fn = new Function('Plot', 'document', body);
    const result = fn(Plot, document);
    if (!result) {
        throw new Error(
            'Code must return an Observable Plot element. '
            + 'The last expression should be a Plot.plot({document, ...}) call.'
        );
    }
    const svg = result.querySelector ? result.querySelector('svg') : null;
    const el = svg || result;
    if (!el.outerHTML) {
        throw new Error('Result has no HTML output. Did Plot.plot() return a valid element?');
    }
    return fixSvg(el.outerHTML);
}

main();
"""

_pool = ContainerPool(
    image_name=JS_IMAGE_NAME,
    containerfile=JS_CONTAINERFILE_CONTENT,
    worker_script=JS_WORKER_SCRIPT,
    script_filename="script.js",
)


async def ensure_js_image() -> None:
    await _pool.ensure_image()


async def shutdown_js() -> None:
    await _pool.shutdown()


async def run_vegalite(
    spec: str,
    output_format: str = "both",
    timeout: int = 60,
) -> PlotResult:
    """Run a Vega-Lite spec. `spec` must be a valid JSON string (caller validates)."""
    spec_obj = json.loads(spec)
    base_dir = get_output_base_dir()
    return await _pool.run(
        {"type": "vegalite", "spec": spec_obj, "output_format": output_format},
        timeout=timeout,
        output_base_dir=base_dir,
    )


async def run_observable(
    code: str,
    output_format: str = "both",
    timeout: int = 60,
) -> PlotResult:
    base_dir = get_output_base_dir()
    return await _pool.run(
        {"type": "observable", "code": code, "output_format": output_format},
        timeout=timeout,
        output_base_dir=base_dir,
    )
