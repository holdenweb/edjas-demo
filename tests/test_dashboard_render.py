"""Rendered-output checks for the spreadsheet-driven dashboard (demo #2)."""
from collections import defaultdict


def test_kpis(render, data):
    html = render("dashboard.html", data)
    assert "Tables Demo" in html and "0.1.2" in html
    for token in ("6", "83", "123", "499", "John Johnson"):
        assert token in html


def test_occupation_breakdown(render, data):
    html = render("dashboard.html", data)
    headings, rows = data["board_data"][0], data["board_data"][1:]
    assert "Occupation" in headings
    occ_idx = headings.index("Occupation")
    totals = defaultdict(float)
    for r in rows:
        totals[r[occ_idx]] += r[2]
    assert round(sum(totals.values())) == 499
    for occ, tot in totals.items():
        assert occ in html
        assert str(int(tot)) in html
    assert html.count("data-occseg=") == len(totals)  # one stacked segment each


def test_player_bars(render, data):
    html = render("dashboard.html", data)
    assert html.count("data-player=") == 6
    assert "width:" in html  # proportional bar widths


def test_generic_footer(render, data):
    html = render("dashboard.html", data)
    assert "your spreadsheet" in html
    assert "demo_data.xlsx" not in html
