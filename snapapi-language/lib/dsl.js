"use strict";

const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"];
const HTTP_METHOD_SET = new Set(HTTP_METHODS);
const REQUEST_METHODS = new Set([...HTTP_METHODS, "OPTIONS"]);
const KNOWN_KEYWORDS = new Set([
    "SUITE",
    "DESC",
    "URL",
    "OPTIONS",
    "TIMEOUT",
    "STOP-ON-FAILURE",
    "FOLLOW-REDIRECTS",
    "SUITE-SETUP",
    "SUITE-TEARDOWN",
    "TEST",
    "TAG",
    "SETUP",
    "TEARDOWN",
    "REQUEST",
    "DATA",
    "BODY",
    "HEADERS",
    "HEADER",
    "QUERY",
    "PARAM",
    "AUTH",
    "EXPECT",
    "SAVE",
    "IMPORT",
    "FILE",
    "GRAPHQL",
    "EXAMPLES",
    "SKIP",
    "ONLY",
    "QUARANTINE",
    "WAIT",
    "SET",
    ...HTTP_METHODS,
]);
const JSON_VALUE_KEYWORDS = new Set(["OPTIONS", "DATA", "BODY", "HEADERS", "GRAPHQL"]);
const AUTH_SCHEMES = ["bearer", "basic", "digest", "token", "oauth2"];
const LINE_KEYWORD_RE = /^([A-Z][A-Z0-9_-]*):(.*)$/;
const HEADER_LINE_RE = /^HEADER\s+(\S+)\s*:\s*(.*)$/;
const SUITE_HOOK_RE = /^SUITE\s+(SETUP|TEARDOWN):\s*(.*)$/i;

const KEYWORD_COMPLETIONS = [
    { label: "SUITE", detail: "Suite name" },
    { label: "DESC", detail: "Suite or test description" },
    { label: "URL", detail: "Base URL" },
    { label: "TIMEOUT", detail: "HTTP timeout in seconds" },
    { label: "STOP-ON-FAILURE", detail: "Stop the suite on the first failure" },
    { label: "FOLLOW-REDIRECTS", detail: "Follow HTTP redirects" },
    { label: "SUITE SETUP", detail: "Run a helper TEST before the suite" },
    { label: "OPTIONS", detail: "Suite options JSON (legacy)" },
    { label: "IMPORT", detail: "Pull tests from another .snaptest file" },
    { label: "TEST", detail: "Start a test" },
    { label: "TAG", detail: "Tags for the current test" },
    { label: "SETUP", detail: "Run another TEST first" },
    { label: "TEARDOWN", detail: "Run another TEST after" },
    { label: "SKIP", detail: "Skip this test" },
    { label: "ONLY", detail: "Run only this test" },
    { label: "QUARANTINE", detail: "Quarantine this test" },
    { label: "EXAMPLES", detail: "CSV rows that expand into tests" },
    { label: "REQUEST", detail: "METHOD /path (use REQUEST: OPTIONS /path for HTTP OPTIONS)" },
    { label: "GET", detail: "GET request" },
    { label: "POST", detail: "POST request" },
    { label: "PUT", detail: "PUT request" },
    { label: "PATCH", detail: "PATCH request" },
    { label: "DELETE", detail: "DELETE request" },
    { label: "HEAD", detail: "HEAD request" },
    { label: "BODY", detail: "JSON, form, or raw body" },
    { label: "DATA", detail: "JSON body (legacy alias of BODY)" },
    { label: "FILE", detail: "Multipart file field FROM path" },
    { label: "GRAPHQL", detail: "GraphQL JSON payload" },
    { label: "HEADER", detail: "Single header" },
    { label: "HEADERS", detail: "Headers JSON (legacy)" },
    { label: "QUERY", detail: "Query string on the current request" },
    { label: "PARAM", detail: "Single query parameter" },
    { label: "AUTH", detail: "Authorization header or oauth2" },
    { label: "EXPECT", detail: "Assertion on the current request" },
    { label: "SAVE", detail: "Store a JSONPath/header/cookie value as ${name}" },
    { label: "WAIT", detail: "Poll the request until a JSONPath matches" },
    { label: "SET", detail: "Assign a variable without HTTP" },
];

function jsonComplete(text) {
    let inString = false;
    let escape = false;
    let depth = 0;
    let started = false;
    for (const char of text) {
        if (inString) {
            if (escape) {
                escape = false;
            } else if (char === "\\") {
                escape = true;
            } else if (char === '"') {
                inString = false;
            }
            continue;
        }
        if (/\s/.test(char)) {
            continue;
        }
        if (char === '"') {
            inString = true;
            started = true;
            continue;
        }
        if (char === "{" || char === "[") {
            depth += 1;
            started = true;
        } else if (char === "}" || char === "]") {
            depth -= 1;
            if (depth < 0) {
                return false;
            }
            started = true;
        } else {
            started = true;
        }
    }
    return started && depth === 0 && !inString;
}

