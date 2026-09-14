const hl = require("./highlight.js");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function testKeywords() {
  const html = hl.highlightSnaptest("TEST: Foo\n  GET: /x");
  assert(/class="tok-kw">TEST</.test(html), "TEST should be a keyword:\n" + html);
  assert(/class="tok-kw">GET</.test(html), "GET should be a keyword:\n" + html);
  assert(/class="tok-p">:</.test(html), "colon should be punctuation:\n" + html);
}

function testTokens() {
  const src = [
    "SUITE: Demo",
    "// a comment",
    "  POST: /users/${userId}",
    '  EXPECT: json $.email == "Ada"',
    "  EXPECT: status != 500",
    "  HEADER Accept: application/json",
  ].join("\n");
  const html = hl.highlightSnaptest(src);
  assert(/class="tok-cmt">\/\/ a comment</.test(html), "comment:\n" + html);
  assert(/class="tok-var">\$\{userId\}</.test(html), "interpolation:\n" + html);
  assert(/class="tok-path">\$\.email</.test(html), "jsonpath:\n" + html);
  assert(/class="tok-op">==</.test(html), "==:\n" + html);
  assert(/class="tok-op">!=</.test(html), "!=:\n" + html);
  assert(/class="tok-str">"Ada"</.test(html), "string:\n" + html);
  assert(/class="tok-num">500</.test(html), "status number:\n" + html);
  assert(/class="tok-kw">HEADER</.test(html), "HEADER:\n" + html);
}

function testEscape() {
  const html = hl.highlightSnaptest("EXPECT: body contains <script> & more");
  assert(html.indexOf("<script>") === -1, "must escape tags:\n" + html);
  assert(/&lt;script&gt;/.test(html), "escaped lt:\n" + html);
  assert(/&amp;/.test(html), "escaped amp:\n" + html);
}

function testRequestMethod() {
  const html = hl.highlightSnaptest("REQUEST: GET /users\nEXPECT: status == 200");
  assert(/class="tok-kw">REQUEST</.test(html), "REQUEST keyword:\n" + html);
  assert(/class="tok-kw">GET</.test(html), "GET after REQUEST:\n" + html);
  assert(/class="tok-aux">status</.test(html), "status aux:\n" + html);
}

function testBoolOps() {
  const html = hl.highlightSnaptest("EXPECT: status == 400 OR status == 401");
  assert(/class="tok-aux">OR</.test(html), "OR should highlight:\n" + html);
}

function testDetect() {
  assert(hl.looksLikeSnaptest("TEST: Foo\n  GET: /x"), "detect snaptest");
  assert(hl.detectLanguage("def test_suite(snapapi_run):", "") === "python", "detect python");
  assert(hl.detectLanguage("snapapi tests/ --safe-url", "") === "bash", "detect bash");
}

function testNewExpectOps() {
  const html = hl.highlightSnaptest('EXPECT: json $.items empty BECAUSE "reason"\nEXPECT: json $.tags contains-only ["a"]');
  assert(/class="tok-aux">empty</.test(html), "empty aux:\n" + html);
  assert(/class="tok-aux">BECAUSE</.test(html), "BECAUSE aux:\n" + html);
  assert(/contains-only/.test(html), "contains-only:\n" + html);
}

testKeywords();
testTokens();
testEscape();
testRequestMethod();
testBoolOps();
testDetect();
testNewExpectOps();
console.log("highlight ok");
