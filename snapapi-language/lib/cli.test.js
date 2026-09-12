"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const cli = require("./cli");

function makeTree() {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "snapapi-cli-"));
    const bin = path.join(root, ".venv", "bin");
    fs.mkdirSync(bin, { recursive: true });
    const script = path.join(bin, "snapapi");
    fs.writeFileSync(script, "#!/bin/sh\n");
    fs.chmodSync(script, 0o755);
    return { root, script };
}

test("resolveCliPath uses PATH when snapapi is on it", () => {
    const { root, script } = makeTree();
    const resolved = cli.resolveCliPath("snapapi", "/tmp", path.dirname(script));
    assert.equal(resolved, script);
    fs.rmSync(root, { recursive: true, force: true });
});

test("resolveCliPath falls back to workspace .venv", () => {
    const { root, script } = makeTree();
    const resolved = cli.resolveCliPath("snapapi", root, "");
    assert.equal(resolved, script);
    fs.rmSync(root, { recursive: true, force: true });
});

test("resolveCliPath keeps an explicit path", () => {
    const resolved = cli.resolveCliPath("/opt/snapapi", "/tmp", "");
    assert.equal(resolved, "/opt/snapapi");
});

test("missingCliHint mentions cliPath", () => {
    const hint = cli.missingCliHint("snapapi", "/tmp");
    assert.match(hint, /ENOENT/);
    assert.match(hint, /snapapi\.cliPath/);
});
