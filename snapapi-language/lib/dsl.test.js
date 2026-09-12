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

test("flags unknown SETUP names", () => {
    const findings = dsl.analyze("TEST: A\n  GET: /x\nSETUP: Missing\n");
    assert.ok(findings.some((item) => /Unknown SETUP test 'Missing'/.test(item.message)));
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

test("ignores comment lines", () => {
    const findings = dsl.analyze("// NOTAKEYWORD: nope\nSUITE: X\n");
    assert.equal(findings.length, 0);
});
