const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { runInNewContext } = require("node:vm");

const root = join(__dirname, "..");
const read = path => readFileSync(join(root, path), "utf8");
const asset = name => read("abs_waterfall_check/web_static/" + name);
const fixture = path => JSON.parse(read("abs_waterfall_check/web_examples_data/" + path));
const document = { addEventListener() {}, querySelector() { return {}; } };
const html = runInNewContext(asset("guide.js") + "\nguideView()", { document, heading: () => "" });
const sheet = runInNewContext(asset("spreadsheet.js") + "\npaymentSpreadsheet");
const fieldsFor = runInNewContext(asset("app.js").replace(/\brender\(\);\s*$/, "paymentFields"), {
  document, window: { addEventListener() {} },
});

test("both new-deal examples quote the bundled clauses verbatim", () => {
  for (const deal of ["ford", "mastr"]) {
    const quote = html.match(new RegExp('<blockquote id="' + deal + '-guide-clause">([^<]+)</blockquote>'));
    assert.equal(quote?.[1], fixture(deal + "/draft.json").support);
    assert.ok(html.includes('href="#new/' + deal + '"'));
  }
});

test("worked examples use the form template and reproduce the fixture amounts in cents", () => {
  const stress = JSON.parse(read("fixtures/ford-synthetic-regression.json")).regression;
  const charges = fixture("mastr/scenario.json");
  const examples = [
    ["ford-stress", "payment_priority", stress.scenario, stress.candidate],
    ["mastr-original", "prepayment_charges_to_class_p", charges, fixture("mastr/original-observed.json")],
    ["mastr-amended", "prepayment_charges_to_class_p", charges, fixture("mastr/amended-observed.json")],
  ];
  for (const [name, kind, scenario, payouts] of examples) {
    const fields = fieldsFor({ kind });
    const template = sheet.template(fields);
    const csv = asset(name + ".csv");
    assert.ok(html.includes('href="/' + name + '.csv"'));
    for (const [, label] of [...fields.scenario, ...fields.payouts]) {
      assert.ok(html.includes("<dt>" + label + "</dt>"));
      assert.ok(template.includes('"' + label + '"'));
    }
    assert.deepEqual(JSON.parse(JSON.stringify(sheet.read(csv, fields))), { scenario, payouts });
  }
});

test("the first screen answers onboarding questions with the verified Class P example", () => {
  const home = runInNewContext(asset("app.js").replace(/\brender\(\);\s*$/, `
    session = {mode: "private", role: "analyst"};
    dashboard = {deals: [], runs: []};
    overview("overview")
  `), { document, window: {addEventListener() {}} });
  for (const question of ["How does it work?", "What do I need?", "What will I get?", "How do I use it?"]) {
    assert.ok(home.includes("<h2>" + question + "</h2>"));
  }
  const expected = fixture("mastr/scenario.json").collected_prepayment_charges;
  const reported = fixture("mastr/original-observed.json").class_p;
  const dollars = cents => (cents / 100).toLocaleString("en-US", {style: "currency", currency: "USD"});
  for (const amount of [expected, reported, expected - reported]) assert.ok(home.includes(dollars(amount)));
  assert.ok(home.includes(fixture("mastr/rule.json").support));
  assert.ok(home.includes('href="#compare"'));
  assert.ok(home.includes('href="#sample"'));
});

test("the published sample PDF stays fixed when a workspace rechecks the MASTR case", () => {
  const sample = runInNewContext(asset("app.js").replace(/\brender\(\);\s*$/, `
    session = {mode: "private", role: "reviewer"};
    dashboard = {deals: [], runs: [{case_id: "mastr-original", filed_payout: true, mode: "check", run_id: "private-reapproval"}]};
    overview("sample")
  `), { document, window: {addEventListener() {}} });
  assert.ok(sample.includes('href="/mastr-sample.pdf"'));
  assert.ok(!sample.includes("/api/runs/"));
  assert.equal(readFileSync(join(root, "abs_waterfall_check/web_static/mastr-sample.pdf")).subarray(0, 5).toString(), "%PDF-");
});

test("approval holds preserve the finding and direct a reviewer to the applicable clause", () => {
  const ctx = { document, window: {addEventListener() {}}, location: {hash: "#deal/mastr/approval/held"} };
  const result = runInNewContext(asset("guide.js") + asset("app.js").replace(/\brender\(\);\s*$/, `
    session = {mode: "private", role: "reviewer", username: "reviewer"};
    const d = {id: "mastr", name: "Trust", kind: "prepayment_charges_to_class_p", approved: true, effective_from: "2020-01-01", draft: {support: "Clause"}};
    dashboard = {deals:[d],runs:[]};
    const held = {run_id: "held", deal_id: "mastr", mode:"check", status:"HOLD", distribution_date:"2019-12-01", reason:"No dated approval covers this distribution."};
    [runView(held), dealView(d, held), needsApprovalReview({...held,reason:"Enter the distribution date to identify the applicable approved terms."}), runView({...held,reason:"Approval receipt differs from the selected deal version"})]
  `), ctx);
  assert.ok(result[0].includes('href="#deal/mastr/approval/held"'));
  assert.ok(result[1].includes('href="#deal/mastr/check/held"'));
  assert.ok(result[1].includes('id="approveform"'));
  assert.ok(result[1].includes("1 Dec 2019"));
  assert.equal(result[2], false);
  assert.ok(result[3].includes("Approval receipt differs from the selected deal version"));
});
