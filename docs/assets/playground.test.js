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
  console.log("playground interpreter ok");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
