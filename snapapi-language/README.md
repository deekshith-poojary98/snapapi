# SnapAPI Language Support

Editor support for [SnapAPI](https://github.com/Deekshith-07/snapapi) `.sapi` files — the HTTP testing DSL. The older `.snaptest` extension still works.

## Features

- Syntax highlighting for current and legacy SnapAPI keywords
- HTTP methods as request aliases (`GET:`, `POST:`, …)
- `AUTH`, `QUERY`, `PARAM`, `HEADER`, `BODY`, `EXPECT`, `SAVE`
- `${VAR}` interpolation, JSON bodies, URLs, and `//` comments
- Snippets for a suite skeleton, GET/POST tests, EXPECT variants, SAVE, AUTH, HEADER, and QUERY
- Keyword completions (and test-name completions after `SETUP:` / `TEARDOWN:` / `SUITE-SETUP:` / `SUITE-TEARDOWN:` / `DEPENDS:`)
- Lightweight diagnostics on edit/save: unknown keywords, missing request paths, unknown SETUP/TEARDOWN/SUITE-SETUP names (including cheap `IMPORT` resolution), `HEAD:` paths, and `REQUEST: OPTIONS /path` (suite `OPTIONS:` remains JSON config)
- **SnapAPI: Run current file** and **SnapAPI: Run test at cursor** (uses `snapapi --name`)
- Status bar + **SnapAPI** output channel for CLI results

Keywords follow the Python parser. Random English words are not highlighted; assertion words like `status` / `contains` only highlight on `EXPECT:` lines.

## Install

### From this repo (debug)

1. Open the `snapapi-language` folder in VS Code / Cursor.
2. Press **F5** to launch an Extension Development Host.
3. Open a `.sapi` file.

If the workspace is the SnapAPI repo root, set the launch argument to `--extensionDevelopmentPath=${workspaceFolder}/snapapi-language`.

### Copy into extensions

```bash
cp -R snapapi-language ~/.vscode/extensions/deekshithpoojary.snapapi-language-0.1.0
```

Restart VS Code / Cursor. For Cursor, use `~/.cursor/extensions/` instead.

### VSIX

From `snapapi-language/`:

```bash
npx @vscode/vsce package
code --install-extension snapapi-language-0.1.0.vsix
```

The CLI must be installed separately (`pip install -e .` from the repo root, or put `snapapi` on your `PATH`).

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `snapapi.cliPath` | `snapapi` | CLI executable. `${workspaceFolder}` is expanded. |
| `snapapi.envFile` | _(empty)_ | Optional `--env` file for `${VAR}` interpolation. |
| `snapapi.profile` | _(empty)_ | Optional `--profile` name (`environments/<name>.env`, `.snapapi/<name>.env`, or `<name>.env`). |

Example for a repo with a virtualenv:

```json
{
  "snapapi.cliPath": "${workspaceFolder}/.venv/bin/snapapi",
  "snapapi.envFile": "${workspaceFolder}/.env"
}
```

## Commands

| Command | Action |
| --- | --- |
| SnapAPI: Run current file | Save and run `snapapi <file>` |
| SnapAPI: Run test at cursor | Save and run `snapapi --name "<TEST>" <file>` using the nearest `TEST:` above the cursor |

Both write to the **SnapAPI** output channel. The status bar shows the last result while a `.sapi` file is active.

## Requirements

- Visual Studio Code 1.91 or later (or a compatible editor)
- SnapAPI CLI for the run commands

## License

MIT — see [LICENSE](LICENSE).
