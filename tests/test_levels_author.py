"""Rendered-output check: author-controlled opening altitude (demo #1).

demo_data carries a 'detail' cell, so the report opens at that altitude with no
template or code change; without it, the template falls back to summary.
"""


def test_detail_cell_sets_opening_altitude(render, data):
    assert data["detail"] == "executive"
    html = render("levels.html", data)
    assert 'data-active="executive"' in html


def test_default_altitude_without_detail(render, data):
    without = {k: v for k, v in data.items() if k != "detail"}
    html = render("levels.html", without)
    assert 'data-active="summary"' in html
