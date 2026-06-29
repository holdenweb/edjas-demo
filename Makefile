# Define the directories
JINJA = sets/default
TARGET = out

# Default target: bring all generated documentation (the demo pages, with their
# linked source copies) up to date.
.PHONY: docs
docs: $(TARGET)/index.html $(TARGET)/simple.html $(TARGET)/levels.html $(TARGET)/dashboard.html

# General rule to convert jinja2 templates to.html files
$(TARGET)/%.html: $(JINJA)/%.html data/demo_data.xlsx

	uv run edjas data/demo_data.xlsx \
	| uv run jinja -d - -f json $< > $@

# Plain-text copies of the templates, served as text/plain so the index's
# "Source" links show the markup instead of the browser rendering it. A UTF-8
# BOM is prepended so browsers decode the markup correctly even when the server
# sends text/plain without a charset.
$(TARGET)/%.html.txt: $(JINJA)/%.html
	printf '\357\273\277' > $@ && cat $< >> $@

# The landing page links to those source copies, so build them alongside it.
$(TARGET)/index.html: $(TARGET)/simple.html.txt $(TARGET)/levels.html.txt $(TARGET)/dashboard.html.txt

# Live demo server: renders the currently-selected template set + spreadsheet on
# the fly, with a sidebar to switch sets/data and auto-refresh on edits. Sets live
# under sets/<name>/ and spreadsheets under data/.
.PHONY: serve
serve:
	uv run python serve.py
