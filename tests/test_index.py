"""Rendered-output checks for the index landing page.

Each demo card links to its output and its source template, and shows the
command make runs to build the output.
"""

DEMOS = ["simple", "levels", "dashboard"]


def test_links_to_outputs(render, data):
    html = render("index.html", data)
    for d in DEMOS:
        assert f'href="{d}.html"' in html


def test_links_to_sources(render, data):
    html = render("index.html", data)
    for d in DEMOS:
        # link to a plain-text copy so the browser shows source, not a render
        assert f'href="{d}.html.txt"' in html
        assert f"templates/{d}.html" in html  # link text still names the source


def test_shows_generating_command(render, data):
    html = render("index.html", data)
    for d in DEMOS:
        assert (
            f"uv run edjas demo_data.xlsx | "
            f"uv run jinja -d - -f json templates/{d}.html > out/{d}.html"
        ) in html


def test_shows_version(render, data):
    html = render("index.html", data)
    assert "v1.0.2" in html
