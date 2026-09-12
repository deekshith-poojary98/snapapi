# SnapAPI user guide

Static HTML — no build step.

## Open locally

Open `docs/index.html` in a browser (double-click or File → Open). Relative scripts load over `file://`.

Or serve the folder:

```bash
python3 -m http.server 8000 --directory docs
```

Then visit [http://127.0.0.1:8000/](http://127.0.0.1:8000/) and [http://127.0.0.1:8000/playground.html](http://127.0.0.1:8000/playground.html).

## Pages

| File | Topic |
| --- | --- |
| `index.html` | Pitch and 30-second example |
| `guide/install.html` | venv and `pip install -e ".[dev]"` |
| `guide/quick-start.html` | First `.sapi` + `snapapi` |
| `guide/dsl.html` | Keywords, expects, SAVE, WAIT |
| `guide/cli.html` | run, lint, fmt, openapi, history, mock, watch |
| `guide/ci.html` | JUnit, HTML, VCR, `--safe-url` |
| `guide/vscode.html` | `snapapi-language` extension |
| `playground.html` | In-browser interpreter + mock API |

The playground JavaScript is `assets/playground.js`. Default mode never calls the network.
