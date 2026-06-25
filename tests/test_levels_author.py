"""Rendered-output check: author-controlled opening altitude (demo #1).

A workbook whose Parameters range carries a 'detail' scalar should make the
report open at that altitude with no template or code change — the spreadsheet
author drives presentation. Run with:
    uv run python tests/test_levels_author.py
"""
import os
import sys

import jinja2
from hubris.read_params import read_file

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "levels_sample.xlsx")

failures = []


def check(cond, msg):
    failures.append(msg) if not cond else None


if not os.path.exists(SAMPLE):
    failures.append("levels_sample.xlsx does not exist yet (run scripts/make_levels_sample.py)")
else:
    data = read_file(SAMPLE)
    # The author's extra cell shows up as a plain scalar in the extracted dict.
    check(
        data.get("detail") == "executive",
        f"author 'detail' not extracted as 'executive' (got {data.get('detail')!r})",
    )
    # The rest of the original data must survive unchanged.
    check(
        data.get("name") == "Tables Demo" and len(data.get("board_data", [])) == 7,
        "sample workbook lost the original name/board_data",
    )
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(os.path.join(ROOT, "templates")),
        autoescape=False,
    )
    html = env.get_template("levels.html").render(**data)
    check(
        'data-active="executive"' in html,
        "report did not open at the author-set altitude (executive)",
    )

if failures:
    print(f"RED — {len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("BLUE — author-set 'detail' cell opens the report at the executive altitude")
