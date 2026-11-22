"use strict";

const paymentSpreadsheet = (() => {
  const header = ["Item", "Amount in USD"];

  function cents(value, label) {
    const entered = value.trim();
    if (!entered) return undefined;
    if (!/^(?:(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{0,2})?|\.\d{1,2})$/.test(entered))
      throw Error("Enter " + label.toLowerCase() + " as a nonnegative dollar amount with at most two decimal places.");
    const [whole, decimals = ""] = entered.replaceAll(",", "").split(".");
    const amount = Number((whole || "0") + decimals.padEnd(2, "0"));
    if (!Number.isSafeInteger(amount)) throw Error(label + " exceeds the supported amount.");
    return amount;
  }

  function template(fields) {
    const lines = [header, ...Object.values(fields).flat().map(([, label]) => [label, ""])];
    return "\uFEFF" + lines.map(line => line.map(cell =>
      '"' + cell.replaceAll('"', '""') + '"').join(",")).join("\r\n") + "\r\n";
  }

  function readRows(text) {
    const input = text.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");
    const rows = [];
    let row = [], cell = "", quoted = false, closed = false;
    function endRow() {
      if (row.length || cell || closed) rows.push([...row, cell]);
      row = [];
      cell = "";
      closed = false;
    }
    for (let i = 0; i < input.length; i++) {
      const ch = input[i];
      if (quoted) {
        if (ch !== '"') cell += ch;
        else if (input[i + 1] === '"') { cell += '"'; i++; }
        else { quoted = false; closed = true; }
      } else if (ch === "\n") endRow();
      else if (ch === ",") { row.push(cell); cell = ""; closed = false; }
      else if (ch === '"' && !cell && !closed) quoted = true;
      else if (ch === '"' || closed)
        throw Error("Check the quotation marks in spreadsheet row " + (rows.length + 1) + ".");
      else cell += ch;
    }
    if (quoted) throw Error("Close the quotation marks in the last spreadsheet row.");
    endRow();
    return rows;
  }

  function read(text, fields) {
    const [columns, ...rows] = readRows(text);
    if (!columns || columns.length !== 2 || columns.some((col, i) => col.trim() !== header[i]))
      throw Error("Use the template headers “Item” and “Amount in USD”.");
    const expected = new Map();
    for (const [group, entries] of Object.entries(fields))
      for (const [key, label] of entries) expected.set(label, {group, key});
    const result = {scenario: {}, payouts: {}};
    const seen = new Set();
    for (const [index, row] of rows.entries()) {
      if (row.length !== 2)
        throw Error("Row " + (index + 2) + " needs two columns. Enclose amounts containing commas in quotation marks.");
      const label = row[0].trim();
      if (!expected.has(label)) throw Error("Unrecognized item in row " + (index + 2) + ". Use an item name from this deal’s template.");
      if (seen.has(label)) throw Error("Remove the duplicate row for " + label + ".");
      seen.add(label);
      const amount = cents(row[1], label);
      const {group, key} = expected.get(label);
      if (amount !== undefined) result[group][key] = amount;
    }
    for (const label of expected.keys())
      if (!seen.has(label)) throw Error("Add the missing “" + label + "” row. Leave its amount blank if unknown.");
    return result;
  }
  return {cents, template, read};
})();
