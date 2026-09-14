const pg = require("./playground.js");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

async function testList() {
  const result = await pg.runText(pg.SAMPLES.list.text);
  assert(result.ok, "list users should pass\n" + result.text);
  assert(result.passed === 1, "expected 1 passed, got " + result.passed);
}

async function testCreateSave() {
  const result = await pg.runText(pg.SAMPLES.create.text);
  assert(result.ok, "create+save should pass\n" + result.text);
  assert(/saved userId=/.test(result.text), "should print saved userId");
  assert(/setup Create User/.test(result.text), "Get User should run Create User as setup");
}

async function testFail() {
  const result = await pg.runText(pg.SAMPLES.fail.text);
  assert(!result.ok, "json expect fail should fail the suite");
  assert(result.failed === 1, "expected 1 failed, got " + result.failed);
  assert(result.passed === 1, "second test should still run, passed=" + result.passed);
  assert(/JSON \$\.data\.email expected/.test(result.text), "should show json mismatch");
}

async function testWait() {
  const result = await pg.runText(pg.SAMPLES.wait.text);
  assert(result.ok, "wait demo should pass\n" + result.text);
  const gets = result.text.split("\n").filter((line) => /GET \/jobs\/demo/.test(line));
  assert(gets.length >= 3, "wait should reissue GET until ready, got " + gets.length);
}

async function testUnsupported() {
  try {
    pg.parse("SUITE: X\nTEST: T\n  GET: /api/users\n  FILE: avatar FROM ./x.png\n");
    throw new Error("FILE should not parse");
  } catch (err) {
    assert(/FILE/.test(err.message), "FILE error: " + err.message);
  }
  try {
    pg.parse("SUITE: X\nTEST: T\n  GET: /api/users\n  EXPECT: xpath //a == 1\n");
    throw new Error("xpath should not parse");
  } catch (err) {
    assert(/xpath/.test(err.message.toLowerCase()), "xpath error: " + err.message);
  }
}

async function testExamples() {
  const src = `
SUITE: Examples
URL: mock://
TEST: Create named
EXAMPLES:
name,email
Ada,ada@example.com
Bob,bob@example.com
  POST: /api/users
  BODY: {"name": "\${name}", "email": "\${email}"}
  EXPECT: status == 201
  EXPECT: json $.name == "\${name}"
`;
  const result = await pg.runText(src);
  assert(result.ok, "examples should pass\n" + result.text);
  assert(result.passed === 2, "expected 2 example rows, got " + result.passed);
}

async function testUnknownKeyword() {
  try {
    pg.parse("SUITE: X\nBANANA: yes\n");
    throw new Error("unknown keyword should throw");
  } catch (err) {
    assert(/Unknown keyword/.test(err.message), err.message);
  }
}

async function testSpacedKeyword() {
  try {
    pg.parse("SUITE: X\nSUITE SETUP: Auth\nTEST: Auth\n  GET: /api/users\n");
    throw new Error("space-separated keyword should throw");
  } catch (err) {
    assert(/Keywords cannot contain spaces; use SUITE-SETUP/.test(err.message), err.message);
  }
  try {
    pg.parse("SUITE: X\nFOLLO REDIRECTS: true\nTEST: Auth\n  GET: /api/users\n");
    throw new Error("spaced keyword typo should throw");
  } catch (err) {
    assert(/did you mean FOLLOW-REDIRECTS/.test(err.message), err.message);
  }
}

async function testJsonPath() {
  const data = { items: [{ id: 1, status: "open" }, { id: 2, status: "closed" }] };
  const got = pg.extract(data, '$.items[?(@.status=="open")].id');
  assert(JSON.stringify(got) === JSON.stringify([1]), "filter path, got " + JSON.stringify(got));
}

