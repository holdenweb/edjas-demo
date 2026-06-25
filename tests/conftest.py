"""Shared pytest fixtures for the demo's rendered-output checks.

Tests verify rendered output (not units): they render the templates against the
real data hubris extracts from the demo workbooks, and assert on the HTML.
"""
import os

import jinja2
import pytest
from hubris.read_params import read_file

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session")
def env():
    # autoescape off to match the jinja CLI used by the Makefile.
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(os.path.join(ROOT, "templates")),
        autoescape=False,
    )


@pytest.fixture(scope="session")
def data():
    return read_file(os.path.join(ROOT, "demo_data.xlsx"))


@pytest.fixture(scope="session")
def sample_data():
    return read_file(os.path.join(ROOT, "levels_sample.xlsx"))


@pytest.fixture
def render(env):
    def _render(template, data):
        return env.get_template(template).render(**data)

    return _render
