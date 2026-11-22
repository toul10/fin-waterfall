"use strict";

function releaseModelName(model) {
  return model ? model.id + " · v" + model.version : "Unknown version";
}

function releaseField(key) {
  return ({
    class_a_principal: "Class A principal", class_b_interest: "Class B interest",
    class_p: "Class P", cash: "Available cash",
    collected_prepayment_charges: "Collected prepayment charges"
  })[key] || pretty(key);
}

function releaseInputs(scenario) {
  return Object.entries(scenario || {}).map(([key, amount]) =>
    esc(releaseField(key)) + " " + esc(money(amount))).join(" · ") || "No collection inputs";
}

function releaseOptions(models, index) {
  return models.map((model, i) => '<option value="' + esc(model.key) + '"' +
    (i === index ? " selected" : "") + '>' + esc(releaseModelName(model)) + '</option>').join("");
}

async function renderReleases(dealId, selectedCaseId) {
  const path = location.hash;
  const catalog = await api("releases");
  if (path !== location.hash) return;
  const deal = dealId ? catalog.deals.find(d => d.id === dealId) :
    catalog.deals.find(d => d.id === "ford") || catalog.deals[0];
  const head = heading("Compare releases", "Check how two payment model versions allocate the same collections.", button("#guide", "User guide"));
  if (!deal || catalog.models.length < 2) {
    main.innerHTML = head + panel("Comparison setup", pad(!deal ? 'Approve a deal rule to compare payouts. ' + button("#deals", "View deals") :
      "Configure at least two payment model versions when starting the workspace."));
    return;
  }
  const detail = await api("releases/" + encodeURIComponent(deal.id));
  if (path !== location.hash) return;
  const options = catalog.deals.map(d => '<option value="' + esc(d.id) + '"' +
    (d.id === deal.id ? " selected" : "") + '>' + esc(d.name) + '</option>').join("");
  const selected = c => selectedCaseId ? c.id === selectedCaseId : c.id !== "missing-input";
  const selectionNote = selectedCaseId && !detail.cases.some(selected)
    ? '<p role="status">This saved payment is unavailable. Choose another case below to continue.</p>' : '';
  const checkbox = c => '<label class="release-case"><input type="checkbox" name="case" value="' +
    esc(c.id) + '"' + (selected(c) ? " checked" : "") +
    '><span><strong>' + esc(c.label) + '</strong><small>' + releaseInputs(c.scenario) + '</small></span></label>';
  const caseGroup = (label, cases) => cases.length ? '<fieldset class="release-group"><legend>' + esc(label) +
    ' (' + cases.length + ')</legend><div class="release-cases">' + cases.map(checkbox).join("") + '</div></fieldset>' : '';
  const missing = detail.cases.find(c => c.id === "missing-input");
  const caseGroups = caseGroup("Saved payments", detail.cases.filter(c => c.source === "saved" && c !== missing)) +
    caseGroup("Synthetic boundary cases", detail.cases.filter(c => c.source === "boundary"));
  main.innerHTML = head + '<form id="release-form">' + panel("Deal and model versions", pad(
    '<label>Approved deal<select id="release-deal">' + options + '</select></label>' +
    '<div class="release-models"><label>Current version<select name="current">' + releaseOptions(catalog.models, 0) +
    '</select></label><label>Candidate version<select name="candidate">' + releaseOptions(catalog.models, 1) +
    '</select></label></div><p>' + esc(detail.clause) + ' · ' + link("deal", deal.id, "View approved deal") + '</p>')) +
    panel("Payment cases", pad(selectionNote + '<div class="release-actions"><button type="button" id="all-payments">Select saved and boundary cases</button><button type="button" id="clear-cases">Clear selection</button></div>' + caseGroups +
    (missing ? '<details class="release-practice"><summary>Try an incomplete case</summary><div class="release-cases">' + checkbox(missing) +
      '</div><button type="button" id="only-incomplete">Select only the missing-input case</button></details>' : '') +
    '<div class="release-actions"><button type="submit" class="button primary">Compare payouts</button></div>' + feedback)) + '</form>';
  $("#release-deal").onchange = e => {location.hash = route("releases", e.target.value);};
  const setSelection = predicate => {
    for (const box of document.querySelectorAll('#release-form input[name="case"]')) box.checked = predicate(box.value);
  };
  $("#clear-cases").onclick = () => setSelection(() => false);
  $("#all-payments").onclick = () => setSelection(id => id !== "missing-input");
  if (missing) $("#only-incomplete").onclick = () => {
    setSelection(id => id === "missing-input");
    $('#release-form button[type="submit"]').focus();
  };
  busy($("#release-form"), async data => {
    const report = await api("releases/" + encodeURIComponent(deal.id) + "/compare", {
      rule_id: detail.rule_id, current_id: data.get("current"), candidate_id: data.get("candidate"), case_ids: data.getAll("case")
    });
    if (path === location.hash) location.hash = route("release-report", report.run_id);
  }, "Checking payouts…");
}

