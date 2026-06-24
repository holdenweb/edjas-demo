# HUBRIS Demo

This is about as simple as I can make it to start with.

The generation process is driven by the `templates` directory,
which contains a collection of jinja2 HTML template files.

The following command shows how to create the corresponding
output file in the `dist` directory.

```
poetry run python hubris_demo.py demo_data.xlsx \
    | jinja -d - -f json templates/index.html > dist/index.html \
    && open dist/index.html
```

This is, in fact, what happens when you type

    make dist/index.html

