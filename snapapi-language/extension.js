"use strict";

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const vscode = require("vscode");
const dsl = require("./lib/dsl");
const cli = require("./lib/cli");

const LANGUAGE_ID = "snaptest";
const SUITE_EXTENSIONS = [".sapi", ".snaptest"];

function isSuiteFileName(fileName) {
    const lower = String(fileName || "").toLowerCase();
    return SUITE_EXTENSIONS.some((ext) => lower.endsWith(ext));
}
const OUTPUT_NAME = "SnapAPI";

let outputChannel;
let diagnosticCollection;
let statusBar;
let debounceTimer;
let running = false;

function activate(context) {
    outputChannel = vscode.window.createOutputChannel(OUTPUT_NAME);
    diagnosticCollection = vscode.languages.createDiagnosticCollection("snaptest");
    statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
    statusBar.command = "snapapi.runCurrentFile";
    statusBar.text = "$(beaker) SnapAPI";
    statusBar.tooltip = "Run current SnapAPI file";

    context.subscriptions.push(
        outputChannel,
        diagnosticCollection,
        statusBar,
        vscode.commands.registerCommand("snapapi.runCurrentFile", () => runActive({ atCursor: false })),
        vscode.commands.registerCommand("snapapi.runTestAtCursor", () => runActive({ atCursor: true })),
        vscode.languages.registerCompletionItemProvider(LANGUAGE_ID, { provideCompletionItems }),
        vscode.workspace.onDidChangeTextDocument((event) => {
            if (event.document.languageId === LANGUAGE_ID) {
                scheduleDiagnostics(event.document);
            }
        }),
        vscode.workspace.onDidSaveTextDocument((document) => {
            if (document.languageId === LANGUAGE_ID) {
                refreshDiagnostics(document);
            }
        }),
        vscode.workspace.onDidOpenTextDocument((document) => {
            if (document.languageId === LANGUAGE_ID) {
                refreshDiagnostics(document);
            }
        }),
        vscode.workspace.onDidCloseTextDocument((document) => {
            diagnosticCollection.delete(document.uri);
        }),
        vscode.window.onDidChangeActiveTextEditor(updateStatusBarVisibility),
    );

    vscode.workspace.textDocuments.forEach((document) => {
        if (document.languageId === LANGUAGE_ID) {
            refreshDiagnostics(document);
        }
    });
    updateStatusBarVisibility(vscode.window.activeTextEditor);
}

function deactivate() {
    if (debounceTimer) {
        clearTimeout(debounceTimer);
    }
}

function updateStatusBarVisibility(editor) {
    if (editor && editor.document.languageId === LANGUAGE_ID) {
        statusBar.show();
    } else {
        statusBar.hide();
    }
}

function scheduleDiagnostics(document) {
    if (debounceTimer) {
        clearTimeout(debounceTimer);
    }
    debounceTimer = setTimeout(() => refreshDiagnostics(document), 250);
}

function refreshDiagnostics(document) {
    if (document.languageId !== LANGUAGE_ID) {
        return;
    }
    const extraTestNames = collectImportedTestNames(document);
    const importErrors = {};
    if (document.uri.scheme === "file") {
        const seen = new Set();
        try {
            seen.add(fs.realpathSync(document.uri.fsPath));
        } catch {
            seen.add(path.resolve(document.uri.fsPath));
        }
        for (const item of dsl.findImports(document.getText())) {
            if (!item.spec) {
                continue;
            }
            importErrors[item.spec] = checkImport(document.uri.fsPath, item.spec, seen);
        }
    }
    const findings = dsl.analyze(document.getText(), {
        extraTestNames,
        checkImport: (spec) => importErrors[spec] || null,
    });
    const diagnostics = findings.map((item) => {
        const range = new vscode.Range(item.line, item.start, item.line, item.end);
        const severity = vscode.DiagnosticSeverity.Error;
        const diagnostic = new vscode.Diagnostic(range, item.message, severity);
        diagnostic.source = "snapapi";
        return diagnostic;
    });
    diagnosticCollection.set(document.uri, diagnostics);
}

function collectImportedTestNames(document) {
    const names = new Set();
    if (document.uri.scheme !== "file") {
        return names;
    }
    const seen = new Set();
    try {
        seen.add(fs.realpathSync(document.uri.fsPath));
    } catch {
        seen.add(path.resolve(document.uri.fsPath));
    }
    gatherImportedNames(document.uri.fsPath, document.getText(), seen, names);
    return names;
}