function releaseTranche(key, row, dealId) {
  return dealId === "ford" && row.id === "ford-stress" && key === "class_a_principal"
    ? "Class A-1 principal" : releaseField(key);
}

function releaseTotal(expected, payouts) {
  const keys = Object.keys(expected || {});
  return keys.length && keys.every(key => Number.isSafeInteger(payouts?.[key]))
    ? keys.reduce((total, key) => total + payouts[key], 0) : null;
}

function releaseFinding(row, dealId, role) {
  const payouts = row[role]?.payouts;
  if (!payouts) return "";
  const differences = Object.entries(row.expected || {}).map(([key, due]) => ({
    name: releaseTranche(key, row, dealId), amount: payouts[key] - due
  })).filter(d => Number.isSafeInteger(d.amount) && d.amount !== 0);
  if (!differences.length) return "";
  const model = role === "baseline" ? "Current version" : "Candidate";
  if (differences.length === 2 && differences[0].amount === -differences[1].amount) {
    const under = differences.find(d => d.amount < 0);
    const over = differences.find(d => d.amount > 0);
    return model + " shifts " + money(over.amount) + " from " + under.name + " to " + over.name +
      " compared with the approved allocation. Both distribute " + money(releaseTotal(row.expected, payouts)) + ".";
  }
  return model + " pays " +
    differences.map(d => d.name + " " + money(Math.abs(d.amount)) +
      (d.amount < 0 ? " below" : " above") + " its approved payout").join(" and ") + ".";
}

function worksheetIssue(row, rule) {
  const affected = paymentFields({draft: rule}).scenario
    .filter(([key]) => !Number.isSafeInteger(row.scenario?.[key]) || row.scenario[key] < 0)
    .map(([,label]) => label);
  return affected.length ? "Enter " + affected.join(", ") + " in dollars." : row.input_error;
}

