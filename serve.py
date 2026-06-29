"""Live demo server for edjas.

Serves the demo pages on the fly from a *currently-selected* template set (a
folder under ``sets/``) rendered with a *currently-selected* spreadsheet (a file
under ``data/``). A collapsible sidebar injected on every page lets you switch
either; separate pages upload new templates (into a set) and new spreadsheets
(into the data folder). The browser auto-refreshes whenever the selection
changes or a watched file is edited on disk. The ``/data`` page shows the
extracted JSON as a collapsible tree.

Run it with::

    uv run python serve.py [--set NAME] [--data FILE] [--frequency SECONDS]

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

import jinja2
from flask import Flask, Response, abort, request
from markupsafe import escape
from werkzeug.utils import secure_filename

from edjas.read_params import read_file

BASE = Path(__file__).resolve().parent
PAGES_DIR = BASE / "pages"


def _read_page(name):
    """Load a server page's HTML chrome from the pages/ directory."""
    return (PAGES_DIR / name).read_text(encoding="utf-8")


# The browser polls this path; its value changes when the selection or a watched
# file changes, which is what drives the live reload.
LIVE_PATH = "/__live__"

# Roots and the current selection — overridden from the command line in main().
SETS_ROOT = BASE / "sets"            # each subdirectory is a template set
DATA_ROOT = BASE / "data"            # a flat folder of selectable .xlsx spreadsheets
RANGE_NAME = "Parameters"
CURRENT_SET = "default"              # selected template set (a SETS_ROOT subdir)
CURRENT_DATA = "demo_data.xlsx"      # selected spreadsheet (a DATA_ROOT file)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB cap on uploads
ALLOWED_SHEET_EXT = {".xlsx", ".xlsm"}
ALLOWED_TEMPLATE_EXT = {".html"}

# Pages render with autoescape OFF to match the static `make` / jinja-cli build the
# templates were authored for. Rendering from_string (rather than swapping Flask's
# template_folder) avoids Flask's name-keyed compiled-template cache, so switching
# sets never serves another set's stale page.
RENDER_ENV = jinja2.Environment(autoescape=False)

# ---------------------------------------------------------------------------
# In-memory data + selection state
#
# Pages render from an in-memory copy of the currently-selected spreadsheet,
# cached and re-read only when the file (or the selection) changes. A lock
# guards the shared state because Flask serves requests on multiple threads.
# ---------------------------------------------------------------------------
_STATE_LOCK = threading.Lock()
_DATA = None        # cached extracted dict for the current spreadsheet, or None
_DATA_KEY = None    # (CURRENT_DATA, st_mtime_ns, st_size) the cache was read at


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


# ---------------------------------------------------------------------------
# Discovery + path-traversal guards
# ---------------------------------------------------------------------------

def list_sets():
    """Names of the available template sets (subdirectories of SETS_ROOT)."""
    if not SETS_ROOT.is_dir():
        return []
    return sorted(p.name for p in SETS_ROOT.iterdir() if p.is_dir())


def list_data():
    """Filenames of the available spreadsheets (workbooks in DATA_ROOT)."""
    if not DATA_ROOT.is_dir():
        return []
    return sorted(
        p.name for p in DATA_ROOT.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_SHEET_EXT
    )


def _safe_child(root, name):
    """Resolve ``name`` directly under ``root``, rejecting any path-traversal.

    Returns the resolved Path, or None if ``name`` is empty, contains a path
    separator, or would escape ``root``.
    """
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    candidate = (root / name).resolve()
    if candidate.parent != root.resolve():
        return None
    return candidate


def sets_dir(name):
    """The directory for template set ``name``, or None if invalid/missing."""
    child = _safe_child(SETS_ROOT, name)
    if child is None or not child.is_dir():
        return None
    return child


def data_path(name):
    """The path for spreadsheet ``name`` in DATA_ROOT, or None if invalid/missing."""
    child = _safe_child(DATA_ROOT, name)
    if child is None or not child.is_file() or child.suffix.lower() not in ALLOWED_SHEET_EXT:
        return None
    return child


# ---------------------------------------------------------------------------
# Selection + data loading
# ---------------------------------------------------------------------------

