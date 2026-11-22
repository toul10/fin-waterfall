const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {runInNewContext} = require("node:vm");

const assets = path.join(__dirname,"../abs_waterfall_check/web_static");
const scripts = ["spreadsheet.js","allocation.js","app.js"].map(name =>
  fs.readFileSync(path.join(assets,name),"utf8")).join("\n").replace(/\brender\(\);\s*$/, "");
function workspace(js, role = "analyst") {
  return runInNewContext(scripts + '\n' + js,{
    document: {querySelector() {return {};},addEventListener(){}},window: {addEventListener(){}},
    location: {hash:"#allocate"},role,
    ford:{id:"ford",kind:"payment_priority",approved:true,name:"Ford"},
    mastr:{id:"mastr",kind:"prepayment_charges_to_class_p",approved:true,name:"MASTR"},
  });
}
const privateSession = 'session = {mode:"private",role}; dashboard = {deals:[ford,mastr],runs:[]};\n';

test("quick start asks for the source and money, and retains detailed checks", () => {
  const start = workspace(privateSession + "allocationView(null)");
  assert.match(start,/Drop your contract/);
  assert.match(start,/Paste text/);
  assert.match(start,/Detailed payment check/);
  assert.doesNotMatch(start,/name="(?:kind|clause|reviewer|payouts)"/);
  assert.match(workspace(privateSession + "allocationView(mastr)"),/Prepayment charges collected \(USD\)/);
  assert.match(workspace(privateSession + "allocationView(ford)"),/Cash remaining at these payment steps/);
});

test("hosted setup uses the exact-excerpt preview on both paths", () => {
  const markup = workspace(privateSession + `
    session.edition="hosted";
    ford.approved=false;
    ford.clause="Section 8"; ford.source_name="Uploaded contract.pdf";
    ford.source_text="Private cover page. Public terms <A>."; ford.suggested_excerpt="Public terms <A>.";
    [allocationView(ford),dealView(ford)];
  `);
  for (const page of markup) {
    assert.match(page,/name="excerpt"[^>]*>Public terms &lt;A&gt;\.<\/textarea>/);
    assert.match(page,/<form id="excerpt-confirm" hidden>/);
    assert.match(page,/I authorize sending this excerpt to Gemini/);
    assert.doesNotMatch(page,/Prepare local draft|Drafting provider<select/);
  }
});
test("only the needed due appears; a plan supplies known inputs and no submitted payments", () => {
  const setup = privateSession + `
    const entry = allocationEntry(ford);
    entry.amount = "50,000.00";
    entry.date = "2026-09-15";
    entry.result = {state:"needs_input",missing_field:"class_a_principal",reason:"Provide priority principal."};
  `;
  const view = workspace(setup + "allocationView(ford)");
  assert.match(view,/name="class_a_principal"/);
  assert.doesNotMatch(view,/name="class_b_interest"/);
  const worksheet = workspace(setup + `
    entry.dues = {class_a_principal:"55000"};
    entry.result = {scenario:{class_b_interest:100},payouts:{class_a_principal:5000000,class_b_interest:0}};
    allocationWorksheet(ford)
  `);
  assert.deepEqual(JSON.parse(JSON.stringify(worksheet.submitted_inputs)),{
    scenario:{cash:5000000,class_a_principal:5500000,class_b_interest:100},adapter:"observed",payout_purpose:"proposed",
  });
  assert.equal(worksheet.distribution_date,"2026-09-15");
});
test("quick approval belongs to a named reviewer and retains imported evidence", () => {
  const setup = privateSession + `
    ford.approved=false;
    ford.draft={order:["class_a_principal","class_b_interest"],contract_excerpt:"Allocate X."};
    ford.source_text="Payment section. Allocate X.";
    ford.initial_allocation={amount_cents:1001,distribution_date:"2026-03-04"};
    allocationView(ford)
  `;
  const analyst = workspace(setup);
  assert.match(analyst,/Full imported contract text/);
  assert.match(analyst,/value="10.01"/);
  assert.match(analyst,/value="2026-03-04"/);
  assert.doesNotMatch(analyst,/id="allocation-approval"/);
  const reviewer = workspace(setup,"reviewer");
  assert.match(reviewer,/id="allocation-approval"/);
  assert.match(reviewer,/type="checkbox".*?required/);
});
test("public examples use the verifier response and omit upload and amount entry", () => {
  const html = workspace(`
    session={mode:"demo",role:"public"}; dashboard={deals:[ford,mastr]};
    allocationView(mastr,{state:"ready",payouts:{class_p:24625394},remaining_cents:0,distribution_date:"2006-06-26",
      rule:{support:"Pay Class P."},retrospective:"The collections were corrected later.",
      period_evidence:{label:"Amended filing",source_url:"https://www.sec.gov/example"},
      approval_effective_from:"2006-06-26",approved_rule_id:"approved",rule_provenance:{approved_rule_sha256:"digest"}})
  `);
  assert.match(html,/href="#allocate\/ford"/);
  assert.match(html,/Class P.*?\$246,253.94/);
  assert.match(html,/The collections were corrected later/);
  assert.match(html,/Amended filing/);
  assert.doesNotMatch(html,/<form|type="file"|Check submitted payments|>PASS</);
});
