"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const dsl = require("./dsl");

test("flags unknown keywords", () => {
    const findings = dsl.analyze("SUITE: X\nNOTAKEYWORD: nope\n");
    assert.equal(findings.length, 1);
    assert.match(findings[0].message, /Unknown keyword 'NOTAKEYWORD'/);
});

test("flags GET without a path", () => {
    const findings = dsl.analyze("TEST: T\n  GET:\n");
    assert.ok(findings.some((item) => /GET requires a path/.test(item.message)));
});

test("flags REQUEST without method and path", () => {
    const findings = dsl.analyze("TEST: T\n  REQUEST: GET\n");
    assert.ok(findings.some((item) => /REQUEST must be in the form/.test(item.message)));
});

test("accepts HEAD and REQUEST OPTIONS", () => {
    const findings = dsl.analyze(`TEST: Cors
  HEAD: /x
  REQUEST: OPTIONS /cors
  EXPECT: status == 200
`);
    assert.equal(findings.length, 0);
});

test("flags OPTIONS inside a TEST as HTTP-method confusion", () => {
    const findings = dsl.analyze(`TEST: Cors
  OPTIONS: /cors
`);
    assert.ok(findings.some((item) => /REQUEST: OPTIONS/.test(item.message)));
});

test("flags unknown SETUP names", () => {
    const findings = dsl.analyze("TEST: A\n  GET: /x\nSETUP: Missing\n");
    assert.ok(findings.some((item) => /Unknown SETUP test 'Missing'/.test(item.message)));
});

test("flags unknown DEPENDS names", () => {
    const findings = dsl.analyze("TEST: A\nDEPENDS: Missing\n  GET: /x\n");
    assert.ok(findings.some((item) => /Unknown DEPENDS test 'Missing'/.test(item.message)));
});

test("accepts SETUP names from extraTestNames", () => {
    const findings = dsl.analyze("TEST: A\nSETUP: Imported\n  GET: /x\n", {
        extraTestNames: ["Imported"],
    });
    assert.equal(findings.filter((item) => /SETUP/.test(item.message)).length, 0);
});

test("does not treat JSON keys as keywords", () => {
    const findings = dsl.analyze(`TEST: A
  POST: /x
  BODY: {
    "name": "Jane"
  }
  EXPECT: status == 201
`);
    assert.equal(findings.length, 0);
});

test("findTestNameAt walks up to the nearest TEST", () => {
    const text = "TEST: One\n  GET: /a\nTEST: Two\n  GET: /b\n";
    assert.equal(dsl.findTestNameAt(text, 1).name, "One");
    assert.equal(dsl.findTestNameAt(text, 3).name, "Two");
});

test("accepts current DSL keywords including FILE GRAPHQL EXAMPLES SKIP ONLY QUARANTINE", () => {
    const findings = dsl.analyze(`SUITE: Demo
FOLLOW-REDIRECTS: false
SUITE-SETUP: Login
TEST: Login
  POST: /login
  EXPECT: status == 200
TEST: Upload
SKIP: later
ONLY:
QUARANTINE: flake
EXAMPLES:
  name,email
  Jane,jane@example.com
  POST: /upload
  FILE: avatar FROM ./a.jpg
  GRAPHQL: {"query": "{ user { id } }"}
  EXPECT: status == 200
`);
    assert.equal(findings.length, 0);
});

test("does not flag EXAMPLES table rows as invalid lines", () => {
    const findings = dsl.analyze(`TEST: Create
EXAMPLES:
  name,email
  Jane,jane@example.com
  GET: /x
`);
    assert.equal(findings.length, 0);
});

test("accepts HELPER blocks for SUITE-SETUP", () => {
    const findings = dsl.analyze(`HELPER: Login
  POST: /login
SUITE-SETUP: Login
TEST: A
  GET: /x
`);
    assert.equal(findings.length, 0);
});

test("flags DEPENDS on a HELPER", () => {
    const findings = dsl.analyze(`HELPER: Login
  POST: /login
SUITE-SETUP: Login
TEST: A
DEPENDS: Login
  GET: /x
`);
    assert.ok(findings.some((item) => /HELPER, not a primary TEST/.test(item.message)));
});

test("accepts SUITE-SETUP hyphenated form", () => {
    const findings = dsl.analyze("SUITE-SETUP: Missing\nTEST: A\n  GET: /x\n");
    assert.ok(findings.some((item) => /Unknown SUITE-SETUP test 'Missing'/.test(item.message)));
});

test("rejects space-separated keywords", () => {
    const findings = dsl.analyze("SUITE SETUP: Missing\nTEST: A\n  GET: /x\n");
    assert.ok(findings.some((item) => /Keywords cannot contain spaces; use SUITE-SETUP/.test(item.message)));
});

test("suggests closest known keyword for spaced typos", () => {
    const findings = dsl.analyze("FOLLO REDIRECTS: true\nTEST: A\n  GET: /x\n");
    assert.ok(findings.some((item) => /did you mean FOLLOW-REDIRECTS/.test(item.message)));
});

test("BODY form is not treated as JSON", () => {
    const findings = dsl.analyze(`TEST: A
  POST: /x
  BODY: form username=Jane
  EXPECT: status == 200
`);
    assert.equal(findings.length, 0);
});
