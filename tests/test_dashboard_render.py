"""Rendered-output check for the spreadsheet-driven dashboard (demo #2).

Red/blue TDD by verifying rendered output: renders templates/dashboard.html
against the real data hubris extracts from demo_data.xlsx and asserts the KPI
roll-ups, the per-occupation breakdown, and the graphical elements (one bar per
player, one stacked segment per occupation) are present and correct. Run with:
    uv run python tests/test_dashboard_render.py
"""
import os
import sys
from collections import defaultdict

import jinja2
from hubris.read_params import read_file

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

failures = []


def check(cond, msg):
    failures.append(msg) if not cond else None


data = read_file(os.path.join(ROOT, "demo_data.xlsx"))
board = data.get("board_data", [[]])
headings = board[0]
rows = board[1:]

env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.join(ROOT, "templates")),
    autoescape=False,
)
try:
    html = env.get_template("dashboard.html").render(**data)
except jinja2.TemplateNotFound:
    html = ""
    failures.append("templates/dashboard.html does not exist yet")
except Exception as exc:
    html = ""
    failures.append(f"template render raised {type(exc).__name__}: {exc}")

if html:
    # KPI roll-ups.
    check("Tables Demo" in html and "0.1.2" in html, "name/version not rendered")
    for token in ("6", "83", "123", "499", "John Johnson"):
        check(token in html, f"KPI '{token}' not rendered")

    # Per-occupation breakdown: each occupation labelled with its point total,
    # and the totals must sum to the grand total.
    check("Occupation" in headings, "data has no Occupation column to chart")
    if "Occupation" in headings:
        occ_idx = headings.index("Occupation")
        totals = defaultdict(float)
        for r in rows:
            totals[r[occ_idx]] += r[2]
        check(round(sum(totals.values())) == 499, "occupation totals do not sum to 499")
        for occ, tot in totals.items():
            check(occ in html, f"occupation '{occ}' not shown")
            check(str(int(tot)) in html, f"total for '{occ}' ({int(tot)}) not shown")
        # One stacked segment per occupation.
        n_seg = html.count("data-occseg=")
        check(
            n_seg == len(totals),
            f"expected {len(totals)} stacked segments, got {n_seg}",
        )

    # One bar per player, and the page is actually graphical.
    n_bars = html.count("data-player=")
    check(n_bars == 6, f"expected 6 player bars, got {n_bars}")
    check("width:" in html or "width=" in html, "no proportional bar widths found")

    # Source-agnostic footer.
    check("your spreadsheet" in html, "footer not generic ('your spreadsheet')")
    check("demo_data.xlsx" not in html, "footer still names demo_data.xlsx")

if failures:
    print(f"RED — {len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("BLUE — dashboard has KPIs, per-occupation breakdown, and per-player bars")