function isCommentOrBlank(stripped) {
    return !stripped || stripped.startsWith("//");
}

function splitKeyword(stripped) {
    const hook = SUITE_HOOK_RE.exec(stripped);
    if (hook) {
        return {
            keyword: `SUITE-${hook[1].toUpperCase()}`,
            rest: hook[2].trim(),
            form: "suite-hook",
        };
    }
    const match = LINE_KEYWORD_RE.exec(stripped);
    if (match) {
        return { keyword: match[1], rest: match[2].trim(), form: "colon" };
    }
    const header = HEADER_LINE_RE.exec(stripped);
    if (header) {
        return {
            keyword: "HEADER",
            rest: `${header[1]}: ${header[2]}`,
            form: "header-line",
        };
    }
    return null;
}

function keywordIndex(lineText, keyword) {
    const start = lineText.indexOf(keyword);
    if (start < 0) {
        const trimmed = lineText.search(/\S/);
        return { start: Math.max(trimmed, 0), end: lineText.length };
    }
    return { start, end: start + keyword.length };
}

function restIndex(lineText, keyword) {
    const found = keywordIndex(lineText, keyword);
    const colon = lineText.indexOf(":", found.end);
    if (colon < 0) {
        return { start: found.end, end: lineText.length };
    }
    let start = colon + 1;
    while (start < lineText.length && lineText[start] === " ") {
        start += 1;
    }
    return { start, end: lineText.length };
}

function walkLines(text, handlers) {
    const lines = text.split(/\r?\n/);
    let jsonBuf = null;
    let jsonMeta = null;
    let inExamples = false;

    const flushJson = () => {
        if (jsonBuf === null || !jsonMeta) {
            return;
        }
        if (handlers.onJson) {
            handlers.onJson({
                keyword: jsonMeta.keyword,
                line: jsonMeta.line,
                text: jsonMeta.text,
                value: jsonBuf,
                complete: jsonComplete(jsonBuf),
            });
        }
        jsonBuf = null;
        jsonMeta = null;
    };

    for (let i = 0; i < lines.length; i += 1) {
        const lineText = lines[i];
        const stripped = lineText.trim();
        if (jsonBuf !== null) {
            if (isCommentOrBlank(stripped)) {
                continue;
            }
            jsonBuf += `\n${lineText}`;
            if (jsonComplete(jsonBuf)) {
                flushJson();
            }
            continue;
        }
        if (isCommentOrBlank(stripped)) {
            continue;
        }
        const parsed = splitKeyword(stripped);
        if (!parsed) {
            if (inExamples) {
                continue;
            }
            if (handlers.onInvalid) {
                handlers.onInvalid({ line: i, text: lineText, stripped });
            }
            continue;
        }
        inExamples = parsed.keyword === "EXAMPLES";
        if (handlers.onKeyword) {
            handlers.onKeyword({
                line: i,
                text: lineText,
                stripped,
                keyword: parsed.keyword,
                rest: parsed.rest,
                form: parsed.form,
            });
        }
        if (JSON_VALUE_KEYWORDS.has(parsed.keyword)) {
            const lower = parsed.rest.toLowerCase();
            if ((parsed.keyword === "BODY" || parsed.keyword === "DATA") && (lower.startsWith("form") || lower.startsWith("raw"))) {
                continue;
            }
            jsonBuf = parsed.rest;
            jsonMeta = { keyword: parsed.keyword, line: i, text: lineText };
            if (jsonComplete(jsonBuf)) {
                flushJson();
            }
        }
    }
    if (jsonBuf !== null) {
        flushJson();
    }
}

function collectTestNames(text) {
    const names = [];
    walkLines(text, {
        onKeyword(event) {
            if (event.keyword === "TEST" && event.rest) {
                names.push(event.rest);
            }
        },
    });
    return names;
}

function findImports(text) {
    const imports = [];
    walkLines(text, {
        onKeyword(event) {
            if (event.keyword === "IMPORT") {
                imports.push({
                    spec: event.rest,
                    line: event.line,
                    text: event.text,
                    keyword: event.keyword,
                });
            }
        },
    });
    return imports;
}

