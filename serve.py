"""Live demo server for edjas.

Renders the Jinja templates in ``templates/`` on every request, using data
that edjas extracts from the demo workbook, and auto-refreshes the browser
whenever a source the page depends on — the spreadsheet or any template —
changes on disk. It lets you experiment with templates and edit the workbook
and watch the rendered page update, without re-running ``make`` or reloading
the tab by hand.

Run it with::

    uv run python serve.py [spreadsheet] [range_name]

then open http://127.0.0.1:8042/.
"""

import argparse
import hashlib
import time
from pathlib import Path

from flask import Flask, Response, abort, render_template
from markupsafe import escape

from edjas.read_params import read_file

BASE = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE / "templates"

# The browser polls this path; its value is a digest of the watched sources,
# so it changes whenever any of them is saved.
LIVE_PATH = "/__live__"

# Set from the command line in main(); these are the defaults.
DATA_FILE = BASE / "demo_data.xlsx"
RANGE_NAME = "Parameters"

app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
# Re-read templates from disk on every render so edits show up live.
app.config["TEMPLATES_AUTO_RELOAD"] = True

# Injected before </body> on every HTML response. Polls the fingerprint endpoint
# and reloads the page when the watched sources change. Kept dependency-free —
# plain polling, no websockets — since this is only a local dev convenience.
_RELOAD_SNIPPET = (
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
    "  setInterval(poll, 700);\n"
    "})();\n"
    "</script>\n"
)


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


def render_page(page):
    """Render templates/<page>.html with fresh workbook data, or an error page."""
    if not (TEMPLATE_DIR / f"{page}.html").is_file():
        abort(404)
    try:
        data = load_data()
    except Exception as exc:  # noqa: BLE001 — show the failure, keep the server up
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
    return render_template(f"{page}.html", **data)


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


@app.route(LIVE_PATH)
def live():
    return Response(fingerprint(), mimetype="text/plain")


@app.after_request
def add_live_reload(response):
    """Inject the reload poller into every HTML page and stop it being cached."""
    if response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        if "</body>" in html:
            html = html.replace("</body>", _RELOAD_SNIPPET + "</body>", 1)
        else:
            html += _RELOAD_SNIPPET
        response.set_data(html)
        response.headers["Cache-Control"] = "no-store"
    return response


def main():
    global DATA_FILE, RANGE_NAME
    parser = argparse.ArgumentParser(description="Live edjas demo server.")
    parser.add_argument(
        "spreadsheet", nargs="?", default=str(DATA_FILE),
        help="source workbook (default: demo_data.xlsx)",
    )
    parser.add_argument(
        "range_name", nargs="?", default=RANGE_NAME,
        help="named range to read (default: Parameters)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8042)
    args = parser.parse_args()

    DATA_FILE = Path(args.spreadsheet).resolve()
    RANGE_NAME = args.range_name

    print(f" * edjas live demo — http://{args.host}:{args.port}/")
    print(f" * watching {DATA_FILE.name} + templates/*.html; pages refresh on change")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