def select(set_name=None, data_name=None):
    """Switch the current template set and/or spreadsheet.

    Validates each name, invalidates the data cache on a data switch, and returns
    a list of rejected ``(field, value)`` pairs (empty on success). A successful
    change shows up in fingerprint() via the selection identity, refreshing pages.
    """
    global CURRENT_SET, CURRENT_DATA, _DATA, _DATA_KEY
    rejected = []
    with _STATE_LOCK:
        if set_name is not None and set_name != CURRENT_SET:
            if sets_dir(set_name) is None:
                rejected.append(("set", set_name))
            else:
                CURRENT_SET = set_name
        if data_name is not None and data_name != CURRENT_DATA:
            if data_path(data_name) is None:
                rejected.append(("data", data_name))
            else:
                CURRENT_DATA = data_name
                _DATA = None        # force a re-read of the newly selected workbook
                _DATA_KEY = None
    return rejected


def _read_workbook(path):
    """Extract a workbook from a path, retrying briefly to ride out a mid-save race."""
    last_exc = None
    for attempt in range(3):
        try:
            return read_file(path, RANGE_NAME)
        except Exception as exc:  # noqa: BLE001 — surface any read/parse failure
            last_exc = exc
            if attempt < 2:
                time.sleep(0.1)
    raise last_exc


