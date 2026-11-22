const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { runInNewContext } = require("node:vm");
const script = readFileSync(join(__dirname, "../abs_waterfall_check/web_static/spreadsheet.js"), "utf8");
const sheet = runInNewContext(script + "\npaymentSpreadsheet;");
const fields = {
  scenario: [["collected_prepayment_charges", "Prepayment charges collected"]],
  payouts: [["class_p", "Class P payment"]],
};
const header = 'Item,Amount in USD\r\n';
const charges = 'Prepayment charges collected';

test("templates use Excel-compatible UTF-8 and preserve unknown amounts", () => {
  const template = sheet.template(fields);
  assert.ok(template.startsWith('\uFEFF"Item","Amount in USD"\r\n'));
  assert.equal(JSON.stringify(sheet.read(template, fields)), '{"scenario":{},"payouts":{}}');
});

test("quoted comma amounts, reordered rows, fractional cents precision and zero", () => {
  const rows = '"Class P payment","0"\r\n"' + charges + '","1,234.05"\r\n';
  const result = sheet.read(header + rows, fields);
  assert.equal(result.scenario.collected_prepayment_charges, 123405);
  assert.equal(result.payouts.class_p, 0);
  assert.equal(sheet.cents(".01", "Amount"), 1);
});

test("rejects unsupported amount formats, malformed quotes and ambiguous rows", () => {
  for (const value of ["-1", "2.005", "1e3", "1,23", "=1+1", "90071992547409.92"])
    assert.throws(() => sheet.cents(value, "Amount"));
  for (const rows of [
    'Class P payment,1\n',
    charges + ',1\nClass P payment,1\nClass P payment,2',
    charges + ',1\nOther,1',
    charges + ',1\nClass P payment,"1',
    charges + ',1\nClass P payment,"1"2',
    charges + ',1\nClass P payment,1,000',
  ]) assert.throws(() => sheet.read(header + rows, fields));
});
