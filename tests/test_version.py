"""Rendered-output check: version is a nested {number} dict.

Driven by the hubris flatten fix (a one-entry dict is no longer flattened to a
vector), version is {number: '0.1.2'} and every template renders version.number.
"""
import pytest

TEMPLATES = ["levels.html", "dashboard.html", "simple.html", "index.html"]


def test_version_is_nested_dict(data):
    assert isinstance(data["version"], dict)
    assert data["version"]["number"] == "0.1.2"


@pytest.mark.parametrize("template", TEMPLATES)
def test_template_uses_version_number(render, data, template):
    html = render(template, data)
    assert "v0.1.2" in html
    assert "{'number'" not in html  # no scalar {{ version }} leaking the dict repr


@pytest.mark.parametrize("template", TEMPLATES)
def test_template_surfaces_codename_and_build(render, data, template):
    # The optional version.name / version.build are shown where the version appears.
    html = render(template, data)
    assert data["version"]["name"] in html
    assert data["version"]["build"] in html
