"""Rendered-output check: version is a nested {number} dict.

Driven by the hubris flatten fix (a one-entry dict is no longer flattened to a
vector), version is {number: '0.1.2'} and every template renders version.number.
"""
import pytest

TEMPLATES = ["levels.html", "dashboard.html", "simple.html", "index.html"]


def test_version_is_nested_dict(data):
    assert data["version"] == {"number": "0.1.2"}


@pytest.mark.parametrize("template", TEMPLATES)
def test_template_uses_version_number(render, data, template):
    # col_values is only needed by the legacy index.html's second table.
    html = render(template, {**data, "col_values": []})
    assert "v0.1.2" in html
    assert "{'number'" not in html  # no scalar {{ version }} leaking the dict repr
