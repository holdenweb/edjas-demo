# Define the directories
PUGS = pug
JINJA = templates
TARGET = out

# General rule to convert jinja2 templates to.html files
$(TARGET)/%.html: $(JINJA)/%.html

	uv run python hubris_demo.py demo_data.xlsx | uv run jinja -d - -f json $< > $@ && open $@

# General rule to convert Pug source to jinja2 templates
$(JINJA)/%.html: $(PUGS)/%.pug

	pug -P -o $(JINJA) $<

# Additional dependencies per specofic targets
$(TARGET)/index.html: $(JINJA)/text_styles1.html $(JINJA)/text_styles2.html
