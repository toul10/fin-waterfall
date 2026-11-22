const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {runInNewContext} = require("node:vm");

const assets = path.join(__dirname, "../abs_waterfall_check/web_static");
// guide.js supplies the worksheet hints that the check form renders.
const scripts = ["spreadsheet.js", "guide.js", "allocation.js", "app.js"].map(name =>
  fs.readFileSync(path.join(assets, name), "utf8")).join("\n").replace(/\brender\(\);\s*$/, "");
// A general rule whose ids repeat a bundled key and whose names need escaping.
const rule = {
  schema: 3, type: "priority_of_payments", rounding: "largest_remainder", funds_label: "Available Funds",
  clause: "Section 8.2(c)", support: "(i) first … (iv) fourth …",
  steps: [
    {payees: [{id: "servicing_fee", label: "Servicing Fee"}], limit: "due",
     evidence: "(i) first, to the Servicer, the Servicing Fee;"},
    {payees: [{id: "trustee_fees", label: "Trustee <Fees>"}, {id: "owner_fees", label: "Owner Trustee Fees"}],
     limit: "due", evidence: "(ii) second, pro rata, the Trustee <Fees> and the Owner Trustee Fees;"},
    {payees: [{id: "class_b_interest", label: "Class B Notes Interest"}], limit: "due",
     evidence: "(iii) third, the Class B Notes Interest;"},
    {payees: [{id: "certificateholders", label: "Certificateholders"}], limit: "remainder",
     evidence: "(iv) fourth, all remaining Available Funds to the Certificateholders."},
  ],
  uncited_paragraphs: ["(v) fifth, to the <Swap> Counterparty, any termination payment;"],
};
const setup = (role = "analyst") => `
  session = {mode: "private", role: "${role}", username: "pat"};
  const rule = ${JSON.stringify(rule)};
  const deal = {id: "synthetic", kind: "priority_of_payments", name: "Synthetic Auto Trust", clause: "Section 8.2(c)",
    approved: true, draft: rule, draft_sha256: "abc", source_text: "Source.", cases: [], effective_from: "2026-06-01",
    approval_versions: [{rule_id: "r", effective_from: "2026-06-01"}]};
  dashboard = {deals: [deal], runs: [], draft_providers: [{id: "local", label: "Local Qwen", available: true}],
    adapters: [{id: "observed", label: "Observed"}, {id: "good", label: "Class A first"}, {id: "faulty", label: "Class B first"}]};
`;
function workspace(js) {
  return runInNewContext(scripts + "\n" + js, {
    document: {querySelector() { return {}; }, addEventListener() {}, getElementById() { return {}; }},
    window: {addEventListener() {}}, location: {hash: "#deal/synthetic"},
  });
}
const unapproved = 'deal.approved = false; deal.approval_versions = []; delete deal.effective_from;';
const check = fields => `{run_id: "r1", mode: "check", deal_id: "synthetic", deal_name: "Synthetic Auto Trust",
  payout_purpose: "actual", distribution_date: "2026-06-15", rule, ${fields}}`;

test("the worksheet uses the approved names in step order and round-trips through CSV", () => {
  const fields = JSON.parse(workspace(setup() + "JSON.stringify(paymentFields(deal))"));
  assert.deepEqual(fields.scenario.map(([key, label]) => [key, label]), [
    ["cash", "Available Funds"], ["servicing_fee", "Servicing Fee due"], ["trustee_fees", "Trustee <Fees> due"],
    ["owner_fees", "Owner Trustee Fees due"], ["class_b_interest", "Class B Notes Interest due"]]);
  assert.deepEqual(fields.payouts.map(([key, label]) => [key, label]), [
    ["servicing_fee", "Servicing Fee payment"], ["trustee_fees", "Trustee <Fees> payment"],
    ["owner_fees", "Owner Trustee Fees payment"], ["class_b_interest", "Class B Notes Interest payment"],
    ["certificateholders", "Certificateholders payment"]]);
  const read = workspace(setup() + `
    const f = paymentFields(deal);
    const csv = paymentSpreadsheet.template(f).replace('"Trustee <Fees> due",""', '"Trustee <Fees> due","1,000.50"')
      .replace('"Certificateholders payment",""', '"Certificateholders payment","0"');
    JSON.stringify(paymentSpreadsheet.read(csv, f))`);
  assert.deepEqual(JSON.parse(read), {scenario: {trustee_fees: 100050}, payouts: {certificateholders: 0}});
  // A general deal without its rule falls back to the funds only, never to the two-step fields.
  const bare = JSON.parse(workspace(setup() + 'JSON.stringify(paymentFields({kind: "priority_of_payments"}))'));
  assert.deepEqual(bare.scenario.map(([key]) => key), ["cash"]);
  assert.deepEqual(bare.payouts, []);
});

