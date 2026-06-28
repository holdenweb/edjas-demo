"""Live demo server for edjas.

Renders the Jinja templates in the templates directory on every request, using
data that edjas extracts from the workbook, and auto-refreshes the browser
whenever a source the page depends on — the spreadsheet or any template —
changes on disk. It lets you experiment with templates and edit the workbook
and watch the rendered page update, without re-running ``make`` or reloading
the tab by hand. The ``/data`` page shows the extracted JSON as a collapsible
tree for comprehension and discussion.

Run it with::

    uv run python serve.py SPREADSHEET [range_name] [--templates DIR] [--frequency SECONDS]

then open http://127.0.0.1:8042/.
"""

import argparse
import hashlib
import json
import math
import re
import time
from pathlib import Path

from flask import Flask, Response, abort, render_template
from markupsafe import escape

from edjas.read_params import read_file

BASE = Path(__file__).resolve().parent

# The browser polls this path; its value is a digest of the watched sources,
# so it changes whenever any of them is saved.
LIVE_PATH = "/__live__"

# Set from the command line in main(); these are the defaults.
DATA_FILE = BASE / "demo_data.xlsx"
RANGE_NAME = "Parameters"
TEMPLATE_DIR = BASE / "templates"

app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
# Re-read templates from disk on every render so edits show up live.
app.config["TEMPLATES_AUTO_RELOAD"] = True


def build_reload_snippet(interval_ms):
    """Build the <script> injected before </body>: poll the fingerprint and reload.

    Kept dependency-free — plain polling, no websockets — since this is only a
    local dev convenience. ``interval_ms`` is the gap between polls.
    """
    return (
        "\n<script>\n"
        "// edjas live-reload: poll a fingerprint of the watched sources and reload\n"
        "// when it changes. Injected by serve.py; not part of the templates.\n"
        "(function () {\n"
        "  var current = null;\n"
        "  async function poll() {\n"
        "    try {\n"
        "      var r = await fetch('" + LIVE_PATH + "', { cache: 'no-store' });\n"
        "      if (!r.ok) return;\n"
        "      var tag = await r.text();\n"
        "      if (current === null) current = tag;\n"
        "      else if (tag !== current) location.reload();\n"
        "    } catch (e) { /* server busy — retry on the next tick */ }\n"
        "  }\n"
        f"  setInterval(poll, {int(interval_ms)});\n"
        "})();\n"
        "</script>\n"
    )


# Rebuilt in main() once --frequency is known; the default keeps the app usable
# if it is imported or run without going through main().
RELOAD_SNIPPET = build_reload_snippet(1000)


def watched_paths():
    """The sources a rendered page is built from: the workbook and every template."""
    return [DATA_FILE, *sorted(TEMPLATE_DIR.glob("*.html"))]


def fingerprint():
    """A digest of the watched files' mtimes and sizes — changes on any saved edit."""
    digest = hashlib.sha1()
    for path in watched_paths():
        try:
            stat = path.stat()
            digest.update(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}".encode())
        except FileNotFoundError:
            digest.update(f"{path.name}:missing".encode())
    return digest.hexdigest()


def load_data():
    """Extract the workbook, retrying briefly to ride out a mid-save read race."""
    last_exc = None
    for attempt in range(3):
        try:
            return read_file(str(DATA_FILE), RANGE_NAME)
        except Exception as exc:  # noqa: BLE001 — surface any read/parse failure
            last_exc = exc
            if attempt < 2:
                time.sleep(0.1)
    raise last_exc


def error_response(exc):
    """A self-healing error page shown when the workbook can't be read."""
    body = (
        "<!doctype html><meta charset='utf-8'>"
        "<title>edjas — source error</title>"
        "<body style=\"font-family:system-ui,sans-serif;margin:2rem;color:#7f1d1d\">"
        f"<h1>Couldn’t read {escape(DATA_FILE.name)}</h1>"
        f"<pre style=\"white-space:pre-wrap;color:#991b1b\">{escape(str(exc))}</pre>"
        "<p style=\"color:#6b7280\">Fix the source and the page will refresh itself.</p>"
        "</body>"
    )
    return Response(body, mimetype="text/html")


# ---------------------------------------------------------------------------
# Collapsible JSON view of the extracted data
# ---------------------------------------------------------------------------

def _leaf_html(value):
    """Render a scalar as a colour-coded, JSON-formatted span."""
    if value is None:
        cls = "null"
    elif isinstance(value, bool):
        cls = "bool"
    elif isinstance(value, (int, float)):
        cls = "num"
    else:
        cls = "str"
    text = json.dumps(value, ensure_ascii=False, default=str)
    return f'<span class="{cls}">{escape(text)}</span>'


