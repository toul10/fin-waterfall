"use strict";

const allocationEntries = new Map();
const amountDueLabels = {
  class_a_principal: "First-priority Class A principal due",
  class_b_interest: "Accrued Class B interest due",
};
// The amounts due a deal's quick allocation can ask for, in payment order.
function allocationDues(d) {
  const rule = waterfallRule(d?.draft);
  if (rule) {
    // Prefixed input names keep payee ids from colliding with this form's own fields.
    return rule.steps.filter(s => s.limit === "due").flatMap(s => s.payees).map(p => ({key: p.id, name: "due_" + p.id,
      label: p.label + " due", hint: "Statement or waterfall worksheet · amount due on the " + p.label + " row for this payment date."}));
  }
  if (!d || d.kind === "priority_of_payments") return [];
  return Object.entries(amountDueLabels).map(([key, label]) => ({key, name: key, label, hint: key === "class_a_principal"
    ? "Trustee waterfall · Calculated Amount on the First Priority Principal Payment row."
    : "Trustee waterfall · Calculated Amount on the Interest – Class B Notes row."}));
}
function allocationDollars(cents) {
  return Number.isSafeInteger(cents) && cents >= 0 ? String(cents).padStart(3, "0").replace(/(\d{2})$/, ".$1") : "";
}
function allocationEntry(deal) {
  if (!allocationEntries.has(deal.id)) {
    const initial = deal.initial_allocation || {};
    allocationEntries.set(deal.id, {amount: allocationDollars(initial.amount_cents), date: initial.distribution_date || "", dues: {}, result: null});
  }
  return allocationEntries.get(deal.id);
}
function allocationSourceOptions(deals, selected) {
  const labels = deals.map(deal => {
    if (["ford", "mastr"].includes(deal.id)) return deal.name + " · bundled example";
    const source = deal.source_name && deal.source_name !== "Pasted text" ? deal.source_name : deal.clause;
    const duplicate = deals.some(other => other.id !== deal.id && other.name === deal.name);
    return duplicate && source ? deal.name + " · " + source : deal.name;
  });
  return '<label>Contract<select name="contract"><option value="">Upload or paste a contract</option>' +
    deals.map((deal, index) => {
      let label = labels[index];
      if (labels.indexOf(label) !== labels.lastIndexOf(label)) {
        label += " · saved " + (deal.created_at || deal.id).replace("T", " ").replace(/\..*$/, "");
      }
      return '<option value="' + esc(deal.id) + '"' + (selected === deal.id ? ' selected' : '') + '>' + esc(label) + '</option>';
    }).join("") + '</select></label>';
}
function allocationSourceFields() {
  return '<div class="source-tabs" role="group" aria-label="Provide a contract"><button type="button" data-source="file" aria-pressed="true">Upload file</button><button type="button" data-source="text" aria-pressed="false">Paste text</button></div>' +
    '<label class="contract-drop" data-upload>Drop your contract here or choose a file<input type="file" name="file" accept=".pdf,.txt" required><small>Searchable PDF or UTF-8 text.</small></label>' +
    '<label hidden data-paste>Contract clause<textarea name="source_text" rows="6"></textarea></label>' +
    '<label class="check"><input type="checkbox" name="general"> Allocate through every step of the priority of payments<small>You will select the complete priority of payments. Amounts due still come from the statement.</small></label>' +
    (usesGemini() ? '<p>We read your source in your organization’s workspace. Before Gemini drafts a rule, you will review and confirm the text to send.</p>' : '') +
    '<div hidden data-excerpt><label>Select the governing excerpt<textarea name="excerpt" rows="7"></textarea></label><small>Keep the exact wording of the payment steps or entitlement. Include the recipient and relevant conditions.</small></div>';
}
function allocationAmountFields(d, entry) {
  const p = d?.kind === "prepayment_charges_to_class_p";
  const general = d?.kind === "priority_of_payments";
  const label = p ? "Prepayment charges collected" : general ? waterfallRule(d.draft)?.funds_label || "Available funds" :
    d ? "Cash remaining at these payment steps" : "Amount to allocate";
  const hint = p ? "Enter only the qualifying mortgage-loan prepayment charges received during this payment date’s related Prepayment Period." :
    general ? "Enter the funds applied at the first step of the priority of payments for this payment date." :
    d ? "Use remaining available funds immediately before Class A first-priority principal and Class B interest." : "Enter the funds covered by the payment clause. You can confirm their scope after the contract is read.";
  return '<div class="allocate-inputs"><label>' + esc(label) + ' (USD)<input type="text" inputmode="decimal" name="amount" placeholder="0.00" value="' + esc(entry.amount) +
    '" required><small>' + hint + '</small></label>' +
    '<label>Distribution date<input type="date" name="payment_date" value="' + esc(entry.date) + '"' + (d ? ' required' : '') +
    '><small>' + (d ? "Payment date used to select the approved terms." : "You can add this after the contract review.") + '</small></label></div>';
}
function approvalForAllocation(d, reason = "") {
  if (!d.draft || d.approval_error) {
    const draftForm = usesGemini() ? geminiDraftForm(d) : '<form id="allocation-draft"><label>Governing excerpt<textarea rows="6" name="excerpt" required>' +
      esc(d.suggested_excerpt || d.source_text || "") + '</textarea></label>' + feedback + '<button type="submit">Prepare local draft</button></form>';
    return panel("Review the contract", pad('<p>' + esc(reason || d.approval_error || "Prepare the governing clause before calculating payments.") + '</p>' +
      (!d.draft && !d.approval_error ? draftForm : "") +
      button(route("deal", d.id), "Open deal and approval history")));
  }
  const draft = d.draft;
  const general = waterfallRule(draft);
  const order = general ? waterfallTerms(general) : draft.recipient ? '<p>Pay all qualifying prepayment charges to <strong>' + esc(payoutLabel(draft.recipient)) + '</strong>.</p>' :
    '<ol>' + draft.order.map(k => '<li>' + esc(payoutLabel(k)) + '</li>').join("") + '</ol>';
  const fullSource = d.source_text && d.source_text !== (draft.contract_excerpt || draft.support)
    ? '<details><summary>Full imported contract text</summary>' + pre(d.source_text) + '</details>' : "";
  const sign = mayApprove() ? '<form id="allocation-approval">' +
    (session.mode === "private" ? '<p>Reviewer · ' + esc(session.username) + '</p>' : input("Reviewer name", "reviewer")) +
    '<label>Terms effective from<input name="effective" type="date" required><small>First payment date covered by this version of the contract.</small></label>' +
    '<label class="check"><input type="checkbox" name="confirm" required> I reviewed the source and approve these payment terms.</label>' +
    feedback + '<button class="primary" type="submit">Approve payment terms</button></form>' :
    '<p>A reviewer can open this saved deal and approve the terms. Your starting amount and payment date stay with the new deal.</p>';
  return panel("Review the payment terms", pad((reason ? '<p>' + esc(reason) + '</p>' : '') +
    '<div class="allocation-review"><div><h3>Source clause</h3><p>' + esc(d.clause) + '</p><blockquote class="source">' +
    esc(draft.contract_excerpt || draft.support) + '</blockquote>' + fullSource + '</div><div><h3>Draft payment terms</h3>' + order + '</div></div>' + sign));
}
function calculatedAllocation(d, result) {
  const general = waterfallRule(result.rule);
  const names = paymentNames(result.rule);
  const rows = Object.entries(result.payouts).map(([key, value]) => [esc(payoutLabel(key, "", names)), money(value)]);
  const evidence = result.period_evidence;
  return '<div id="allocation-result" tabindex="-1">' + panel("Calculated payments", pad('<p>' + esc(d.name) + ' · ' + esc(reviewDate(result.distribution_date)) + '</p>' +
    '<p><strong>' + (d.kind === "prepayment_charges_to_class_p" ? "Qualifying prepayment charges" :
      general ? esc(general.funds_label || "Available funds") : "Cash entering the reviewed steps") + '</strong> · ' + money(result.amount_cents) + '</p>' +
    table(["Recipient", "Amount to pay"], rows) +
    '<p><strong>Cash remaining after these terms</strong> · ' + money(result.remaining_cents) + '</p>' +
    (result.retrospective ? '<p><strong>Retrospective</strong> · ' + esc(result.retrospective) + '</p>' : '') +
    (evidence ? '<p>Statement amounts used · ' + sources([{url:evidence.source_url,label:evidence.label}]) + '</p>' : '') +
    '<p>Governing terms · ' + esc(result.rule?.clause || d.clause) + ' · effective ' + esc(reviewDate(result.approval_effective_from)) + '</p>' +
    '<blockquote class="source">' + esc(result.rule?.support) + '</blockquote>' +
    '<p>To verify a statement or proposal, enter its independently submitted payments.</p>' +
    (isDemo() ? link("deal", d.id, "Open the worked distribution check") : button(route("deal", d.id) + "/from-allocation", "Check submitted payments")) +
    '<details><summary>Approval used</summary><p>Approval · ' + esc(result.approved_rule_id) + '</p><p>Approved rule SHA-256<br><code>' + esc(result.rule_provenance?.approved_rule_sha256) + '</code></p></details>')) + '</div>';
}
function allocationView(d, example = null) {
  if (isDemo()) {
    const choices = dashboard.deals.filter(deal => ["ford","mastr"].includes(deal.id));
    return heading("Allocate a payment", "Explore how the approved contract allocates the funds for one distribution.") +
      '<div class="worksheets">' + choices.map(deal => '<article class="worksheet"><h3>' + esc(shortDeal(deal.id,deal.name)) +
        '</h3><p>' + (deal.id === "ford" ? "June 15, 2026 · cash available at the two priority steps." : "June 26, 2006 · qualifying prepayment charges from the subsequent amendment. Retrospective case.") +
        '</p>' + button(route("allocate", deal.id), "See payment allocation") + '</article>').join("") + '</div>' +
      (example?.state === "ready" && d ? calculatedAllocation(d,example) : '') +
      '<p>The private workspace also accepts your contract and amount.</p>' + button("#compare", "Detailed payment check");
  }
  const entry = d ? allocationEntry(d) : {amount:"",date:"",dues:{}};
  const result = entry.result;
  const firstApproval = d && !d.approved && !d.approval_versions?.length;
  const review = firstApproval || result?.state === "needs_approval";
  const general = d?.kind === "priority_of_payments";
  const asked = new Set([result?.missing_field, ...(result?.missing_fields || [])]);
  // A general priority of payments shows every amount due at once. The two-step rule asks for each when needed.
  const dueFields = allocationDues(d).filter(f => general ? !firstApproval : f.key in entry.dues || asked.has(f.key))
    .map(f => '<label>' + esc(f.label) + ' (USD)<input type="text" name="' + esc(f.name) +
      '" inputmode="decimal" value="' + esc(entry.dues[f.key] || '') + '"><small>' + esc(f.hint) + '</small></label>').join("");
  const dues = general && dueFields ? '<fieldset class="allocation-dues"><legend>Amounts due from the statement</legend>' +
    '<p class="help">Enter each amount due before the funds are applied. Leave a step blank if the funds do not reach it.</p>' +
    '<div class="allocate-inputs">' + dueFields + '</div></fieldset>' : dueFields;
  return heading("Allocate a payment", "Start with the contract and the funds to distribute. We calculate who should receive the money.") +
    '<form class="panel allocation-form" id="allocation-form"><div class="pad">' + allocationSourceOptions(dashboard.deals,d?.id) +
    (!d ? allocationSourceFields() : "") + allocationAmountFields(d,entry) +
    (result?.state === "needs_input" ? '<p class="allocation-prompt" role="status">' + esc(result.reason) + '</p>' : '') + dues + feedback +
    (firstApproval ? "" : '<button type="submit" class="primary">' + (d ? "Calculate payments" : "Read contract") + '</button>') +
    '</div></form>' + (review ? approvalForAllocation(d, result?.reason) : '') +
    (result?.state === "ready" ? calculatedAllocation(d,result) : '') +
    '<div class="actions allocation-switch">' + button(d ? route("deal",d.id) + "/from-allocation" : "#compare", "Detailed payment check") + link("guide",null,"Analyst guide") + '</div>';
}
function allocationWorksheet(d) {
  const entry = allocationEntries.get(d.id);
  if (!entry) return null;
  const source = {};
  const read = (raw, label) => {
    try { return paymentSpreadsheet.cents(raw, label); } catch { return undefined; }
  };
  const cash = read(entry.amount, "Amount");
  if (cash !== undefined) source[d.kind === "prepayment_charges_to_class_p" ? "collected_prepayment_charges" : "cash"] = cash;
  const dues = allocationDues(d);
  for (const [key, raw] of Object.entries(entry.dues)) {
    const cents = read(raw, dues.find(f => f.key === key)?.label || key);
    if (cents !== undefined) source[key] = cents;
  }
  return {deal_id:d.id, distribution_date:entry.date, submitted_inputs:{scenario:{...entry.result?.scenario, ...source}, adapter:"observed", payout_purpose:"proposed"}};
}
function bindAllocation(d) {
  const form = $("#allocation-form");
  if (!form) return;
  const el = form.elements;
  el.contract.onchange = () => { location.hash = route("allocate", el.contract.value); };
  const base = d && "deals/" + encodeURIComponent(d.id);
  const entry = d && allocationEntry(d);
  const dueInputs = allocationDues(d);
  const general = d?.kind === "priority_of_payments";
  let changed = 0;
  if (d) {
    form.oninput = event => {
      if (event.target === el.contract) return;
      changed += 1;
      const due = dueInputs.find(f => f.name === event.target.name);
      if (event.target === el.amount || event.target === el.payment_date) {
        entry.amount = el.amount.value;
        entry.date = el.payment_date.value;
        // A new date needs that date's amounts due. A general priority of payments keeps its worksheet visible.
        if (event.target === el.payment_date && !general) {
          entry.dues = {};
          for (const f of dueInputs) {
            if (el[f.name]) el[f.name].closest("label").remove();
          }
        }
      } else if (due) {
        entry.dues[due.key] = event.target.value;
      }
      entry.result = null;
      document.querySelector("#allocation-result")?.remove();
    };
  } else {
    const fileBox = form.querySelector("[data-upload]"), pasteBox = form.querySelector("[data-paste]");
    const clearExcerpt = () => { el.excerpt.value = ""; form.querySelector("[data-excerpt]").hidden = true; el.excerpt.required = false; };
    for (const btn of form.querySelectorAll("[data-source]")) btn.onclick = () => {
      const fileMode = btn.dataset.source === "file";
      for (const other of form.querySelectorAll("[data-source]")) other.setAttribute("aria-pressed", String(btn === other));
      fileBox.hidden = !fileMode; el.file.required = fileMode;
      pasteBox.hidden = fileMode; el.source_text.required = !fileMode;
      clearExcerpt();
    };
    el.file.onchange = clearExcerpt;
    el.general.onchange = clearExcerpt;
    el.source_text.oninput = clearExcerpt;
    fileBox.ondragover = ev => ev.preventDefault();
    fileBox.ondrop = ev => {
      ev.preventDefault();
      if (ev.dataTransfer.files.length === 1) { el.file.files = ev.dataTransfer.files; clearExcerpt(); }
    };
  }
  form.onsubmit = event => event.preventDefault();
  if (form.querySelector("[type=submit]")) busy(form, async () => {
    if (!d) {
      const body = {};
      if (!form.querySelector("[data-upload]").hidden) {
        const file = el.file.files[0];
        if (!file) throw Error("Choose a searchable PDF or text file.");
        body.file = {name:file.name, content_base64:await file64(file)};
      } else body.source_text = el.source_text.value;
      if (!form.querySelector("[data-excerpt]").hidden) body.excerpt = el.excerpt.value;
      if (el.general.checked) body.kind = "priority_of_payments";
      body.amount_cents = paymentSpreadsheet.cents(el.amount.value,"Amount");
      if (el.payment_date.value) body.distribution_date = el.payment_date.value;
      const intake = await api("intake",body);
      if (!form.isConnected) return;
      if (intake.state === "select_excerpt") {
        form.querySelector("[data-excerpt]").hidden = false;
        el.excerpt.required = true;
        if (!el.excerpt.value) el.excerpt.value = intake.source_text || "";
        form.querySelector(".feedback").textContent = intake.reason;
        el.excerpt.focus();
        return;
      }
      if (!intake.deal?.id) throw Error(intake.reason || "Open the detailed deal setup to review this contract.");
      allocationEntries.set(intake.deal.id, {amount:el.amount.value,date:el.payment_date.value,dues:{},result:null});
      location.hash = route("allocate", intake.deal.id);
      if (intake.state === "draft_needed") notify(intake.reason);
      return;
    }
    const dues = {};
    for (const f of dueInputs) {
      if (el[f.name]?.value.trim()) dues[f.key] = paymentSpreadsheet.cents(el[f.name].value, f.label);
    }
    const body = {distribution_date:el.payment_date.value,amount_cents:paymentSpreadsheet.cents(el.amount.value,"Amount"),dues};
    const submitted = changed;
    const result = await api(base + "/allocation",body);
    if (!form.isConnected || submitted !== changed) return;
    entry.result = result;
    entry.amount = el.amount.value;
    entry.date = el.payment_date.value;
    entry.dues = Object.fromEntries(dueInputs.filter(f => f.key in dues).map(f => [f.key, el[f.name].value]));
    await render();
    if (result.state === "ready") $("#allocation-result")?.focus();
    else if (result.missing_field) {
      const field = {distribution_date:"payment_date",amount_cents:"amount"}[result.missing_field] ||
        dueInputs.find(f => f.key === result.missing_field)?.name || result.missing_field;
      $("#allocation-form")?.elements.namedItem(field)?.focus();
    }
  }, d ? "Calculating…" : "Reading the contract…");
  if (usesGemini() && d) bindGeminiDraft(base);
  const draftForm = $("#allocation-draft");
  if (draftForm) busy(draftForm, async f => {
    await api(base + "/draft",{provider:"local",excerpt:f.get("excerpt")});
    await render();
  }, "Preparing local draft…");
  const approval = $("#allocation-approval");
  if (approval) busy(approval, async f => {
    await api(base + "/approve",{reviewer:session.mode === "private" ? session.username : f.get("reviewer"),
      sha256:d.draft_sha256, effective_from:f.get("effective"), confirmed:f.get("confirm") === "on"});
    entry.result = null;
    await render();
    $("#allocation-form [type=submit]")?.focus();
  }, "Saving approval…");
}