test("the check form names each payment and offers no two-step stress test", () => {
  const html = workspace(setup("operator") + 'location.hash = "#deal/synthetic/tools"; runForm(deal)');
  assert.match(html, /Servicing Fee → Trustee &lt;Fees&gt; \+ Owner Trustee Fees \(pro rata\) → Class B Notes Interest → Certificateholders/);
  assert.match(html, /name="scenario_trustee_fees"/);
  assert.match(html, /name="payouts_certificateholders"/);
  assert.match(html, /amount due on the Trustee &lt;Fees&gt; row/);
  assert.doesNotMatch(html, /name="scenario_certificateholders"/);
  assert.doesNotMatch(html, /value="search"|value="good"|value="faulty"/);
  assert.doesNotMatch(html, /undefined|Interest – Class B Notes|Accrued interest due to Class B|<Fees>/);
  // The two-step rule keeps its stress test and calculations.
  const ford = workspace(setup("operator") + `location.hash = "#deal/ford/tools";
    runForm({id: "ford", kind: "payment_priority", draft: {order: ["class_a_principal", "class_b_interest"]}, cases: []})`);
  assert.match(ford, /value="search"/);
  assert.match(ford, /value="faulty"/);
});

test("reviewers see every step, its evidence and any uncited paragraph before approving", () => {
  for (const html of [workspace(setup("reviewer") + unapproved + "dealView(deal)"),
                      workspace(setup("reviewer") + unapproved + "allocationView(deal)")]) {
    const warning = html.indexOf("Check before approving");
    assert.ok(warning > 0);
    assert.ok(warning < html.search(/id="(?:approveform|allocation-approval)"/));
    assert.match(html, /&lt;Swap&gt; Counterparty/);
    assert.match(html, /<strong>Trustee &lt;Fees&gt;, Owner Trustee Fees<\/strong><small class="secondary-line">Amounts due, pro rata if the funds are short/);
    assert.match(html, /<strong>Certificateholders<\/strong><small class="secondary-line">All remaining Available Funds/);
    assert.match(html, /\(iii\) third, the Class B Notes Interest;/);
    assert.doesNotMatch(html, /<Swap>|<Fees>|Class B interest/);
  }
  // The first approval comes before the worksheet of amounts due.
  assert.doesNotMatch(workspace(setup("reviewer") + unapproved + "allocationView(deal)"), /name="due_/);
});

test("accepted cent rounding passes without a shortfall headline", () => {
  const html = workspace(setup() + "runView(" + check(`status: "PASS",
    expected: {servicing_fee: 0, trustee_fees: 1, owner_fees: 0, class_b_interest: 0, certificateholders: 0},
    candidate: {servicing_fee: 0, trustee_fees: 0, owner_fees: 1, class_b_interest: 0, certificateholders: 0},
    expected_total_cents: 1, candidate_total_cents: 1, rounding_accepted: ["trustee_fees", "owner_fees"],
    scenario: {cash: 1, servicing_fee: 0, trustee_fees: 1, owner_fees: 1}`) + ")");
  assert.match(html, /The reported payment matches the amount due/);
  assert.match(html, /Pro rata cent rounding accepted for Trustee &lt;Fees&gt;, Owner Trustee Fees\./);
  assert.match(html, /−\$0\.01 · rounding/);
  assert.match(html, /<dt>Available Funds<\/dt><dd>\$0\.01<\/dd>/);
  assert.doesNotMatch(html, / short|shortfall|overpayment|The total balances/);
});

test("a difference is named after the rule even when its id repeats a bundled key", () => {
  const scenario = 'scenario: {cash: 500, servicing_fee: 0, trustee_fees: 0, owner_fees: 0, class_b_interest: 500}';
  const html = workspace(setup() + "runView(" + check(`status: "BLOCK", ${scenario},
    expected: {servicing_fee: 0, trustee_fees: 0, owner_fees: 0, class_b_interest: 500, certificateholders: 0},
    candidate: {servicing_fee: 0, trustee_fees: 0, owner_fees: 0, class_b_interest: 0, certificateholders: 500},
    expected_total_cents: 500, candidate_total_cents: 500`) + ")");
  assert.match(html, /Class B Notes Interest was \$5\.00 short/);
  assert.match(html, /Certificateholders<\/span><strong>\$5\.00 overpayment/);
  assert.doesNotMatch(html, /Class B interest/);
  const finding = workspace(setup() + `paymentFinding({status: "BLOCK",
    expected: {class_b_interest: 500, certificateholders: 0}, candidate: {class_b_interest: 0, certificateholders: 500},
    largest_underpayment: {tranche: "class_b_interest", amount_cents: 500},
    payment_names: {class_b_interest: "Class B Notes Interest", certificateholders: "Certificateholders"}})`);
  assert.match(finding, /\$5\.00 short<\/strong><small class="secondary-line">Class B Notes Interest</);
  assert.equal(workspace(setup() + 'payoutLabel("class_b_interest")'), "Class B interest");
});

test("an incomplete check asks only for the amounts due at the step the funds reach", () => {
  const html = workspace(setup() + "runView(" + check(`status: "HOLD",
    reason: "Supply missing integer-cent fields: trustee_fees, owner_fees",
    submitted_inputs: {scenario: {cash: 100, servicing_fee: 50}, adapter: "observed",
      payouts: {servicing_fee: 50, trustee_fees: 25, owner_fees: 25, class_b_interest: 0, certificateholders: 0}}`) + ")");
  assert.match(html, /Enter Trustee &lt;Fees&gt; due, Owner Trustee Fees due to complete the comparison\./);
  assert.doesNotMatch(html, /Class B Notes Interest due/);
});

test("quick allocation shows every amount due at once under prefixed names", () => {
  const html = workspace(setup() + `
    const entry = allocationEntry(deal);
    entry.amount = "1,000.00"; entry.date = "2026-06-15";
    entry.result = {state: "needs_input", missing_field: "trustee_fees", missing_fields: ["trustee_fees", "owner_fees"],
      reason: "Cash reaches this step. Enter the amount due for Trustee <Fees>, Owner Trustee Fees for this distribution."};
    allocationView(deal)`);
  assert.match(html, /<label>Available Funds \(USD\)<input/);
  for (const id of ["servicing_fee", "trustee_fees", "owner_fees", "class_b_interest"])
    assert.match(html, new RegExp('name="due_' + id + '"'));
  assert.match(html, /Trustee &lt;Fees&gt; due \(USD\)/);
  assert.match(html, /Enter the amount due for Trustee &lt;Fees&gt;, Owner Trustee Fees/);
  assert.doesNotMatch(html, /name="due_certificateholders"|name="(?:servicing_fee|trustee_fees|class_b_interest)"/);
  const sheet = JSON.parse(workspace(setup() + `
    const entry = allocationEntry(deal);
    entry.amount = "1,000.00"; entry.date = "2026-06-15"; entry.dues = {servicing_fee: "500", trustee_fees: "250.00"};
    entry.result = {scenario: {owner_fees: 100}};
    JSON.stringify(allocationWorksheet(deal))`));
  assert.deepEqual(sheet.submitted_inputs.scenario, {owner_fees: 100, cash: 100000, servicing_fee: 50000, trustee_fees: 25000});
  const done = workspace(setup() + `calculatedAllocation(deal, {state: "ready", distribution_date: "2026-06-15",
    amount_cents: 100000, remaining_cents: 0, rule, approval_effective_from: "2026-06-01", approved_rule_id: "r",
    rule_provenance: {approved_rule_sha256: "d"},
    payouts: {servicing_fee: 50000, trustee_fees: 25000, owner_fees: 25000, class_b_interest: 0, certificateholders: 0}})`);
  assert.match(done, /<strong>Available Funds<\/strong> · \$1,000\.00/);
  assert.match(done, /<span>Trustee &lt;Fees&gt;<\/span>.*?\$250\.00/);
  assert.match(done, /<span>Class B Notes Interest<\/span>/);
  assert.doesNotMatch(done, /Class B interest|<Fees>/);
});

test("the general rule is an explicit choice", () => {
  assert.match(workspace(setup() + "createView()"),
    /<option value="priority_of_payments">Priority of payments · every step<\/option>/);
  assert.match(workspace(setup() + "allocationView(null)"), /<input type="checkbox" name="general">/);
  assert.equal(workspace(setup() + "scopeLabel(deal)"), "Priority of payments · every step");
});