function findTestNameAt(text, lineNumber) {
    const lines = text.split(/\r?\n/);
    const start = Math.min(Math.max(lineNumber, 0), Math.max(lines.length - 1, 0));
    for (let i = start; i >= 0; i -= 1) {
        const stripped = lines[i].trim();
        if (isCommentOrBlank(stripped)) {
            continue;
        }
        const parsed = splitKeyword(stripped);
        if (parsed && parsed.keyword === "TEST" && parsed.rest) {
            return { name: parsed.rest, line: i };
        }
    }
    return null;
}

function analyze(text, options = {}) {
    const diagnostics = [];
    const extraTestNames = new Set(options.extraTestNames || []);
    const testNames = new Set([...collectTestNames(text), ...extraTestNames]);
    const seenTests = new Set();
    const checkImport = options.checkImport;
    let inTest = false;

    const push = (event, message, range) => {
        const span = range || keywordIndex(event.text, event.keyword || "");
        diagnostics.push({
            line: event.line,
            start: span.start,
            end: span.end,
            message,
            severity: "error",
        });
    };

    walkLines(text, {
        onInvalid(event) {
            const start = Math.max(event.text.search(/\S/), 0);
            diagnostics.push({
                line: event.line,
                start,
                end: event.text.length,
                message: "Invalid line (expected KEYWORD: value)",
                severity: "error",
            });
        },
        onKeyword(event) {
            if (!KNOWN_KEYWORDS.has(event.keyword)) {
                push(event, `Unknown keyword '${event.keyword}'`);
                return;
            }
            if (event.keyword === "TEST") {
                inTest = true;
                if (!event.rest) {
                    push(event, "TEST name is required");
                } else if (seenTests.has(event.rest)) {
                    push(event, `Duplicate test name '${event.rest}'`, restIndex(event.text, "TEST"));
                } else {
                    seenTests.add(event.rest);
                }
            } else if (event.keyword === "SUITE") {
                inTest = false;
            } else if (
                event.keyword === "SETUP" ||
                event.keyword === "TEARDOWN" ||
                event.keyword === "SUITE-SETUP" ||
                event.keyword === "SUITE-TEARDOWN"
            ) {
                if (event.rest && !testNames.has(event.rest)) {
                    push(
                        event,
                        `Unknown ${event.keyword.replace("-", " ")} test '${event.rest}'`,
                        restIndex(event.text, event.keyword === "SUITE-SETUP" || event.keyword === "SUITE-TEARDOWN" ? "SUITE" : event.keyword),
                    );
                }
            } else if (event.keyword === "OPTIONS" && inTest) {
                push(
                    event,
                    "OPTIONS at suite level is JSON config; use REQUEST: OPTIONS /path for the HTTP method",
                    restIndex(event.text, "OPTIONS"),
                );
            } else if (event.keyword === "REQUEST") {
                const parts = event.rest.split(/\s+/, 2);
                if (parts.length !== 2 || !parts[0] || !parts[1]) {
                    push(event, "REQUEST must be in the form: METHOD /path", restIndex(event.text, "REQUEST"));
                } else if (!REQUEST_METHODS.has(parts[0].toUpperCase())) {
                    push(event, `Unknown HTTP method '${parts[0].toUpperCase()}'`, restIndex(event.text, "REQUEST"));
                }
            } else if (HTTP_METHOD_SET.has(event.keyword) && !event.rest) {
                push(event, `${event.keyword} requires a path`);
            } else if (event.keyword === "IMPORT") {
                if (!event.rest) {
                    push(event, "IMPORT path is required");
                } else if (checkImport) {
                    const error = checkImport(event.rest);
                    if (error) {
                        push(event, error, restIndex(event.text, "IMPORT"));
                    }
                }
            }
        },
        onJson(event) {
            if (!event.complete) {
                push(event, "Unterminated JSON value");
                return;
            }
            let parsed;
            try {
                parsed = JSON.parse(event.value);
            } catch (err) {
                const detail = err && err.message ? err.message.split("\n")[0] : "invalid";
                push(event, `Invalid JSON: ${detail}`);
                return;
            }
            if (
                (event.keyword === "OPTIONS" || event.keyword === "HEADERS") &&
                (parsed === null || typeof parsed !== "object" || Array.isArray(parsed))
            ) {
                push(event, `${event.keyword} must be a JSON object`);
            }
        },
    });

    return diagnostics;
}

module.exports = {
    AUTH_SCHEMES,
    HTTP_METHODS,
    REQUEST_METHODS,
    KEYWORD_COMPLETIONS,
    KNOWN_KEYWORDS,
    analyze,
    collectTestNames,
    findImports,
    findTestNameAt,
};
