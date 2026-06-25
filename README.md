# HUBRIS Demo

This is about as simple as I can make it to start with.

The generation process is driven by the `templates` directory,
which contains a collection of jinja2 HTML template files.

The following command shows how to create the corresponding
output file in the `out` directory.

```
uv run python hubris_demo.py demo_data.xlsx \
    | uv run jinja -d - -f json templates/index.html > out/index.html \
    && open out/index.html
```

This is, in fact, what happens when you type

    make out/index.html

