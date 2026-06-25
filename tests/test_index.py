"""Rendered-output check: the index page is a landing page linking the demos."""

DEMOS = ["simple.html", "levels.html", "dashboard.html"]


def test_index_links_to_demos(render, data):
    html = render("index.html", {**data, "col_values": []})
    for demo in DEMOS:
        assert f'href="{demo}"' in html


def test_index_shows_version(render, data):
    html = render("index.html", {**data, "col_values": []})
    assert "v0.1.2" in html
