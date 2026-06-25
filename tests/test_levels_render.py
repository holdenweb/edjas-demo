"""Rendered-output check for the 'levels of detail' demo (demo #1).

Red/blue TDD by verifying rendered output (not unit tests): this renders
templates/levels.html against the real data extracted from demo_data.xlsx by
hubris, and asserts the three altitudes and the computed roll-ups are present
and correct. Run with:  uv run python tests/test_levels_render.py
"""
import os
import sys

import jinja2
from hubris.read_params import read_file

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

failures = []


def check(cond, msg):
    failures.append(msg) if not cond else None


data = read_file(os.path.join(ROOT, "demo_data.xlsx"))

# Extraction sanity — the data source we are reporting on.
check(data.get("name") == "Tables Demo", "name not extracted as 'Tables Demo'")
check(len(data.get("board_data", [])) == 7, "board_data should be 1 header + 6 rows")

env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.join(ROOT, "templates")),
    autoescape=False,
)
try:
    html = env.get_template("levels.html").render(**data)
except jinja2.TemplateNotFound:
    html = ""
    failures.append("templates/levels.html does not exist yet")
except Exception as exc:  # surfaces template syntax/runtime errors as a red
    html = ""
    failures.append(f"template render raised {type(exc).__name__}: {exc}")

if html:
    # Title comes straight from the Parameters scalars.
    check("Tables Demo" in html and "0.1.2" in html, "name/version not rendered")

    # All three altitudes are present in one rendered page.
    for level in ("executive", "summary", "detail"):
        check(f'data-level="{level}"' in html, f"missing altitude section: {level}")

    # Executive roll-ups (computed in the template from board_data):
    #   players=6, total=499, average=83, high score=123, leader=John Johnson.
    check("499" in html, "total points (499) not rendered")
    check("83" in html, "average points (83) not rendered")
    check("123" in html, "high score (123) not rendered")
    check("John Johnson" in html, "leader (John Johnson) not rendered")

    # Summary altitude is ranked: the leader must precede the lowest scorer.
    check(
        0 <= html.find("John Johnson") < html.find("Jill Smith"),
        "ranking not applied (leader should appear before lowest scorer)",
    )

    # Detail altitude: one progressive-disclosure block per player.
    n_details = html.count("<details")
    check(n_details == 6, f"expected 6 <details> blocks (one per player), got {n_details}")

if failures:
    print(f"RED — {len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("BLUE — all checks passed; rendered report has all three altitudes and correct roll-ups")
