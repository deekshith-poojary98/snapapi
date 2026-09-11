# SnapAPI Language Support

Syntax highlighting and a run command for SnapAPI `.snaptest` files.

## Features

- Highlights SnapAPI keywords (`SUITE`, `TEST`, `REQUEST`, `DATA`, `HEADERS`, `EXPECT`, `SAVE`, `IMPORT`, …)
- Highlights HTTP methods, assertions (`STATUS`, `JSON`, `HEADER`, `RETRY`), comments (`//`), and `${VAR}` placeholders
- Command **SnapAPI: Run current file** — saves the buffer and runs `snapapi` in a terminal

Install the `snapapi` Python package so the run command can find the CLI (`pip install -e .` from the repo root, with the virtualenv active in your terminal).

## Requirements

- Visual Studio Code 1.91 or later
- SnapAPI CLI on `PATH` for the run command

## License

MIT — see [LICENSE](LICENSE).
