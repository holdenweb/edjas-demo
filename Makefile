# Define the directories
JINJA = templates
TARGET = out

# General rule to convert jinja2 templates to.html files
$(TARGET)/%.html: $(JINJA)/%.html

	uv run python hubris_demo.py demo_data.xlsx \
	| uv run jinja -d - -f json $< > $@ && open $@