function releaseCaseReport(row, dealId, opened, rule) {
  const expected = row.expected || {};
  const baseline = row.baseline?.payouts || {};
  const candidate = row.candidate?.payouts || {};
  const keys = Object.keys(expected);
  const approvedTotal = releaseTotal(expected, expected);
  const currentTotal = releaseTotal(expected, baseline);
  const proposedTotal = releaseTotal(expected, candidate);
  let totals = "";
  if (approvedTotal !== null && proposedTotal !== null) {
    totals = currentTotal === approvedTotal && proposedTotal === approvedTotal
      ? "Approved, current and candidate totals all equal " + money(approvedTotal) + " across these tranches."
      : "Across these tranches, the approved total is " + money(approvedTotal) +
        ", the current total is " + money(currentTotal) + " and the candidate total is " + money(proposedTotal) + ".";
  }
  const amounts = (a, b, c) => [money(a), money(b), money(c),
    Number.isSafeInteger(c) && Number.isSafeInteger(a) ? money(c - a) : "—",
    Number.isSafeInteger(c) && Number.isSafeInteger(b) ? money(c - b) : "—"];
  const rows = keys.map(k => [esc(releaseTranche(k, row, dealId)), ...amounts(expected[k], baseline[k], candidate[k])]);
  if (rows.length) rows.push(["<strong>Total paid</strong>", ...amounts(approvedTotal, currentTotal, proposedTotal)]);
  const issue = row.input_error ? [worksheetIssue(row, rule)] : [row.reason, row.baseline?.reason && "Current version · " + row.baseline.reason,
    row.candidate?.reason && "Candidate version · " + row.candidate.reason].filter(Boolean);
  const source = row.source === "boundary" ? "Boundary scenario" : row.id === "missing-input" ? "Incomplete example" : "Saved payment case";
  return '<details class="release-result"' + (opened ? ' open' : '') + '><summary><strong>' + esc(row.label) +
    '</strong><small>' + esc(source) + '</small><span>Current ' + badge(row.baseline?.status) +
    ' · Candidate ' + badge(row.candidate?.status) + '</span></summary><p>' + releaseInputs(row.scenario) +
    '</p>' + (totals ? '<p><strong>' + esc(totals) + '</strong></p>' : '') + issue.map(v => "<p>" + esc(v) + "</p>").join("") +
    table(["Tranche", "Approved payout", "Current payout", "Candidate payout", "Candidate less approved", "Change from current"],
      rows, "Payouts unavailable for these inputs.") + '</details>';
}