def load_data():
    """Return the in-memory data for the current spreadsheet, lazily (re-)reading
    DATA_ROOT/CURRENT_DATA when the selection or the file on disk has changed."""
    global _DATA, _DATA_KEY
    with _STATE_LOCK:
        path = DATA_ROOT / CURRENT_DATA
        try:
            stat = path.stat()
            key = (CURRENT_DATA, stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            key = None
        if _DATA is None or (key is not None and key != _DATA_KEY):
            _DATA = _read_workbook(str(path))   # may raise; leaves cache untouched
            _DATA_KEY = key
        return _DATA


def watched_paths():
    """Files whose on-disk edits should refresh open pages: the current set's
    templates and the current spreadsheet. (Caller holds _STATE_LOCK.)"""
    paths = []
    set_dir = sets_dir(CURRENT_SET)
    if set_dir is not None:
        paths.extend(sorted(set_dir.glob("*.html")))
    paths.append(DATA_ROOT / CURRENT_DATA)
    return paths


def fingerprint():
    """A digest that changes on anything the open pages depend on: the current
    selection (set + data names) and the watched files' mtimes and sizes."""
    digest = hashlib.sha1()
    with _STATE_LOCK:
        digest.update(f"sel:{CURRENT_SET}:{CURRENT_DATA}".encode())
        paths = watched_paths()
    for path in paths:
        try:
            stat = path.stat()
            digest.update(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}".encode())
        except FileNotFoundError:
            digest.update(f"{path.name}:missing".encode())
    return digest.hexdigest()


def error_response(exc):
    """A self-healing error page shown when the current workbook can't be read."""
    body = (
        "<!doctype html><meta charset='utf-8'>"
        "<title>edjas — source error</title>"
        "<body style=\"font-family:system-ui,sans-serif;margin:2rem;color:#7f1d1d\">"
        f"<h1>Couldn’t read {escape(CURRENT_DATA)}</h1>"
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


# Page chrome lives in pages/json_page.html; json_page_html() fills the tokens.
JSON_PAGE = _read_page("json_page.html")


def json_page_html(data):
    """Wrap the rendered tree in the viewer page chrome (single-pass token fill)."""
    replacements = {
        "__SOURCE__": str(escape(CURRENT_DATA)),
        "__TREE__": render_node(data),
    }
    return re.sub(r"__SOURCE__|__TREE__", lambda m: replacements[m.group(0)], JSON_PAGE)


# ---------------------------------------------------------------------------
# Upload pages
# ---------------------------------------------------------------------------

UPLOAD_DATA_PAGE = _read_page("upload_data_page.html")
UPLOAD_TEMPLATE_PAGE = _read_page("upload_template_page.html")


def _notices_html(messages):
    return "".join(
        f'<div class="notice {level}">{text}</div>' for level, text in (messages or [])
    )


def _fill(template, replacements):
    """Single-pass token replacement; inserted text is never re-scanned."""
    pattern = "|".join(re.escape(tok) for tok in replacements)
    return re.sub(pattern, lambda m: replacements[m.group(0)], template)


def upload_data_page_html(messages=None):
    """Render the data-upload form with notices and the current data state."""
    return _fill(UPLOAD_DATA_PAGE, {
        "__MESSAGES__": _notices_html(messages),
        "__CURRENT__": str(escape(CURRENT_DATA)),
        "__DATA_DIR__": str(escape(str(DATA_ROOT))),
        "__DATA_LIST__": str(escape(", ".join(list_data()) or "(none)")),
    })


def upload_template_page_html(messages=None):
    """Render the template-upload form with notices and the set selector."""
    options = "".join(
        f'<option value="{escape(s)}">{escape(s)}</option>' for s in list_sets()
    )
    return _fill(UPLOAD_TEMPLATE_PAGE, {
        "__MESSAGES__": _notices_html(messages),
        "__SET_OPTIONS__": options,
        "__CURRENT_SET__": str(escape(CURRENT_SET)),
        "__SETS_LIST__": str(escape(", ".join(list_sets()) or "(none)")),
    })


def safe_upload_name(original, ext, default_stem):
    """A safe on-disk name carrying the already-validated, lowercased extension.

    secure_filename strips a non-ASCII base name down to the bare extension
    token (e.g. 'データ.html' -> 'html'), so fall back to a default stem when
    that happens. The extension is always normalised to ``ext`` (lowercase) so
    the saved file, the route and the success link all agree.
    """
    secured = secure_filename(original)
    if not secured or Path(secured).suffix.lower() != ext:
        return f"{default_stem}{ext}"
    return f"{Path(secured).stem}{ext}"


def resolve_target_set(existing, new_set):
    """The set folder an uploaded template should go into: a sanitised new set
    (created on demand), a chosen existing set, or the current set. None if invalid."""
    if new_set:
        child = _safe_child(SETS_ROOT, secure_filename(new_set))
        if child is None:
            return None
        child.mkdir(parents=True, exist_ok=True)
        return child
    if existing:
        return sets_dir(existing)
    return sets_dir(CURRENT_SET)


# ---------------------------------------------------------------------------
# Selection sidebar (injected on every page)
# ---------------------------------------------------------------------------

# Static HTML+CSS+JS; it fetches /selection.json to populate the two dropdowns and
# posts /select on change. Collapsed by default. Classes are namespaced to avoid
# colliding with the demo pages it is injected into.
SIDEBAR_SNIPPET = """
<div id="__edjas_sb" data-open="0">
  <button type="button" id="__edjas_sb_toggle" title="Choose template set / data">&#10070; set &amp; data</button>
  <div id="__edjas_sb_panel">
    <label>Template set<select id="__edjas_sb_set"></select></label>
    <label>Data<select id="__edjas_sb_data"></select></label>
    <div class="__edjas_sb_lnk"><a href="/upload/template">+ template</a> &middot; <a href="/upload/data">+ data</a></div>
  </div>
</div>
<style>
 #__edjas_sb { position: fixed; top: 12px; right: 12px; z-index: 2147483600; font: 13px/1.4 system-ui, sans-serif; }
 #__edjas_sb_toggle { border: 1px solid #d1d5db; background: #fff; color: #0f766e; font-weight: 600; padding: 0.3rem 0.6rem; border-radius: 8px; cursor: pointer; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
 #__edjas_sb_panel { display: none; margin-top: 6px; background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 0.7rem 0.8rem; width: 220px; box-shadow: 0 4px 16px rgba(0,0,0,0.12); }
 #__edjas_sb[data-open="1"] #__edjas_sb_panel { display: block; }
 #__edjas_sb label { display: block; color: #6b7280; font-size: 0.8rem; margin-bottom: 0.5rem; }
 #__edjas_sb select { display: block; width: 100%; margin-top: 0.2rem; font: inherit; padding: 0.25rem; border: 1px solid #d1d5db; border-radius: 6px; }
 #__edjas_sb .__edjas_sb_lnk { font-size: 0.8rem; }
 #__edjas_sb .__edjas_sb_lnk a { color: #0f766e; text-decoration: none; font-weight: 600; }
</style>
<script>
 (function () {
   if (window.__edjas_sb_init) return; window.__edjas_sb_init = true;
   var root = document.getElementById('__edjas_sb');
   var toggle = document.getElementById('__edjas_sb_toggle');
   var setSel = document.getElementById('__edjas_sb_set');
   var dataSel = document.getElementById('__edjas_sb_data');
   toggle.addEventListener('click', function () {
     root.setAttribute('data-open', root.getAttribute('data-open') === '1' ? '0' : '1');
   });
   function opt(v, cur) { var o = document.createElement('option'); o.value = v; o.textContent = v; if (v === cur) o.selected = true; return o; }
   fetch('/selection.json', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (s) {
     (s.sets || []).forEach(function (v) { setSel.appendChild(opt(v, s.current_set)); });
     (s.data_files || []).forEach(function (v) { dataSel.appendChild(opt(v, s.current_data)); });
   }).catch(function () {});
   function choose(field, value) {
     var body = new URLSearchParams(); body.set(field, value);
     fetch('/select', { method: 'POST', body: body, cache: 'no-store' })
       .then(function (r) { if (r.ok) location.reload(); });
   }
   setSel.addEventListener('change', function () { choose('set', setSel.value); });
   dataSel.addEventListener('change', function () { choose('data', dataSel.value); });
 })();
</script>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def render_page(page):
    """Render the current set's <page>.html from disk with the current data."""
    set_dir = sets_dir(CURRENT_SET)
    tmpl_path = (set_dir / f"{page}.html") if set_dir else None
    if tmpl_path is None or not tmpl_path.is_file():
        abort(404)
    try:
        data = load_data()
    except Exception as exc:  # noqa: BLE001 — show the failure, keep the server up
        return error_response(exc)
    # ``live`` lets templates show server-only affordances; a workbook key of the
    # same name would win, which is fine.
    template = RENDER_ENV.from_string(tmpl_path.read_text(encoding="utf-8"))
    return template.render(**{"live": True, **data})


@app.route("/")
def index():
    return render_page("index")


@app.route("/<page>.html")
def page(page):
    return render_page(page)


@app.route("/<page>.html.txt")
def source(page):
    """Serve a template's raw markup from the current set, as text."""
    set_dir = sets_dir(CURRENT_SET)
    tmpl_path = (set_dir / f"{page}.html") if set_dir else None
    if tmpl_path is None or not tmpl_path.is_file():
        abort(404)
    return Response(tmpl_path.read_text(encoding="utf-8"), mimetype="text/plain")


@app.route("/data")
def data_view():
    """Collapsible view of the JSON edjas extracts from the current workbook."""
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


@app.route("/selection.json")
def selection_json():
    """The sets, data files, and current selection — used by the sidebar."""
    with _STATE_LOCK:
        current = {"current_set": CURRENT_SET, "current_data": CURRENT_DATA}
    payload = {"sets": list_sets(), "data_files": list_data(), **current}
    return Response(json.dumps(payload), mimetype="application/json")


@app.route("/select", methods=["POST"])
def select_route():
    """Switch the current set and/or data (form fields ``set`` / ``data``)."""
    rejected = select(set_name=request.form.get("set"), data_name=request.form.get("data"))
    if rejected:
        payload = json.dumps({"error": "unknown selection", "rejected": rejected})
        return Response(payload, status=400, mimetype="application/json")
    with _STATE_LOCK:
        current = {"current_set": CURRENT_SET, "current_data": CURRENT_DATA}
    return Response(json.dumps({"ok": True, **current}), mimetype="application/json")


@app.route("/upload/data", methods=["GET", "POST"])
def upload_data():
    """Upload a spreadsheet into the data folder and select it."""
    if request.method == "GET":
        return Response(upload_data_page_html(), mimetype="text/html")

    sheet = request.files.get("spreadsheet")
    if not (sheet and sheet.filename):
        return Response(upload_data_page_html([("info", "Choose a .xlsx file, then press Upload.")]), mimetype="text/html")
    ext = Path(sheet.filename).suffix.lower()
    if ext not in ALLOWED_SHEET_EXT:
        msg = ("error", f"Spreadsheet must be .xlsx or .xlsm — got “{escape(sheet.filename)}”.")
        return Response(upload_data_page_html([msg]), mimetype="text/html")

    blob = sheet.read()
    try:  # parsing the bytes IS the validation — nothing is written if it fails
        read_file(io.BytesIO(blob), RANGE_NAME)
    except Exception as exc:  # noqa: BLE001
        msg = ("error", f"Couldn’t read that workbook, nothing saved: {escape(str(exc))}")
        return Response(upload_data_page_html([msg]), mimetype="text/html")

    name = safe_upload_name(sheet.filename, ext, "data")
    (DATA_ROOT / name).write_bytes(blob)
    select(data_name=name)  # make the upload the current data
    msg = ("ok", f"Saved “{escape(name)}” and selected it — the demos and <a href=\"/data\">JSON</a> have updated.")
    return Response(upload_data_page_html([msg]), mimetype="text/html")


@app.route("/upload/template", methods=["GET", "POST"])
def upload_template():
    """Upload a template into a chosen or new set."""
    if request.method == "GET":
        return Response(upload_template_page_html(), mimetype="text/html")

    tmpl = request.files.get("template")
    if not (tmpl and tmpl.filename):
        return Response(upload_template_page_html([("info", "Choose a .html template, then press Upload.")]), mimetype="text/html")
    ext = Path(tmpl.filename).suffix.lower()
    if ext not in ALLOWED_TEMPLATE_EXT:
        msg = ("error", f"Template must be a .html file — got “{escape(tmpl.filename)}”.")
        return Response(upload_template_page_html([msg]), mimetype="text/html")

    source = tmpl.read().decode("utf-8", errors="replace")
    try:  # syntax-check before writing so a broken upload can't clobber a page
        RENDER_ENV.parse(source)
    except Exception as exc:  # noqa: BLE001
        msg = ("error", f"Template has a Jinja syntax error, not saved: {escape(str(exc))}")
        return Response(upload_template_page_html([msg]), mimetype="text/html")

    target = resolve_target_set(request.form.get("set"), request.form.get("new_set"))
    if target is None:
        return Response(upload_template_page_html([("error", "Invalid target set name.")]), mimetype="text/html")

    name = safe_upload_name(tmpl.filename, ext, "template")
    dest = target / name
    verb = "Replaced" if dest.exists() else "Added"
    dest.write_text(source, encoding="utf-8")
    stem = escape(Path(name).stem)
    if target.name == CURRENT_SET:
        where = f"View it at <a href=\"/{stem}.html\">/{stem}.html</a>."
    else:
        where = f"Select set “{escape(target.name)}” in the sidebar to view it."
    msg = ("ok", f"{verb} “{escape(name)}” in set “{escape(target.name)}”. {where}")
    return Response(upload_template_page_html([msg]), mimetype="text/html")


@app.route(LIVE_PATH)
def live():
    return Response(fingerprint(), mimetype="text/plain")


@app.after_request
def inject_affordances(response):
    """Inject the reload poller and the selection sidebar into every HTML page."""
    if response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        injection = RELOAD_SNIPPET + SIDEBAR_SNIPPET
        if "</body>" in html:
            html = html.replace("</body>", injection + "</body>", 1)
        else:
            html += injection
        response.set_data(html)
        response.headers["Cache-Control"] = "no-store"
    return response


def main():
    global SETS_ROOT, DATA_ROOT, RANGE_NAME, CURRENT_SET, CURRENT_DATA, RELOAD_SNIPPET
    parser = argparse.ArgumentParser(description="Live edjas demo server.")
    parser.add_argument("--sets-root", default=str(SETS_ROOT), metavar="DIR",
                        help="root of template-set folders (default: ./sets)")
    parser.add_argument("--data-root", default=str(DATA_ROOT), metavar="DIR",
                        help="folder of data spreadsheets (default: ./data)")
    parser.add_argument("--set", default=None, metavar="NAME",
                        help="initial template set (default: 'default' or the first set)")
    parser.add_argument("--data", default=None, metavar="FILE",
                        help="initial spreadsheet (default: demo_data.xlsx or the first)")
    parser.add_argument("--range", dest="range_name", default=RANGE_NAME, metavar="NAME",
                        help="named range to read (default: Parameters)")
    parser.add_argument("--frequency", type=float, default=1.0, metavar="SECONDS",
                        help="seconds between live-reload polls (default: 1.0)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8042)
    args = parser.parse_args()

    if not math.isfinite(args.frequency) or args.frequency <= 0:
        parser.error("--frequency must be a positive number of seconds")

    SETS_ROOT = Path(args.sets_root).resolve()
    DATA_ROOT = Path(args.data_root).resolve()
    RANGE_NAME = args.range_name
    if not SETS_ROOT.is_dir():
        parser.error(f"sets root not found: {SETS_ROOT}")
    if not DATA_ROOT.is_dir():
        parser.error(f"data root not found: {DATA_ROOT}")

    sets = list_sets()
    if not sets:
        parser.error(f"no template sets found in {SETS_ROOT}")
    CURRENT_SET = args.set or ("default" if "default" in sets else sets[0])
    if CURRENT_SET not in sets:
        parser.error(f"set not found: {CURRENT_SET} (available: {', '.join(sets)})")

    datas = list_data()
    if not datas:
        parser.error(f"no spreadsheets found in {DATA_ROOT}")
    CURRENT_DATA = args.data or ("demo_data.xlsx" if "demo_data.xlsx" in datas else datas[0])
    if CURRENT_DATA not in datas:
        parser.error(f"data file not found: {CURRENT_DATA} (available: {', '.join(datas)})")

    # Floor the interval so a tiny --frequency can't become setInterval(…, 0).
    RELOAD_SNIPPET = build_reload_snippet(max(50, round(args.frequency * 1000)))

    print(f" * edjas live demo — http://{args.host}:{args.port}/")
    print(f" * sets root: {SETS_ROOT}  (current: {CURRENT_SET})")
    print(f" * data root: {DATA_ROOT}  (current: {CURRENT_DATA})")
    print(f" * polling every {args.frequency:g}s; pages refresh on edit or selection")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