def render_node(value, prefix=""):
    """Recursively render a JSON-ish value as a collapsible <details> tree.

    ``prefix`` is the already-escaped label (a key or array index) shown before
    the value. All workbook-derived text is escaped, so cell contents can't
    inject markup.
    """
    if isinstance(value, dict):
        if not value:
            return f'<div class="leaf">{prefix}<span class="punc">{{}}</span></div>'
        n = len(value)
        meta = f'{n} key' + ('' if n == 1 else 's')
        rows = "".join(
            render_node(
                v,
                f'<span class="key">{escape(json.dumps(str(k), ensure_ascii=False))}</span>'
                '<span class="punc">: </span>',
            )
            for k, v in value.items()
        )
        summary = (
            f'{prefix}<span class="punc">{{</span> '
            f'<span class="meta">{meta}</span> <span class="punc">}}</span>'
        )
        return (
            f'<details open class="node"><summary>{summary}</summary>'
            f'<div class="children">{rows}</div></details>'
        )
    if isinstance(value, list):
        if not value:
            return f'<div class="leaf">{prefix}<span class="punc">[]</span></div>'
        n = len(value)
        meta = f'{n} item' + ('' if n == 1 else 's')
        rows = "".join(
            render_node(v, f'<span class="idx">{i}</span><span class="punc">: </span>')
            for i, v in enumerate(value)
        )
        summary = (
            f'{prefix}<span class="punc">[</span> '
            f'<span class="meta">{meta}</span> <span class="punc">]</span>'
        )
        return (
            f'<details open class="node"><summary>{summary}</summary>'
            f'<div class="children">{rows}</div></details>'
        )
    return f'<div class="leaf">{prefix}{_leaf_html(value)}</div>'


