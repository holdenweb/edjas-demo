"""Live demo server for edjas.

Renders the Jinja templates in the templates directory on the fly from an
in-memory copy of the data that edjas extracts from the workbook. Uploading a
new spreadsheet on the ``/upload`` page re-parses it in memory and updates every
open page; a template upload is written into the templates directory. The
browser also auto-refreshes whenever a watched file changes on disk, so you can
experiment with templates and edit the workbook and watch the rendered page
update without re-running ``make`` or reloading the tab. The ``/data`` page
shows the extracted JSON as a collapsible tree for comprehension and discussion.

Run it with::

    uv run python serve.py SPREADSHEET [range_name] [--templates DIR] [--frequency SECONDS]

then open http://127.0.0.1:8042/.
"""

import argparse
import hashlib
import io
import json
import math
import re
import threading
import time
from pathlib import Path

from flask import Flask, Response, abort, render_template, request
from markupsafe import escape
from werkzeug.utils import secure_filename

from edjas.read_params import read_file

BASE = Path(__file__).resolve().parent
PAGES_DIR = BASE / "pages"


def _read_page(name):
    """Load a server page's HTML chrome from the pages/ directory."""
    return (PAGES_DIR / name).read_text(encoding="utf-8")


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
# Cap upload size; remember which extensions the upload page accepts.
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB
ALLOWED_SHEET_EXT = {".xlsx", ".xlsm"}
ALLOWED_TEMPLATE_EXT = {".html"}

# ---------------------------------------------------------------------------
# In-memory workbook data
#
# The extracted workbook dict lives in memory and is the single source the
# pages render from. It is replaced when a spreadsheet is uploaded (parsed in
# memory — no temp file) and re-read when the on-disk workbook changes while it
# is still the active source. A generation counter lets the live-reload poller
# notice in-memory updates that touch no watched file. Access is guarded by a
# lock because Flask serves requests on multiple threads (threaded=True).
# ---------------------------------------------------------------------------
_STATE_LOCK = threading.Lock()
_DATA = None                        # cached extracted dict, or None before first load
_DATA_VERSION = 0                   # bumped on every successful data replacement
_DATA_SOURCE = "disk"               # "disk" (startup workbook) or "upload"
_DATA_SOURCE_LABEL = DATA_FILE.name  # name shown on the JSON / upload pages
_DISK_MTIME = None                  # (st_mtime_ns, st_size) of DATA_FILE at last read


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
    """On-disk files whose edits should refresh pages: templates always, plus the
    startup workbook while it is still the active source (an upload supersedes it)."""
    paths = sorted(TEMPLATE_DIR.glob("*.html"))
    if _DATA_SOURCE == "disk":
        paths.insert(0, DATA_FILE)
    return paths


def fingerprint():
    """A digest that changes on anything the open pages depend on: the in-memory
    data generation (so uploads and disk re-reads, which touch no watched file,
    are noticed) plus the watched files' mtimes and sizes."""
    digest = hashlib.sha1()
    with _STATE_LOCK:
        digest.update(f"v:{_DATA_VERSION}:{_DATA_SOURCE}".encode())
        paths = watched_paths()
    for path in paths:
        try:
            stat = path.stat()
            digest.update(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}".encode())
        except FileNotFoundError:
            digest.update(f"{path.name}:missing".encode())
    return digest.hexdigest()


def _read_workbook(path):
    """Extract a workbook from a path, retrying briefly to ride out a mid-save
    read race. (Only paths retry; an in-memory upload is parsed once directly.)"""
    last_exc = None
    for attempt in range(3):
        try:
            return read_file(path, RANGE_NAME)
        except Exception as exc:  # noqa: BLE001 — surface any read/parse failure
            last_exc = exc
            if attempt < 2:
                time.sleep(0.1)
    raise last_exc


def _read_disk_into_state():
    """Load the on-disk workbook into the in-memory state. Caller holds _STATE_LOCK."""
    global _DATA, _DATA_VERSION, _DATA_SOURCE_LABEL, _DISK_MTIME
    data = _read_workbook(str(DATA_FILE))  # may raise; leaves state untouched on failure
    stat = DATA_FILE.stat()
    _DATA = data
    _DISK_MTIME = (stat.st_mtime_ns, stat.st_size)
    _DATA_SOURCE_LABEL = DATA_FILE.name
    _DATA_VERSION += 1


def _set_uploaded_data(new_data, label):
    """Make an uploaded workbook the live in-memory data. Caller holds _STATE_LOCK."""
    global _DATA, _DATA_VERSION, _DATA_SOURCE, _DATA_SOURCE_LABEL
    _DATA = new_data
    _DATA_SOURCE = "upload"
    _DATA_SOURCE_LABEL = label
    _DATA_VERSION += 1


def load_data():
    """Return the in-memory workbook data, lazily (re-)reading the disk workbook
    when it is the active source and has changed (or nothing is cached yet)."""
    with _STATE_LOCK:
        if _DATA_SOURCE == "disk":
            try:
                stat = DATA_FILE.stat()
                fresh = (stat.st_mtime_ns, stat.st_size)
            except FileNotFoundError:
                fresh = None
            if _DATA is None or (fresh is not None and fresh != _DISK_MTIME):
                _read_disk_into_state()
        return _DATA


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


