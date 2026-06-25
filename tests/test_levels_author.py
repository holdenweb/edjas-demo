"""Rendered-output check: author-controlled opening altitude (demo #1).

A workbook whose Parameters range carries a 'detail' scalar makes the report
open at that altitude, with no template or code change.
"""


def test_author_sets_opening_altitude(render, sample_data):
    assert sample_data["detail"] == "executive"
    assert sample_data["name"] == "Tables Demo"
    assert len(sample_data["board_data"]) == 7
    html = render("levels.html", sample_data)
    assert 'data-active="executive"' in html
