# Change Log

## [0.1.0]

- Treat the extension as a SnapAPI language pack (publisher, snippets, completions, diagnostics)
- Highlight current DSL keywords as line keywords; HTTP methods only as `GET:` / `REQUEST: GET` aliases
- Highlight AUTH schemes, EXPECT check types, `${VAR}`, JSON bodies, URLs, and `//` comments without matching random English words
- Language configuration: `//` comments, bracket auto-close, indentation folding
- Snippets for suite, GET/POST tests, EXPECT, SAVE, AUTH, HEADER, QUERY
- **SnapAPI: Run test at cursor** via `snapapi --name`
- Status bar and SnapAPI output channel for CLI results
- `snapapi.cliPath`, `snapapi.envFile`, and `snapapi.profile` settings
- Diagnostics for FILE, GRAPHQL, EXAMPLES, SKIP, ONLY, QUARANTINE, FOLLOW-REDIRECTS, SUITE SETUP, WAIT, SET
- Remove Yeoman leftover docs and unused devDependencies

## [0.0.2]

- Highlight HEADERS, SAVE, IMPORT, JSON, HEADER, RETRY, FROM, and `${VAR}`
- Add **SnapAPI: Run current file** command

## [0.0.1]

- Initial release with `.snaptest` syntax highlighting