# Page chrome lives in pages/json_page.html; json_page_html() fills the
# __SOURCE__ and __TREE__ tokens.
JSON_PAGE = _read_page("json_page.html")


def json_page_html(data):
    """Wrap the rendered tree in the viewer page chrome.

    A single regex pass maps each token exactly once, so inserted text — the
    source label or the tree — is never re-scanned for the other token.
    """
    replacements = {
        "__SOURCE__": str(escape(_DATA_SOURCE_LABEL)),
        "__TREE__": render_node(data),
    }
    return re.sub(r"__SOURCE__|__TREE__", lambda m: replacements[m.group(0)], JSON_PAGE)


# ---------------------------------------------------------------------------
# Upload page
# ---------------------------------------------------------------------------

# Page chrome lives in pages/upload_page.html; upload_page_html() fills the
# __MESSAGES__, __SHEET__, __TEMPLATES_DIR__ and __TEMPLATE_LIST__ tokens.
UPLOAD_PAGE = _read_page("upload_page.html")


def upload_page_html(messages=None):
    """Render the upload form, with any result notices and the current state."""
    notices = "".join(
        f'<div class="notice {level}">{text}</div>' for level, text in (messages or [])
    )
    template_list = ", ".join(sorted(p.name for p in TEMPLATE_DIR.glob("*.html"))) or "(none)"
    replacements = {
        "__MESSAGES__": notices,
        "__SHEET__": str(escape(_DATA_SOURCE_LABEL)),
        "__TEMPLATES_DIR__": str(escape(str(TEMPLATE_DIR))),
        "__TEMPLATE_LIST__": str(escape(template_list)),
    }
    return re.sub("|".join(replacements), lambda m: replacements[m.group(0)], UPLOAD_PAGE)


def safe_upload_name(original, ext, default_stem):
    """A safe on-disk name carrying the already-validated, lowercased extension.

    secure_filename strips a non-ASCII base name down to the bare extension
    token (e.g. 'データ.html' -> 'html'), so fall back to a default stem when
    that happens. The extension is always normalised to ``ext`` (lowercase) so
    the saved file, the ``/<page>.html`` route and the success link all agree —
    an uppercase 'Report.HTML' would otherwise be unreachable and unwatched.
    """
    secured = secure_filename(original)
    if not secured or Path(secured).suffix.lower() != ext:
        return f"{default_stem}{ext}"
    return f"{Path(secured).stem}{ext}"


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


@app.route("/upload", methods=["GET", "POST"])
def upload():
    """Upload a new workbook — parsed in memory, becomes the live data — and/or a
    template, written into the templates directory."""
    if request.method == "GET":
        return Response(upload_page_html(), mimetype="text/html")

    messages = []
    sheet = request.files.get("spreadsheet")
    tmpl = request.files.get("template")

    if sheet and sheet.filename:
        ext = Path(sheet.filename).suffix.lower()
        if ext not in ALLOWED_SHEET_EXT:
            messages.append(("error", f"Spreadsheet must be .xlsx or .xlsm — got “{escape(sheet.filename)}”."))
        else:
            try:  # parsing the bytes IS the validation — no temp file, no disk write
                new_data = read_file(io.BytesIO(sheet.read()), RANGE_NAME)
            except Exception as exc:  # noqa: BLE001
                messages.append(("error", f"Couldn’t read that workbook, keeping the current one: {escape(str(exc))}"))
            else:
                with _STATE_LOCK:
                    _set_uploaded_data(new_data, sheet.filename)
                messages.append(("ok", f"Now serving data from “{escape(sheet.filename)}” — the demos and <a href=\"data\">JSON</a> have updated."))

    if tmpl and tmpl.filename:
        ext = Path(tmpl.filename).suffix.lower()
        if ext not in ALLOWED_TEMPLATE_EXT:
            messages.append(("error", f"Template must be a .html file — got “{escape(tmpl.filename)}”."))
        else:
            source = tmpl.read().decode("utf-8", errors="replace")
            try:  # syntax-check before writing so a broken upload can't clobber a page
                app.jinja_env.parse(source)
            except Exception as exc:  # noqa: BLE001
                messages.append(("error", f"Template has a Jinja syntax error, not saved: {escape(str(exc))}"))
            else:
                name = safe_upload_name(tmpl.filename, ext, "template")
                dest = TEMPLATE_DIR / name
                verb = "Replaced" if dest.exists() else "Added"
                dest.write_text(source, encoding="utf-8")
                stem = escape(Path(name).stem)
                messages.append(("ok", f"{verb} template “{escape(name)}” — view it at <a href=\"{stem}.html\">/{stem}.html</a>."))

    if not messages:
        messages.append(("info", "Choose a spreadsheet and/or a template file, then press Upload."))

    return Response(upload_page_html(messages), mimetype="text/html")


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
    global DATA_FILE, RANGE_NAME, TEMPLATE_DIR, RELOAD_SNIPPET, _DATA_SOURCE_LABEL
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
    _DATA_SOURCE_LABEL = DATA_FILE.name
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