async function testExpectBool() {
  const parsed = pg.parse(
    "SUITE: X\nTEST: T\n  GET: /api/users\n  EXPECT: (status == 400 OR status == 200) AND body contains data\n"
  );
  const check = parsed.tests[0].steps[0].checks[0];
  assert(check.type === "AND", "grouped root should be AND, got " + check.type);
  assert(check.terms[0].type === "OR", "grouped left should be OR");

  const pass = await pg.runText(
    "SUITE: X\nTEST: T\n  GET: /api/users\n  EXPECT: status == 400 OR status == 200\n  EXPECT: json $.page == 1 AND body contains data\n"
  );
  assert(pass.ok, "AND/OR should pass\n" + pass.text);

  const fail = await pg.runText(
    "SUITE: X\nTEST: T\n  GET: /api/users\n  EXPECT: status == 400 OR status == 401\n"
  );
  assert(!fail.ok, "OR should fail when neither status matches");
  assert(/OR expected at least one check to pass/.test(fail.text), fail.text);
}

async function testDepends() {
  const failed = await pg.runText(`
SUITE: Depends
URL: mock://
TEST: Create User
  GET: /api/users
  EXPECT: status == 201
TEST: Get User
DEPENDS: Create User
  GET: /api/users/1
  EXPECT: status == 200
`);
  assert(!failed.ok, "create should fail");
  assert(failed.failed === 1, "expected 1 failed, got " + failed.failed);
  assert(failed.skipped === 1, "dependent should skip, skipped=" + failed.skipped);
  assert(/depends on 'Create User' which failed/.test(failed.text), failed.text);

  const passed = await pg.runText(`
SUITE: Depends
URL: mock://
TEST: Create User
  GET: /api/users
  EXPECT: status == 200
TEST: Get User
DEPENDS: Create User
  GET: /api/users/1
  EXPECT: status == 200
`);
  assert(passed.ok, "both should pass\n" + passed.text);
  assert(passed.passed === 2, "expected 2 passed, got " + passed.passed);

  const reordered = await pg.runText(`
SUITE: Depends order
URL: mock://
TEST: tc1
  GET: /api/users
  EXPECT: status == 200
TEST: tc2
DEPENDS: tc3
  GET: /api/users
  EXPECT: status == 200
TEST: tc3
  GET: /health
  EXPECT: status == 200
`);
  assert(reordered.ok, "reordered suite should pass\n" + reordered.text);
  const i1 = reordered.text.indexOf("tc1");
  const i3 = reordered.text.indexOf("tc3");
  const i2 = reordered.text.indexOf("tc2");
  assert(i1 >= 0 && i3 > i1 && i2 > i3, "expected tc1, tc3, tc2; got\n" + reordered.text);
}

async function testHelper() {
  const result = await pg.runText(`
SUITE: Helpers
URL: mock://
HELPER: Seed
  POST: /api/users
  BODY: {"name": "Ada", "email": "ada@example.com"}
  EXPECT: status == 201
SUITE-SETUP: Seed
TEST: List
  GET: /api/users
  EXPECT: status == 200
`);
  assert(result.ok, "helper suite should pass\n" + result.text);
  assert(result.passed === 1, "helper must not count as a test, passed=" + result.passed);
  assert(/setup Seed/.test(result.text), "should announce setup Seed");
}

async function testSuiteSetupFailure() {
  const result = await pg.runText(`
SUITE: Setup fail
URL: mock://
HELPER: Seed
  GET: /health
  EXPECT: status == 201
SUITE-SETUP: Seed
TEST: List
  GET: /api/users
  EXPECT: status == 200
TEST: Other
  GET: /health
  EXPECT: status == 200
`);
  assert(!result.ok, "suite should not be ok");
  assert(result.failed === 0, "setup must not count as a failed test, failed=" + result.failed);
  assert(result.passed === 0, "passed=" + result.passed);
  assert(result.skipped === 2, "skipped=" + result.skipped);
  assert(/0 passed/.test(result.text) && /0 failed/.test(result.text) && /2 skipped/.test(result.text), result.text);
  assert(/SUITE-SETUP \(Seed\)/.test(result.text), result.text);
}