# Token-replaced (not f-string/.format) so the CSS braces need no escaping.
JSON_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>edjas data — __SOURCE__</title>
<style>
 :root { --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb; --accent:#0f766e; --bg:#fafafa; }
 * { box-sizing: border-box; }
 body { font-family: system-ui, sans-serif; color: var(--ink); margin: 0; background: var(--bg); }
 .wrap { max-width: 860px; margin: 0 auto; padding: 2rem 1.5rem 4rem; }
 header { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; align-items: baseline; justify-content: space-between; }
 h1 { font-size: 1.4rem; margin: 0; }
 h1 small { color: var(--muted); font-weight: normal; font-size: 0.95rem; }
 .home { color: var(--accent); text-decoration: none; font-size: 0.9rem; font-weight: 600; }
 .home:hover { text-decoration: underline; }
 .controls { display: flex; gap: 0.5rem; align-items: center; margin: 1rem 0 1.25rem; }
 .controls button, .controls a.btn { font: inherit; font-size: 0.82rem; border: 1px solid var(--line); background: #fff; color: var(--ink); padding: 0.35rem 0.7rem; border-radius: 7px; cursor: pointer; text-decoration: none; }
 .controls button:hover, .controls a.btn:hover { border-color: var(--accent); color: var(--accent); }
 .tree { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 1rem 1.1rem; font: 0.85rem/1.7 ui-monospace, Menlo, monospace; overflow-x: auto; }
 .tree summary { cursor: pointer; list-style: none; }
 .tree summary::-webkit-details-marker { display: none; }
 .tree summary::before { content: '▸'; color: var(--muted); display: inline-block; width: 1rem; }
 .tree details[open] > summary::before { content: '▾'; }
 .tree .children { margin-left: 1.1rem; border-left: 1px solid var(--line); padding-left: 0.7rem; }
 .tree .leaf { padding-left: 1rem; }
 .tree .key { color: #9333ea; }
 .tree .idx { color: var(--muted); }
 .tree .punc { color: var(--muted); }
 .tree .meta { color: var(--muted); font-style: italic; font-size: 0.8rem; }
 .tree .str { color: #0f766e; }
 .tree .num { color: #2563eb; }
 .tree .bool { color: #b45309; }
 .tree .null { color: #9ca3af; }
 footer { margin-top: 1.5rem; font-size: 0.8rem; color: var(--muted); }
</style>
</head>
<body>
 <div class="wrap">
  <header>
   <h1>Extracted data <small>__SOURCE__</small></h1>
   <a class="home" href="/">← edjas demos</a>
  </header>
  <div class="controls">
   <button type="button" onclick="setAll(true)">Expand all</button>
   <button type="button" onclick="setAll(false)">Collapse all</button>
   <a class="btn" href="data.json">Raw JSON ↗</a>
  </div>
  <div class="tree" id="tree">__TREE__</div>
  <footer>This is exactly what edjas hands to the templates — edit the spreadsheet and this view refreshes itself.</footer>
 </div>
 <script>
  function setAll(open) {
    document.querySelectorAll('#tree details').forEach(function (d) { d.open = open; });
  }
 </script>
</body>
</html>
"""


def json_page_html(data):
    """Wrap the rendered tree in the viewer page chrome.

    A single regex pass maps each token exactly once, so inserted text — the
    filename or the tree — is never re-scanned for the other token.
    """
    replacements = {
        "__SOURCE__": str(escape(DATA_FILE.name)),
        "__TREE__": render_node(data),
    }
    return re.sub(r"__SOURCE__|__TREE__", lambda m: replacements[m.group(0)], JSON_PAGE)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def render_page(page):
    """Render <templates>/<page>.html with fresh workbook data, or an error page."""
    if not (TEMPLATE_DIR / f"{page}.html").is_file():
        abort(404)
    try:
        data = load_data()
    except Exception as exc:  # noqa: BLE001 — show the failure, keep the server up
        return error_response(exc)
    # ``live`` lets templates show server-only affordances (e.g. the JSON link);
    # a workbook key of the same name would win, which is fine.
    return render_template(f"{page}.html", **{"live": True, **data})


@app.route("/")
def index():
    return render_page("index")


@app.route("/<page>.html")
def page(page):
    return render_page(page)


@app.route("/<page>.html.txt")
def source(page):
    """Serve a template's raw markup as text, matching the static build's source links."""
    template_path = TEMPLATE_DIR / f"{page}.html"
    if not template_path.is_file():
        abort(404)
    return Response(template_path.read_text(encoding="utf-8"), mimetype="text/plain")


@app.route("/data")
def data_view():
    """Collapsible view of the JSON edjas extracts from the workbook."""
    try:
        data = load_data()
    except Exception as exc:  # noqa: BLE001
        return error_response(exc)
    return Response(json_page_html(data), mimetype="text/html")


@app.route("/data.json")
def data_json():
    """The extracted data as raw JSON, for copy-paste and discussion."""
    try:
        data = load_data()
    except Exception as exc:  # noqa: BLE001
        payload = json.dumps({"error": str(exc)}, indent=2)
        return Response(payload, status=500, mimetype="application/json")
    payload = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    return Response(payload, mimetype="application/json")


@app.route(LIVE_PATH)
def live():
    return Response(fingerprint(), mimetype="text/plain")


@app.after_request
def add_live_reload(response):
    """Inject the reload poller into every HTML page and stop it being cached."""
    if response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        if "</body>" in html:
            html = html.replace("</body>", RELOAD_SNIPPET + "</body>", 1)
        else:
            html += RELOAD_SNIPPET
        response.set_data(html)
        response.headers["Cache-Control"] = "no-store"
    return response


def main():
    global DATA_FILE, RANGE_NAME, TEMPLATE_DIR, RELOAD_SNIPPET
    parser = argparse.ArgumentParser(description="Live edjas demo server.")
    parser.add_argument("spreadsheet", help="source workbook to extract and serve")
    parser.add_argument(
        "range_name", nargs="?", default=RANGE_NAME,
        help="named range to read (default: Parameters)",
    )
    parser.add_argument(
        "--templates", default=str(TEMPLATE_DIR), metavar="DIR",
        help="templates directory (default: ./templates)",
    )
    parser.add_argument(
        "--frequency", type=float, default=1.0, metavar="SECONDS",
        help="seconds between live-reload polls (default: 1.0)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8042)
    args = parser.parse_args()

    if not math.isfinite(args.frequency) or args.frequency <= 0:
        parser.error("--frequency must be a positive number of seconds")

    DATA_FILE = Path(args.spreadsheet).resolve()
    RANGE_NAME = args.range_name
    TEMPLATE_DIR = Path(args.templates).resolve()
    if not TEMPLATE_DIR.is_dir():
        parser.error(f"templates directory not found: {TEMPLATE_DIR}")
    app.template_folder = str(TEMPLATE_DIR)
    # Floor the interval so a tiny --frequency can't become setInterval(…, 0).
    RELOAD_SNIPPET = build_reload_snippet(max(50, round(args.frequency * 1000)))

    print(f" * edjas live demo — http://{args.host}:{args.port}/")
    print(f" * spreadsheet: {DATA_FILE}")
    print(f" * templates:   {TEMPLATE_DIR}")
    print(f" * polling every {args.frequency:g}s; pages refresh when a source changes")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