function gatherImportedNames(fromPath, text, seen, names) {
    for (const item of dsl.findImports(text)) {
        if (!item.spec) {
            continue;
        }
        const target = path.resolve(path.dirname(fromPath), item.spec);
        if (!fs.existsSync(target) || !fs.statSync(target).isFile()) {
            continue;
        }
        let resolved = target;
        try {
            resolved = fs.realpathSync(target);
        } catch {
            continue;
        }
        if (seen.has(resolved)) {
            continue;
        }
        seen.add(resolved);
        let imported;
        try {
            imported = fs.readFileSync(resolved, "utf8");
        } catch {
            continue;
        }
        for (const name of dsl.collectTestNames(imported)) {
            names.add(name);
        }
        gatherImportedNames(resolved, imported, seen, names);
    }
}

function checkImport(fromPath, spec, seen) {
    const target = path.resolve(path.dirname(fromPath), spec);
    if (!fs.existsSync(target) || !fs.statSync(target).isFile()) {
        return `IMPORT file not found: ${spec}`;
    }
    try {
        const resolved = fs.realpathSync(target);
        if (seen.has(resolved)) {
            return "IMPORT cycle detected";
        }
    } catch {
        return `IMPORT file not found: ${spec}`;
    }
    return null;
}

function provideCompletionItems(document, position) {
    const line = document.lineAt(position).text;
    const prefix = line.slice(0, position.character);
    const trimmed = prefix.trim();
    const items = [];

    const expectMatch = /^EXPECT:\s*(.*)$/i.exec(trimmed);
    if (expectMatch) {
        return expectCompletions(expectMatch[1]);
    }
    const authMatch = /^AUTH:\s*(.*)$/i.exec(trimmed);
    if (authMatch && !authMatch[1].trim()) {
        return schemeCompletions();
    }
    const setupMatch = /^(SETUP|TEARDOWN|SUITE-SETUP|SUITE-TEARDOWN|DEPENDS):\s*(.*)$/.exec(trimmed);
    if (setupMatch) {
        return testNameCompletions(document, setupMatch[2]);
    }
    const requestMatch = /^REQUEST:\s*(.*)$/.exec(trimmed);
    if (requestMatch) {
        return methodCompletions(requestMatch[1]);
    }
    if (trimmed.includes(":")) {
        return items;
    }

    for (const keyword of dsl.KEYWORD_COMPLETIONS) {
        const item = new vscode.CompletionItem(keyword.label, vscode.CompletionItemKind.Keyword);
        item.detail = keyword.detail;
        item.insertText = new vscode.SnippetString(`${keyword.label}: $0`);
        item.sortText = `0-${keyword.label}`;
        items.push(item);
    }
    return items;
}

function expectCompletions(rest) {
    const snippets = [
        ["status == 200", "EXPECT: status == ${1:200}"],
        ["body contains", "EXPECT: body contains ${1:text}"],
        ["json path", "EXPECT: json ${1:$.path} == ${2:\"value\"}"],
        ["json empty", "EXPECT: json ${1:$.items} empty"],
        ["json type", "EXPECT: json ${1:$.id} type ${2:string}"],
        ["header contains", "EXPECT: header ${1:Content-Type} contains ${2:json}"],
        ["status OR", "EXPECT: status == ${1:400} OR status == ${2:401}"],
        ["AND / OR group", "EXPECT: (status == ${1:400} OR status == ${2:401}) AND json ${3:$.success} == false"],
        ["BECAUSE", "EXPECT: json $.ok == true BECAUSE \"${1:reason}\""],
        ["STATUS 200", "EXPECT: STATUS ${1:200}"],
        ["CONTAINS", "EXPECT: CONTAINS ${1:text}"],
        ["RETRY", "EXPECT: status == ${1:200} RETRY ${2:5}"],
    ];
    return snippets.map(([label, insert], index) => {
        const item = new vscode.CompletionItem(label, vscode.CompletionItemKind.Snippet);
        item.insertText = new vscode.SnippetString(insert);
        item.filterText = `EXPECT: ${label}`;
        item.range = rest !== undefined ? undefined : undefined;
        item.sortText = `0-${index}-${label}`;
        return item;
    });
}

function schemeCompletions() {
    return dsl.AUTH_SCHEMES.map((scheme) => {
        const item = new vscode.CompletionItem(scheme, vscode.CompletionItemKind.EnumMember);
        item.insertText = new vscode.SnippetString(`${scheme} \${1:TOKEN}`);
        item.detail = "AUTH scheme";
        return item;
    });
}

function testNameCompletions(document, typed) {
    const extra = collectImportedTestNames(document);
    const names = [...dsl.collectTestNames(document.getText()), ...extra];
    const unique = [...new Set(names)];
    return unique
        .filter((name) => !typed || name.toLowerCase().includes(typed.toLowerCase()))
        .map((name) => {
            const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Reference);
            item.detail = "TEST name";
            return item;
        });
}

