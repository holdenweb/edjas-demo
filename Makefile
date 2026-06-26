# Define the directories
JINJA = templates
TARGET = out

# General rule to convert jinja2 templates to.html files
$(TARGET)/%.html: $(JINJA)/%.html

	uv run python hubris_demo.py demo_data.xlsx \
	| uv run jinja -d - -f json $< > $@

# Plain-text copies of the templates, served as text/plain so the index's
# "Source" links show the markup instead of the browser rendering it. A UTF-8
# BOM is prepended so browsers decode the markup correctly even when the server
# sends text/plain without a charset.
$(TARGET)/%.html.txt: $(JINJA)/%.html
	printf '\357\273\277' > $@ && cat $< >> $@

# The landing page links to those source copies, so build them alongside it.
$(TARGET)/index.html: $(TARGET)/simple.html.txt $(TARGET)/levels.html.txt $(TARGET)/dashboard.html.txt
