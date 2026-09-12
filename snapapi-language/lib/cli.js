"use strict";

const fs = require("fs");
const path = require("path");

const DEFAULT_CLI = "snapapi";

function isFile(filePath) {
    try {
        return fs.statSync(filePath).isFile();
    } catch (err) {
        return false;
    }
}

function venvCliCandidates(root) {
    if (process.platform === "win32") {
        return [
            path.join(root, ".venv", "Scripts", "snapapi.exe"),
            path.join(root, "venv", "Scripts", "snapapi.exe"),
        ];
    }
    return [
        path.join(root, ".venv", "bin", "snapapi"),
        path.join(root, "venv", "bin", "snapapi"),
    ];
}

function findOnPath(command, pathEnv) {
    if (!command || command.includes("/") || command.includes("\\")) {
        return null;
    }
    const pathValue = pathEnv == null ? process.env.PATH || "" : pathEnv;
    const sep = process.platform === "win32" ? ";" : ":";
    const exts =
        process.platform === "win32"
            ? String(process.env.PATHEXT || ".EXE;.CMD;.BAT").split(";")
            : [""];
    for (const dir of pathValue.split(sep)) {
        if (!dir) continue;
        for (const ext of exts) {
            const candidate = path.join(dir, command + ext);
            if (isFile(candidate)) return candidate;
        }
    }
    return null;
}

function findVenvCli(cwd) {
    if (!cwd) return null;
    for (const candidate of venvCliCandidates(cwd)) {
        if (isFile(candidate)) return candidate;
    }
    return null;
}

function resolveCliPath(cliPath, cwd, pathEnv) {
    const requested = String(cliPath || DEFAULT_CLI).trim() || DEFAULT_CLI;
    if (requested !== DEFAULT_CLI) {
        if (path.isAbsolute(requested)) return requested;
        return cwd ? path.resolve(cwd, requested) : requested;
    }
    const fromPath = findOnPath(DEFAULT_CLI, pathEnv);
    if (fromPath) return fromPath;
    const fromVenv = findVenvCli(cwd);
    if (fromVenv) return fromVenv;
    return DEFAULT_CLI;
}

function missingCliHint(cliPath, cwd) {
    const lines = [
        `Failed to start ${cliPath}: not found (ENOENT).`,
        "The editor often does not see your shell PATH, so a venv `snapapi` is invisible unless you set snapapi.cliPath.",
        "Install the CLI (`pip install -e .` from the SnapAPI repo) or set snapapi.cliPath to ${workspaceFolder}/.venv/bin/snapapi",
    ];
    const venv = findVenvCli(cwd);
    if (venv) {
        lines.push("Found a workspace CLI at " + venv);
    }
    return lines.join("\n");
}

module.exports = {
    DEFAULT_CLI,
    findOnPath,
    findVenvCli,
    resolveCliPath,
    missingCliHint,
};
