"""Rendered-output checks for the 'levels of detail' demo (demo #1)."""
from collections import defaultdict

ALLOWED_OCCUPATIONS = {"Tech Arch", "Developer", "DM"}


def test_extraction(data):
    assert data["name"] == "Tables Demo"
    assert len(data["board_data"]) == 7  # 1 header + 6 players


def test_three_altitudes(render, data):
    html = render("levels.html", data)
    for level in ("executive", "summary", "detail"):
        assert f'data-level="{level}"' in html


def test_executive_rollups(render, data):
    html = render("levels.html", data)
    assert "Tables Demo" in html and "1.0.2" in html
    for token in ("499", "83", "123", "John Johnson"):
        assert token in html


def test_summary_is_ranked(render, data):
    html = render("levels.html", data)
    assert 0 <= html.find("John Johnson") < html.find("Jill Smith")


def test_detail_one_block_per_player(render, data):
    html = render("levels.html", data)
    assert html.count("<details") == 6


def test_occupation_totals(render, data):
    html = render("levels.html", data)
    headings, rows = data["board_data"][0], data["board_data"][1:]
    assert "Occupation" in headings
    occ_idx = headings.index("Occupation")
    totals = defaultdict(float)
    for r in rows:
        assert r[occ_idx] in ALLOWED_OCCUPATIONS
        totals[r[occ_idx]] += r[2]
    assert round(sum(totals.values())) == 499
    for occ, tot in totals.items():
        assert occ in html
        assert str(int(tot)) in html


def test_generic_footer(render, data):
    html = render("levels.html", data)
    assert "your spreadsheet" in html
    assert "demo_data.xlsx" not in html