async function renderReleaseReport(input) {
  const path = location.hash;
  const r = typeof input === "string" ? await api("runs/" + encodeURIComponent(input)) : input;
  const catalog = await api("releases");
  if (path !== location.hash) return;
  const deal = catalog.deals.find(d => d.id === r.deal_id);
  const rule = r.rule || {};
  const ruleRef = rule.clause || r.rule_provenance?.clause || "";
  const head = heading("Release comparison", deal?.name || r.deal_name || "", button(route("releases", r.deal_id), "Compare releases"));
  const incomplete = (r.rows || []).filter(row => row.baseline?.status === "HOLD" || row.candidate?.status === "HOLD");
  const inputCase = incomplete.find(row => row.input_error);
  let decision = r.reason, action = r.action, remedy = "";
  if (inputCase) {
    const saved = /^saved-([a-f0-9]{32})$/.exec(inputCase.id);
    const href = route("deal", r.deal_id) + (saved ? "/check/" + saved[1] : "/check");
    const instruction = worksheetIssue(inputCase, rule);
    if (r.status === "HOLD" && incomplete[0] === inputCase) {
      decision = inputCase.label + " needs a complete worksheet.";
      action = instruction;
    }
    if (r.deal_id) remedy = '<p>' + button(href, saved ? "Correct the saved worksheet" : "Open payment worksheet") +
      (saved ? ' ' : ' Choose “Enter my own amounts”. ') +
      'Save the corrected payment, then choose “Compare releases with these inputs”.</p>';
  }
  const pending = incomplete.length ? '<p><strong>Incomplete cases</strong> · ' + incomplete.map(row => esc(row.label)).join(", ") + '</p>' : "";
  const auditUrl = "/api/runs/" + encodeURIComponent(r.run_id);
  const rows = r.rows || [];
  const candidateFailure = rows.find(row => row.candidate?.status === "BLOCK");
  const currentFailure = rows.find(row => row.baseline?.status === "BLOCK");
  const first = candidateFailure || currentFailure || incomplete[0] || rows[0];
  const ordered = first ? [first, ...rows.filter(row => row !== first)] : [];
  const caseTables = first ? releaseCaseReport(first, r.deal_id, true, rule) : "";
  const otherCases = ordered.slice(1);
  const remainingTables = otherCases.length ? '<details class="release-other"><summary>Other selected cases (' +
    otherCases.length + ')</summary>' + otherCases.map(row => releaseCaseReport(row, r.deal_id, false, rule)).join("") + '</details>' : "";
  const versionFindings = [[candidateFailure, "candidate"], [currentFailure, "baseline"]]
    .filter(([row]) => row).map(([row, role]) => '<p><strong>' + esc(row.label) + '</strong> · ' +
      esc(releaseFinding(row, r.deal_id, role)) + '</p>').join("");
  const receipt = r.rule_provenance;
  const reference = '<p>Comparison ' + esc(r.run_id) + '</p>' + (receipt ?
    '<p>Approved rule ' + esc(receipt.rule_id) + '</p>' +
    (rule.approved_by ? '<p>Approved by · ' + esc(rule.approved_by) + '</p>' : '') +
    (rule.rule_version ? '<p>Rule version · ' + esc(rule.rule_version) + '</p>' : '') +
    (receipt.draft_sha256 ? '<p>Reviewed draft SHA-256 · ' + esc(receipt.draft_sha256) + '</p>' : '') +
    '<p>Approved SHA-256 · ' + esc(receipt.approved_rule_sha256) + '</p>' : '');
  main.innerHTML = '<div class="release-report">' + head + panel("Release decision", pad(badge(r.status) + '<h3>' + esc(decision) +
    '</h3>' + versionFindings + '<p>' + esc(action) + '</p>' + pending + remedy + '<p>Current · ' + esc(releaseModelName(r.baseline)) +
    '<br>Candidate · ' + esc(releaseModelName(r.candidate_model)) +
    '</p><p>' + (ruleRef ? esc(ruleRef) + ' · ' : '') +
    (r.deal_id ? link("deal", r.deal_id, receipt ? "View approved clause" : "Review deal approval") : '') +
    '</p>' + (rule.support ? '<blockquote>' + esc(rule.support) + '</blockquote>' : '') +
    '<details class="release-reference"><summary>Approval reference</summary>' + reference + '</details>' +
    '<div class="release-actions">' + button(auditUrl + "/audit", "Download comparison") +
    '<button class="button" type="button" id="release-print">Print / save PDF</button></div>')) +
    (first ? panel("Payouts by case", caseTables + remainingTables) : "") +
    (r.replay_case ? panel("Recheck the failed case", pad(
      '<p>Use the captured collections and current-version payouts to check a corrected candidate.</p>' +
      '<form id="release-replay"><label>Failed case<select name="index">' +
      r.replay_cases.map((c,i) => '<option value="' + i + '">' + esc(c.row.label) + '</option>').join("") +
      '</select></label><a class="button" id="release-export" href="' + auditUrl + '/cases/0">Download failed case</a>' +
      '<label>Corrected candidate<select name="candidate">' + releaseOptions(catalog.models, Math.min(2,catalog.models.length-1)) +
      '</select></label><button class="button primary" type="submit">Replay against candidate</button>' + feedback + '</form>')) : "") + '</div>';
  const failures = $("#release-replay select[name=index]");
  $("#release-print").onclick = () => window.print();
  if (failures) failures.onchange = () => {$("#release-export").href = auditUrl + "/cases/" + failures.value;};
  if ($("#release-replay")) busy($("#release-replay"), async data => {
    const report = await api("releases/" + encodeURIComponent(r.deal_id) + "/replay", {
      run_id: r.run_id, index: Number(data.get("index")), candidate_id: data.get("candidate")
    });
    if (path === location.hash) location.hash = route("release-report", report.run_id);
  }, "Rechecking payouts…");
}

window.addEventListener("beforeprint", () => {
  for (const detail of document.querySelectorAll(".release-report details:not([open])")) {
    detail.dataset.forPrint = "true";
    detail.open = true;
  }
});
window.addEventListener("afterprint", () => {
  for (const detail of document.querySelectorAll(".release-report details[data-for-print]")) {
    detail.open = false;
    delete detail.dataset.forPrint;
  }
});