function methodCompletions(rest) {
    if (rest && rest.trim()) {
        return [];
    }
    return dsl.HTTP_METHODS.map((method) => {
        const item = new vscode.CompletionItem(method, vscode.CompletionItemKind.Keyword);
        item.insertText = new vscode.SnippetString(`${method} \${1:/path}`);
        item.detail = "HTTP method";
        return item;
    });
}

async function runActive({ atCursor }) {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        vscode.window.showWarningMessage("Open a .sapi file to run SnapAPI.");
        return;
    }
    if (editor.document.languageId !== LANGUAGE_ID && !isSuiteFileName(editor.document.fileName)) {
        vscode.window.showWarningMessage("SnapAPI can only run .sapi files.");
        return;
    }
    if (editor.document.uri.scheme !== "file") {
        vscode.window.showWarningMessage("Save the .sapi file before running SnapAPI.");
        return;
    }
    await editor.document.save();
    const file = editor.document.uri.fsPath;
    const args = [];
    if (atCursor) {
        const found = dsl.findTestNameAt(editor.document.getText(), editor.selection.active.line);
        if (!found) {
            vscode.window.showWarningMessage("No TEST: found above the cursor.");
            return;
        }
        args.push("--name", found.name);
    }
    await runCli(file, args, editor.document.uri);
}

function resolveSettingPath(value) {
    if (!value || typeof value !== "string") {
        return value;
    }
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || !folders.length) {
        return value;
    }
    return value.replace(/\$\{workspaceFolder\}/g, folders[0].uri.fsPath);
}

function cliArgs(file, extraArgs) {
    const config = vscode.workspace.getConfiguration("snapapi");
    const args = [];
    const envFile = resolveSettingPath((config.get("envFile") || "").trim());
    if (envFile) {
        args.push("--env", envFile);
    }
    const profile = (config.get("profile") || "").trim();
    if (profile) {
        args.push("--profile", profile);
    }
    args.push(...extraArgs);
    args.push(file);
    return args;
}

function runCli(file, extraArgs, uri) {
    if (running) {
        vscode.window.showWarningMessage("SnapAPI is already running.");
        return Promise.resolve();
    }
    const config = vscode.workspace.getConfiguration("snapapi");
    const args = cliArgs(file, extraArgs);
    const folder = uri ? vscode.workspace.getWorkspaceFolder(uri) : undefined;
    const cwd = folder ? folder.uri.fsPath : path.dirname(file);
    const cliPath = cli.resolveCliPath(resolveSettingPath(config.get("cliPath") || "snapapi"), cwd);

    running = true;
    statusBar.text = "$(sync~spin) SnapAPI";
    statusBar.tooltip = "Running SnapAPI…";
    outputChannel.clear();
    outputChannel.show(true);
    outputChannel.appendLine(`$ ${cliPath} ${args.map(quoteArg).join(" ")}`);
    outputChannel.appendLine("");

    return new Promise((resolve) => {
        let child;
        try {
            child = spawn(cliPath, args, {
                cwd,
                env: process.env,
                shell: process.platform === "win32",
            });
        } catch (err) {
            running = false;
            const message = err && err.message ? err.message : String(err);
            finishRun(1, startErrorMessage(cliPath, cwd, message));
            resolve();
            return;
        }

        child.stdout.on("data", (chunk) => outputChannel.append(chunk.toString()));
        child.stderr.on("data", (chunk) => outputChannel.append(chunk.toString()));
        child.on("error", (err) => {
            running = false;
            finishRun(1, startErrorMessage(cliPath, cwd, err.message));
            resolve();
        });
        child.on("close", (code) => {
            running = false;
            const exit = code === null ? 1 : code;
            finishRun(exit);
            resolve();
        });
    });
}

function finishRun(code, extraMessage) {
    if (extraMessage) {
        outputChannel.appendLine(extraMessage);
    }
    if (code === 0) {
        statusBar.text = "$(check) SnapAPI passed";
        statusBar.tooltip = "Last SnapAPI run passed";
        statusBar.backgroundColor = undefined;
    } else {
        statusBar.text = code === 2 ? "$(error) SnapAPI error" : "$(error) SnapAPI failed";
        statusBar.tooltip = extraMessage || `Last SnapAPI run exited ${code}`;
        statusBar.backgroundColor = new vscode.ThemeColor("statusBarItem.errorBackground");
    }
    updateStatusBarVisibility(vscode.window.activeTextEditor);
}

function startErrorMessage(cliPath, cwd, message) {
    if (/ENOENT/i.test(message || "")) {
        return cli.missingCliHint(cliPath, cwd);
    }
    return `Failed to start ${cliPath}: ${message}`;
}

function quoteArg(value) {
    if (/[\s"]/.test(value)) {
        return `"${value.replace(/"/g, '\\"')}"`;
    }
    return value;
}

module.exports = { activate, deactivate };