async function testSoftCollectAndNewOps() {
  const parsed = pg.parse(`
SUITE: X
TEST: T
  GET: /health
  EXPECT: json $.ok type boolean
  EXPECT: json $.ok == true BECAUSE "health should pass"
  EXPECT: json $.missing empty
`);
  const checks = parsed.tests[0].steps[0].checks;
  assert(checks[0].operator === "TYPE" && checks[0].value === "boolean", "type op");
  assert(checks[1].because === "health should pass", "because on check");
  assert(checks[2].operator === "EMPTY", "empty op");

  const ops = await pg.runText(`
SUITE: Ops
URL: mock://
TEST: Health
  GET: /health
  EXPECT: json $.ok type boolean
  EXPECT: json $.ok in [true]
  EXPECT: json $.ok == true BECAUSE "health should pass"
  EXPECT: body not empty
  EXPECT: header Content-Type starts-with application
TEST: Users
  GET: /api/users
  EXPECT: json $.page between 1 10
  EXPECT: json $.page type number
  EXPECT: json $.data not empty
  EXPECT: json $.data[*].id unique
  EXPECT: json $.data[*].id contains-any [1, 99]
  EXPECT: json $.data[*].id contains-only [1, 2]
`);
  assert(ops.ok, "new operators should pass\n" + ops.text);

  const soft = await pg.runText(`
SUITE: Soft
URL: mock://
TEST: Fail many
  GET: /health
  EXPECT: status == 201
  EXPECT: json $.ok == false
`);
  assert(!soft.ok, "soft suite should fail");
  assert(/Status code expected 201, got 200/.test(soft.text), soft.text);
  assert(/JSON \$\.ok expected false, got true/.test(soft.text), soft.text);

  const becauseFail = await pg.runText(`
SUITE: Because
URL: mock://
TEST: Login
  GET: /health
  EXPECT: json $.ok == false BECAUSE "login should succeed"
`);
  assert(!becauseFail.ok, "because fail should fail");
  assert(/login should succeed:/.test(becauseFail.text), becauseFail.text);

  const more = pg.parse(`
SUITE: X
TEST: T
  GET: /api/users
  EXPECT: json $.email starts-with "ada@"
  EXPECT: json $.email ends-with "@x.com"
  EXPECT: json $.email not matches @tempmail
  EXPECT: json $.score close-to 0.33 delta 0.01
  EXPECT: json $.tags contains-only ["a","b"]
  EXPECT: json $.tags contains-any ["a"]
  EXPECT: header X-Trace empty
  EXPECT: body matches ok
`);
  const moreChecks = more.tests[0].steps[0].checks;
  assert(moreChecks[0].operator === "STARTS-WITH", moreChecks[0].operator);
  assert(moreChecks[1].operator === "ENDS-WITH", moreChecks[1].operator);
  assert(moreChecks[2].operator === "NOT MATCHES", moreChecks[2].operator);
  assert(moreChecks[3].operator === "CLOSE-TO" && moreChecks[3].delta === 0.01, "close-to");
  assert(moreChecks[4].operator === "CONTAINS-ONLY", moreChecks[4].operator);
  assert(moreChecks[5].operator === "CONTAINS-ANY", moreChecks[5].operator);
  assert(moreChecks[6].operator === "EMPTY", moreChecks[6].operator);
  assert(moreChecks[7].operator === "MATCHES", moreChecks[7].operator);
}

async function main() {
  await testList();
  await testCreateSave();
  await testFail();
  await testWait();
  await testUnsupported();
  await testExamples();
  await testUnknownKeyword();
  await testSpacedKeyword();
  await testJsonPath();
  await testExpectBool();
  await testDepends();
  await testHelper();
  await testSuiteSetupFailure();
  await testSoftCollectAndNewOps();
  console.log("playground interpreter ok");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
