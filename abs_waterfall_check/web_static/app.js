"use strict";
const $ = (s) => document.querySelector(s);
const main = $("#content");
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const json = (v) => JSON.stringify(v, null, 2);
const paths = {
  overview:
    '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  deals: '<path d="M3 7V5h7l2 3h9v12H3z"/>',
  runs: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
  releases: '<path d="M4 7h15m-4-4 4 4-4 4M20 17H5m4-4-4 4 4 4"/>',
  guide: '<path d="M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1zm0 0v14"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  alert: '<path d="m12 3 9 17H3zM12 9v5m0 2v1"/>',
  arrow: '<path d="M4 12h15m-6-6 6 6-6 6"/>',
  down: '<path d="m4 7 6 6 4-4 6 8m0-6v6h-6"/>',
  pending: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/>',
  matched: '<circle cx="12" cy="12" r="8.5"/><path d="m8 12 3 3 5-6"/>',
};
const icon = (name) =>
  '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  paths[name] +
  "</svg>";
let session;
const isDemo = () => session.mode === "demo";
const usesGemini = () => session.edition === "hosted";
const isOperator = () => ["operator", "local"].includes(session.role);
const operatorTools = () => isOperator() && location.hash.split("/")[2] === "tools";
const mayApprove = () => isOperator() || session.role === "reviewer";
const nav = { overview: "Review desk", allocate: "Allocate a payment", deals: "Saved deals", runs: "Review history", guide: "Analyst guide", operator: "Operator area" };
paths.operator = paths.releases;
paths.allocate = paths.arrow;
const route = (page, id) =>
  "#" + page + (id ? "/" + encodeURIComponent(id) : "");
const button = (url, label, primary = false) =>
  '<a class="button ' +
  (primary ? "primary" : "") +
  '" href="' +
  esc(url) +
  '">' +
  label +
  "</a>";
const newDeal = () => isDemo() ? "" : button("#new", icon("plus") + " Create a new deal", true);
const heading = (title, sub, action = "") =>
  '<div class="top"><div><h1>' +
  esc(title) +
  "</h1>" +
  (sub ? "<p>" + esc(sub) + "</p>" : "") +
  "</div>" +
  action +
  "</div>";
const panel = (title, body, action = "") =>
  '<section class="panel"><div class="section-head"><h2>' +
  esc(title) +
  "</h2>" +
  action +
  "</div>" +
  body +
  "</section>";
const pad = (html) => '<div class="pad">' + html + "</div>";
const pre = (v) =>
  "<pre>" + esc(typeof v === "string" ? v : json(v)) + "</pre>";
const badge = (status) => {
  const s = String(status || "Draft");
  const tone = /block|fail|found|violation/i.test(s)
    ? "bad"
    : /hold|draft|pending|review/i.test(s)
      ? "warn"
      : /pass|approved|verified|clear/i.test(s)
        ? "ok"
        : "";
  return (
    '<span class="badge ' +
    tone +
    '">' +
    esc(s.replaceAll("_", " ")) +
    "</span>"
  );
};
const table = (cols, rows, empty) =>
  rows.length
    ? '<div class="table-scroll"><table><thead><tr>' +
      cols.map((c) => '<th scope="col">' + esc(c) + "</th>").join("") +
      "</tr></thead><tbody>" +
      rows
        .map(
          (r) => "<tr>" + r.map((v, index) =>
            '<td data-label="' + esc(cols[index]) + '"><span>' + v + "</span></td>",
          ).join("") + "</tr>",
        )
        .join("") +
      "</tbody></table></div>"
    : '<div class="empty">' + esc(empty) + "</div>";
const link = (page, id, label) =>
  '<a href="' + esc(route(page, id)) + '">' + esc(label) + "</a>";
async function api(path, body) {
  const resp = await fetch(
    "/api/" + path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const data = await resp.json();
  if (!resp.ok) throw Error(data.error || data.detail || resp.statusText);
  return data;
}
function busy(form, work, label) {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = form.querySelector("[type=submit]");
    const err = form.querySelector(".feedback");
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = label;
    err.textContent = "";
    try {
      await work(new FormData(form));
    } catch (ex) {
      err.textContent = ex.message;
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  });
}
const feedback = '<p class="feedback" role="alert"></p>';
const money = (cents) =>
  cents == null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
      }).format(Number(cents) / 100);
const pretty = (text) => String(text || "").replaceAll("_", " ");
const file64 = (file) =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = () => reject(Error("Unable to read the selected file."));
    reader.readAsDataURL(file);
  });
$("#menu").onclick = () =>
  $("#menu").setAttribute(
    "aria-expanded",
    String(document.body.classList.toggle("open")),
  );
let dashboard,
  version = 0;
const reviewTypes = [
  {status: "BLOCK", label: "Payment differences", icon: "down"},
  {status: "HOLD", label: "Incomplete reviews", icon: "pending"},
  {status: "PASS", label: "Payments matched", icon: "matched"},
];
const shortDeal = (id, name) =>
  ({ ford: "Ford 2025-C", mastr: "MASTR 2006-HE1" })[id] || name || "Payment review";
function reviewDate(date) {
  const parsed = new Date(date);
  if (!date || Number.isNaN(parsed.getTime())) return "";
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric", month: "short", year: "numeric",
    ...(/^\d{4}-\d{2}-\d{2}$/.test(date) ? {timeZone: "UTC"} : {}),
  }).format(parsed);
}
function paymentReviewName(r) {
  if (r.mode === "release") return r.origin_run_id ? "Replayed release case" : "Model release comparison";
  if (r.mode === "search") return "Payment priority stress test";
  if (r.mode === "replay") return "Replayed comparison";
  if (r.case_id === "ford-stress") return "$50,000 cash stress";
  if (r.filed_payout && r.case_id === "mastr-original") return "June 2006 · Original distribution";
  if (r.filed_payout && r.case_id === "mastr-amended") return "June 2006 · Amended distribution";
  return r.distribution_date ? "Distribution · " + reviewDate(r.distribution_date) : "Payment comparison";
}
function paymentFinding(r) {
  if (r.mode === "release") return esc({PASS: "Candidate matches the clause", BLOCK: "Candidate payout differs", HOLD: "Release check incomplete"}[r.status] || "Review release");
  if (r.status === "HOLD") return '<span class="finding missing">Review incomplete</span>';
  if (r.status === "PASS") return '<span class="finding balanced">Amounts match</span>';
  if (r.status !== "BLOCK") return "Result unavailable";
  const differences = Object.entries(r.expected || {}).flatMap(([key, value]) => {
    const proposed = r.candidate?.[key];
    if (!Number.isSafeInteger(value) || !Number.isSafeInteger(proposed) || value === proposed) return [];
    return [{ key, diff: proposed - value }];
  });
  const under = r.largest_underpayment;
  const main = under ? { key: under.tranche, diff: -under.amount_cents } :
    differences.sort((a, b) => a.diff < 0 && b.diff > 0 ? -1 : a.diff > 0 && b.diff < 0 ? 1 :
      Math.abs(b.diff) - Math.abs(a.diff))[0];
  if (!main) return '<span class="finding discrepancy">Payment difference</span>';
  return '<strong class="finding discrepancy">' + money(Math.abs(main.diff)) +
    (main.diff < 0 ? " short" : " over") + '</strong><small class="secondary-line">' +
    esc(payoutLabel(main.key, r.affected_tranche || "", r.payment_names)) + '</small>';
}
const runRows = (runs, empty = "No payment reviews have been saved.") =>
  table(["Deal and payment", "Finding", "Result", "Reviewed"], runs.filter(r => isOperator() || r.mode === "check").map(r => [
    '<a class="record-link" href="' + esc(route(r.mode === "release" ? "release-report" : "run", r.run_id)) + '">' +
      esc(shortDeal(r.deal_id, r.deal_name)) + '</a><small class="secondary-line">' +
      esc(paymentReviewName(r)) + (r.synthetic && r.mode === "check" ? " · Synthetic" : "") + '</small>',
    paymentFinding(r),
    badge(r.status),
    '<span class="date-cell">' + esc(reviewDate(r.created_at) || "Unknown") + "</span>",
  ]), empty);
function scopeLabel(d) {
  if (d.kind === "prepayment_charges_to_class_p") return "Prepayment charges to Class P";
  if (d.kind === "payment_priority") return "Class A principal and Class B interest";
  if (d.kind === "priority_of_payments") return "Priority of payments · every step";
  return d.clause || "Payment terms";
}
const dealRows = (deals) =>
  table(["Deal", "Payments in scope", "Contract terms", "Allocation"], deals.map(d => [
    link("deal", d.id, d.name), esc(scopeLabel(d)),
    badge(d.approved ? "Approved" : d.draft_sha256 ? "Awaiting approval" : "Awaiting review"),
    link("allocate", d.id, d.approved ? "Allocate a payment" : "Continue setup"),
  ]), "Add a deal to review its payment terms.");
function worksheetCard(d) {
  const example = d.id === "ford" || d.id === "mastr";
  const context = d.id === "ford" ? "Synthetic stress" : d.id === "mastr" ? "Retrospective" :
    d.approved ? "Approved terms" : "Approval required";
  const intro = d.id === "ford"
    ? "With $50,000 available, how much goes to Class A principal and Class B interest?"
    : d.id === "mastr"
      ? "Compare the original and amended Class P payments with the corrected collections."
      : scopeLabel(d);
  return '<article class="worksheet"><div class="sheet-caption"><span class="sheet-rule"></span>' +
    esc(context) + '</div><h3>' + esc(shortDeal(d.id, d.name)) + '</h3><p>' + esc(intro) + '</p>' +
    '<a href="' + esc(route("deal", d.id)) + '">' +
    (example ? "Open example" : d.approved ? "Compare payments" : "Review payment terms") + ' ' + icon("arrow") + '</a></article>';
}
function deskRoute(status, scope) {
  return "#overview" + (scope !== "all" ? "/" + (status || "all") + "/" + scope : status ? "/" + status : "");
}
function reviewMetrics(checks, filter, scope) {
  return '<div class="review-counts" aria-label="Payment comparisons by result">' + reviewTypes.map(t => {
    const count = checks.filter(r => r.status === t.status).length;
    const active = filter === t.status;
    return '<a href="' + deskRoute(active ? "" : t.status, scope) + '" class="review-count count-' +
      t.status.toLowerCase() + (active ? ' selected' : '') + '" ' + (active ? 'aria-current="true"' : '') +
      '><div><span class="count-symbol">' + icon(t.icon) + '</span><span>' + esc(t.label) +
      '</span></div><strong>' + (checks.length ? count : '—') + '</strong></a>';
  }).join('') + '</div>';
}
function sampleEvidence() {
  return panel("Sample report · MASTR 2006-HE1", pad(
    '<p>June 26, 2006 distribution · Original reported payment checked against collections disclosed in the subsequent amendment.</p>' +
    table(["Recipient", "Expected", "Reported", "Difference"], [["Class P", "$246,253.94", "$190,392.82", "−$55,861.12"]]) +
    '<p><strong>Why it differs</strong> The original Class P payment was $55,861.12 below the corrected prepayment charges collected. The trust paid the difference on July 13.</p>' +
    '<blockquote class="source">On each Distribution Date, the trust administrator will withdraw from the distribution account all amounts representing prepayment charges, if any, in respect of the Mortgage Loans received during the related Prepayment Period and will distribute these amounts to the holders of the Class P Certificates.</blockquote>' +
    '<p>February 22, 2006 prospectus supplement · Overcollateralization Provisions. <a href="https://www.sec.gov/Archives/edgar/data/1352928/000088237706000631/d439989_424b5.txt">Read the governing clause</a>.</p>'));
}
function overview(page, filter, selectedScope) {
  const { deals, runs } = dashboard;
  const start = button("#compare", "Check a distribution", true);
  if (page === "sample") {
    return heading("Sample distribution report", "Retrospective check using the original and amended trustee filings.") +
      sampleEvidence() + button("/mastr-sample.pdf", "Download report", true) +
      button("#guide", "Trace the source amounts") +
      panel("Distribution evidence", pad('<p>The original report supplied the Class P total distribution. The amendment supplied the corrected collections and recorded the additional payment. A reviewer on June 26 would have needed the contemporaneous collection worksheet.</p>' +
        sources([{label:"Original trustee distribution", url:"https://www.sec.gov/Archives/edgar/data/1352928/000105640406002258/mab06he1_10d-200606.txt"}, {label:"Amendment and corrective payment", url:"https://www.sec.gov/Archives/edgar/data/1352928/000105640406002437/mab06he1_10da-200606.txt"}])));
  }
  if (page === "deals")
    return heading("Deal portfolio", "Open a deal to review its contract terms or compare payments.", newDeal()) +
      panel("Saved deals", dealRows(deals));
  if (page === "compare")
    return heading(isDemo() ? "Choose an example" : "Choose a saved deal or create a new deal", isDemo() ? "Explore the bundled distributions and their completed checks." : "Reuse an approved clause for the next payment date, or add the governing clause for a new deal.", newDeal()) +
      '<div class="worksheets">' + deals.map(worksheetCard).join("") + '</div>';
  if (page === "runs")
    return heading("Review history", "Open a past distribution to inspect its inputs, clause and payment finding.", start) +
      panel("All reviews", runRows(runs));
  // A repeated check with the same evidence is one comparison on the desk.
  const seen = new Set();
  const checks = runs.filter(r => {
    if (r.mode !== "check") return false;
    const key = r.review_scope || r.run_id;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const active = reviewTypes.find(t => t.status === filter);
  const scope = ["own", "example"].includes(selectedScope) ? selectedScope : "all";
  const scopedChecks = checks.filter(r => {
    const example = r.case_id === "ford-stress" || ["mastr-original", "mastr-amended"].includes(r.case_id);
    return scope === "all" || (scope === "example" ? example : !example);
  });
  const filtered = active ? scopedChecks.filter(r => r.status === active.status) : scopedChecks;
  const scopeLinks = checks.length ? '<nav class="desk-views" aria-label="Payment comparisons to show">' +
    [["all", "All comparisons"], ["own", "My worksheets"], ["example", "Bundled examples"]].map(([key, label]) =>
      '<a href="' + deskRoute(active?.status, key) + '"' + (scope === key ? ' aria-current="true"' : '') + '>' + label + '</a>'
    ).join("") + '</nav>' : "";
  const examples = deals.filter(d => ["ford", "mastr"].includes(d.id)).sort((a, b) => a.id.localeCompare(b.id));
  const unapproved = deals.filter(d => !d.approved);
  const worksheets = examples.length ? '<section class="desk-examples"><h2>Worked examples</h2><div class="worksheets">' +
    examples.map(worksheetCard).join("") + '</div></section>' : "";
  const reviewPanel = '<section class="panel reviews-panel"><div class="section-head"><div><h2>' +
    esc(active?.label || "Payment reviews") + '</h2><p>Repeated completed comparisons appear once.</p></div>' +
    (active ? '<a href="' + deskRoute("", scope) + '">Clear filter</a>' : link("runs", null, "Full history →")) + '</div>' +
    (filtered.length ? runRows(filtered.slice(0, 8)) : '<div class="desk-empty"><span>' + icon("runs") +
      '</span><div><strong>' + (active ? "No reviews with this result" : "No payments reviewed yet") +
      '</strong><p>' + (active ? 'Choose another result or view all payment reviews.' :
        'Choose a deal or open a worked example to compare your first payment.') + '</p></div></div>') + '</section>';
  return heading("Allocate a payment", isDemo() ? "Explore the Ford and MASTR examples to see how the approved terms allocate each payment." : "Choose your contract and enter the amount to see who should be paid.") +
    '<div class="actions home-actions">' + button("#allocate", "Allocate a payment", true) + button("#compare", "Detailed payment check") + link("sample", null, "See a sample report") + '</div>' +
    '<div class="review-questions"><div><h2>How does it work?</h2><p>' + (isDemo() ? 'Choose a sample deal and open a distribution to see who should receive the available funds.' : 'Upload the contract or choose a saved deal. Enter the funds covered by the clause and the payment date. We apply the approved terms and ask for a period figure only when it can change the allocation.') + '</p></div>' +
    '<div><h2>What do I need?</h2><p>A reviewer approves the governing terms once. For each distribution use the amount for that payment step. The detailed check also accepts the payments on your statement or proposal.</p></div>' +
    '<div><h2>What will I get?</h2><p>The quick allocation shows each recipient’s amount, any cash left over and the governing clause. The detailed check compares those amounts to your submitted payments and gives you a report.</p></div>' +
    '<div><h2>How do I use it?</h2><p>Request a correction before releasing proposed payments. Investigate differences in actual payments with the trustee. Attach the PDF to the distribution record.</p></div></div>'  +
    sampleEvidence() +
    scopeLinks + reviewMetrics(scopedChecks, active?.status, scope) + (checks.length ? reviewPanel + worksheets : worksheets + reviewPanel) +
    (unapproved.length ? panel("Contract terms awaiting approval", dealRows(unapproved)) : "") +
    panel("Deal portfolio", dealRows(deals.slice(0, 5)), link("deals", null, "All deals →"));
}
const input = (label, name, req = true) =>
  "<label>" +
  label +
  '<input name="' +
  name +
  '" ' +
  (req ? "required" : "") +
  "></label>";
const opt = (v, t) => '<option value="' + esc(v) + '">' + esc(t) + "</option>";
function createView() {
  return (
    heading("New deal", "Start with the contract clause that determines who gets paid.") +
    '<section class="guide-prep"><h2>What you’ll need</h2><p>Have the governing clause, its section reference and the deal’s legal name ready. For the comparison, gather the same distribution’s collection or waterfall worksheet and the reported or proposed payments.</p>' +
    (usesGemini() ? '<p>Saving a contract keeps its text in your organization’s workspace. You will select and confirm the exact excerpt before Gemini receives it.</p>' : '') +
    '<a href="#guide">Open the analyst guide and filled CSV examples</a></section>' +
    panel(
      "Deal and governing clause",
      pad(
        '<form id="newform"><div class="pair">' +
          input("Deal name", "name") +
          input("Clause reference", "clause") +
          '</div><label>Payment rule<select name="kind">' +
          opt("payment_priority", "Payment priority") +
          opt(
            "prepayment_charges_to_class_p",
            "Prepayment charges to Class P",
          ) +
          opt("priority_of_payments", "Priority of payments · every step") +
          '</select><small>Priority of payments covers every numbered step of the clause. Amounts due still come from the distribution statement.</small></label><label>Source format<select name="sourceMode">' +
          opt("paste", "Paste clause text") +
          opt("file", "Upload PDF or text file") +
          '</select></label><label id="pastefield">Clause text<textarea name="source_text" rows="7" placeholder="Paste the complete operative paragraph, including the recipient or adjacent numbered priority steps." required></textarea></label><label id="filefield" hidden>Source document<input name="file" type="file" accept=".pdf,.txt"><small>Use a searchable PDF or UTF-8 text file. After upload, select the governing excerpt on the deal page.</small></label>' +
          feedback +
          '<div class="actions"><button class="primary" type="submit">Save contract and review excerpt</button>' +
          button("#deals", "Cancel") +
          "</div></form>",
      ),
    )
  );
}
function bindNew(example) {
  const form = $("#newform");
  if (example) {
    form.elements.name.value = example.name;
    form.clause.value = example.clause;
    form.kind.value = example.kind;
    form.source_text.value = example.source_text;
  }
  form.sourceMode.onchange = () => {
    const upload = form.sourceMode.value === "file";
    $("#pastefield").hidden = upload;
    $("#filefield").hidden = !upload;
    form.source_text.required = !upload;
    form.file.required = upload;
  };
  busy(
    form,
    async (f) => {
      const body = {
        name: f.get("name"),
        clause: f.get("clause"),
        kind: f.get("kind"),
      };
      if (f.get("sourceMode") === "file") {
        const file = f.get("file");
        body.file = { name: file.name, content_base64: await file64(file) };
      } else body.source_text = f.get("source_text");
      const d = await api("deals", body);
      location.hash = route("deal", d.id);
    },
    "Saving the contract…",
  );
}
const sources = (list) =>
  (list || [])
    .filter((x) => /^https?:\/\//i.test(x.url))
    .map(
      (x) =>
        '<a target="_blank" rel="noopener noreferrer" href="' +
        esc(x.url) +
        '">' +
        esc(x.label || "Source filing") +
        (x.section ? " · " + esc(x.section) : "") +
        " ↗</a>",
    )
    .join("<br>");
function notify(msg) {
  $("#notice").textContent = msg;
  setTimeout(() => {
    if ($("#notice").textContent === msg) $("#notice").textContent = "";
  }, 12000);
}
function needsApprovalReview(run) {
  if (run?.status !== "HOLD" || run.mode !== "check") return false;
  const reason = run.reason || "";
  if (/^Enter (?:a valid |the )?distribution date/i.test(reason)) return false;
  return /approv|receipt|draft digest|reviewed draft|approved-drafts/i.test(reason);
}
function geminiDraftForm(deal) {
  const excerpt = deal.suggested_excerpt || deal.draft?.contract_excerpt ||
    (deal.source_name === "Pasted text" && deal.source_text?.length <= 32000 ? deal.source_text : "");
  return '<form id="excerpt-preview"><label>Contract excerpt to review<textarea name="excerpt" rows="6" maxlength="32000" required>' +
    esc(excerpt) + '</textarea></label><p>Choose consecutive paragraphs from the saved source. Keep the payment steps, recipients and conditions together.</p>' +
    '<p>Gemini on Google Cloud can draft a rule from this excerpt. You will see exactly what it will receive before you send it.</p>' +
    feedback + '<button type="submit" class="primary">Review excerpt for Gemini</button></form>' +
    '<form id="excerpt-confirm" hidden><h3 tabindex="-1">Confirm the excerpt for Gemini</h3>' +
    '<p>This is the exact contract text that will be sent to Gemini on Google Cloud. Your uploaded file and distribution worksheet stay in this workspace.</p>' +
    '<blockquote class="source" data-confirmed-excerpt></blockquote>' +
    '<label class="check"><input name="confirmed" type="checkbox" required> I authorize sending this excerpt to Gemini to draft the payment terms.</label>' +
    feedback + '<div class="actions"><button type="submit" class="primary">Send to Gemini</button>' +
    '<button type="button" data-edit-excerpt>Edit excerpt</button></div></form>';
}
function bindGeminiDraft(base) {
  const editForm = $("#excerpt-preview");
  if (!editForm) return;
  const confirmForm = $("#excerpt-confirm");
  const editor = editForm.elements.excerpt;
  const editButton = confirmForm.querySelector("[data-edit-excerpt]");
  let preview = null;
  function resetPreview() {
    preview = null;
    confirmForm.elements.confirmed.checked = false;
    confirmForm.querySelector("[data-confirmed-excerpt]").textContent = "";
    confirmForm.hidden = true;
    editForm.hidden = false;
  }
  editor.addEventListener("input", resetPreview);
  editButton.onclick = () => { resetPreview(); editor.focus(); };
  busy(editForm, async () => {
    const excerpt = editor.value;
    const result = await api(base + "/draft-preview", {excerpt});
    if (!editForm.isConnected || editor.value !== excerpt) return;
    if (result.provider !== "gemini" || !result.consent_token || result.excerpt !== excerpt)
      throw Error("The excerpt could not be confirmed. Review the text and try again.");
    preview = result;
    confirmForm.querySelector("[data-confirmed-excerpt]").textContent = result.excerpt;
    confirmForm.querySelector(".feedback").textContent = "";
    editForm.hidden = true;
    confirmForm.hidden = false;
    confirmForm.querySelector("h3").focus();
  }, "Preparing preview…");
  busy(confirmForm, async () => {
    if (!preview || !confirmForm.elements.confirmed.checked || editor.value !== preview.excerpt)
      throw Error("Review the excerpt and confirm sending it to Gemini.");
    const {excerpt, consent_token} = preview;
    editButton.disabled = true;
    try {
      const result = await api(base + "/draft", {provider:"gemini", excerpt, consent_token, confirmed:true});
      if (!confirmForm.isConnected) return;
      if (result.state === "draft_retry") {
        resetPreview();
        editForm.querySelector(".feedback").textContent = result.reason || "Gemini is unavailable. Review the saved excerpt again to retry.";
        editor.focus();
        return;
      }
      await render();
    } catch (error) {
      if (!confirmForm.isConnected) return;
      resetPreview();
      editForm.querySelector(".feedback").textContent = error.message + " Review the saved excerpt again to retry.";
      editor.focus();
    } finally {
      editButton.disabled = false;
    }
  }, "Drafting with Gemini…");
}
function dealView(d, held = null) {
  const excerpt = d.draft?.contract_excerpt || d.source_text || "";
  const general = waterfallRule(d.draft);
  const review = d.draft
    ? '<div class="stack">' +
      (general ? waterfallTerms(general) : "") +
      (d.draft.order
        ? "<ol>" +
          d.draft.order.map((t) => "<li>" + esc(payoutLabel(t)) + "</li>").join("") +
          "</ol>"
        : "") +
      (d.draft.recipient
        ? "<p><strong>Recipient</strong> · " +
          esc(payoutLabel(d.draft.recipient)) +
          "</p>"
        : "") +
      (d.draft.support && !general ? "<p>" + esc(d.draft.support) + "</p>" : "") +
      (operatorTools() ? "<details><summary>Technical rule record</summary>" + pre(d.draft) + "</details>" : "") + "</div>"
    : '<p class="muted">Extract a draft from the source clause for review.</p>';
  const approvals = d.approval_versions || [];
  const approvalHistory = approvals.length ? panel("Approved clause versions", table(["Effective from", "Version", "Reviewer"],
    approvals.map(a => [esc(reviewDate(a.effective_from) || "Date required"), esc(a.rule_version || a.rule_id), esc(a.reviewer || d.draft?.approved_by || "See approval record")]))) : "";
  const returnToCheck = held ? route("deal", d.id) + "/check/" + encodeURIComponent(held.run_id) : "";
  const showEffectiveForm = held && mayApprove() && /no dated approval|no effective date/i.test(held.reason || "");
  const decision = d.approved && !showEffectiveForm && (d.effective_from || !mayApprove())
    ? '<div class="stack">' +
      badge("Approved") + "<p>Effective from " + esc(reviewDate(d.effective_from) || "date awaiting reviewer confirmation") + "</p>" +
      (operatorTools() && d.rule_provenance
        ? "<details><summary>Approval record</summary>" +
          pre(d.rule_provenance) +
          "</details>"
        : "") +
      "</div>"
     : d.draft && !mayApprove() ? "<p>A reviewer must confirm the extracted rule against the source clause before it can be used for payment checks.</p>" : d.draft
      ? '<form id="approveform">' +
        (session.mode === "private" ? '<label>Reviewer<input name="reviewer" readonly value="' + esc(session.username) + '"></label>' : input("Reviewer name", "reviewer")) +
        '<label>Effective from<input type="date" name="effective_from" required><small>First payment date covered by this version, inclusive.</small></label>' +
        "<details><summary>Version fingerprint</summary><code>" +
        esc(d.draft_sha256) +
        "</code></details>" +
        '<label class="check"><input name="confirmed" type="checkbox" required> I reviewed the source clause and approve this rule for verification.</label>' +
        feedback +
        '<button type="submit" class="primary">Approve rule</button></form>'
      : "";
  const providers = dashboard.draft_providers || [{id: "local", label: "Local Qwen", available: true}];
  const available = providers.some(p => p.available);
  const draftForm = usesGemini() ? geminiDraftForm(d) : !isDemo()
    ? '<form id="draftform"><label' + (operatorTools() ? '' : ' hidden') + '>Drafting provider<select name="provider">' +
      providers.map(p => '<option value="' + esc(p.id) + '"' + (p.available ? '' : ' disabled') + '>' +
        esc(p.label + (p.available ? '' : ' (not configured)')) + '</option>').join('') +
      '</select></label><label>Contract excerpt to review<textarea rows="5" maxlength="32000" name="excerpt" required>' +
      esc(excerpt.length <= 32000 ? excerpt : "") +
      '</textarea></label><p class="help">' + (isGeneral(d) ? "Select the complete priority of payments, from its opening sentence to the last numbered step." :
        "Use the exact paragraphs that identify the payment order or recipient.") + '</p>' +
      '<label class="check" id="hosted-consent" hidden><input name="send_excerpt" type="checkbox"><span></span></label>' +
      (!available ? '<p>Clause extraction is unavailable. Ask your workspace operator to enable local drafting, then return to this saved contract.</p>' : '') +
      feedback +
      '<button type="submit" class="primary"' + (available ? '' : ' disabled') + '>' +
      (d.draft ? "Regenerate draft" : "Prepare payment rule") +
      "</button></form>"
    : "";
  return (
    heading(
      d.name,
      d.clause,
      badge(
        d.approved
          ? "Approved"
          : d.draft || d.draft_sha256
            ? "In review"
            : "Draft",
      ),
    ) +
    (d.description
      ? '<p class="description">' + esc(d.description) + "</p>"
      : "") +
    (!isDemo() && d.initial_allocation && !d.approved ? button(route("allocate",d.id), "Continue allocation", true) : "") +
    ((d.approved || approvals.length) && !isDemo() ? '<div class="deal-paths">' + button(route("allocate",d.id), "Allocate a payment") + button(route("deal", d.id) + "/next", "Check next payment date") + link("guide", null, "Worksheet guide") + '</div>' + runForm(d) : "") +
    (draftForm && !d.draft ? panel("Prepare rule from contract", pad(draftForm)) : "") +
    '<section id="applicable-approval" tabindex="-1" aria-label="Review applicable approval">' +
    (held ? panel("Review applicable approval", pad('<p><strong>Held distribution</strong> · ' +
      esc(reviewDate(held.distribution_date || held.submitted_inputs?.distribution_date) || "Undated distribution") + '</p><p>' + esc(held.reason) + '</p>' +
      '<p>Compare the source clause and its effective date with the distribution. A reviewer can approve terms after checking the signed contract.</p>' +
      (d.approval_error ? '<p>Ask the workspace operator to restore the verified approval records before attempting another approval.</p>' : '') +
      button(returnToCheck, "Return to saved distribution") + button(route("run", held.run_id), "Open original HOLD"))) : "") +
    '<div class="pair">' +
    panel(
      "Source clause",
      pad(
        '<div class="stack"><small>' +
          esc(d.source_name || "Pasted clause") +
          '</small><div class="source">' +
          esc(excerpt) +
          "</div>" +
          (excerpt !== d.source_text
            ? "<details><summary>Full imported source text</summary>" + pre(d.source_text) + "</details>"
            : "") +
          sources(
            d.filing_sources ||
              (d.source_url
                ? [{ label: d.source_name, url: d.source_url }]
                : []),
          ) +
          "</div>",
      ),
    ) +
    panel("Payment entitlement", pad(review)) +
    "</div>" +
    (decision || d.approval_error
      ? panel(
          "Rule approval",
          pad(
            (d.approval_error
              ? '<p class="error">' + esc(d.approval_error) + "</p>"
              : "") + decision,
          ),
        )
      : "") +
    approvalHistory +
    "</section>" +
    (draftForm && d.draft ? '<details class="panel"><summary>' + (d.approved ? 'Prepare a new clause version' : 'Revise the draft') + '</summary>' + pad(draftForm) + '</details>' : "") +
    panel(
      "Payment review history",
      runRows(dashboard.runs.filter((r) => r.deal_id === d.id)),
    )
  );
}
function bindDeal(d, previousReview = null, selectedCase = "", reviewingApproval = false) {
  const base = "deals/" + encodeURIComponent(d.id);
  if (usesGemini()) bindGeminiDraft(base);
  if ($("#draftform")) {
    const form = $("#draftform");
    const consent = form.elements.send_excerpt;
    const syncConsent = (providerChanged = false) => {
      const provider = form.provider.selectedOptions[0];
      const hosted = provider && provider.value !== "local" && !provider.disabled;
      $("#hosted-consent").hidden = !hosted;
      consent.required = Boolean(hosted);
      consent.checked = false;
      if (hosted) {
        $("#hosted-consent span").textContent = "I authorize sending this exact excerpt to " + provider.textContent + " for this draft.";
        if (providerChanged && d.source_name !== "Pasted text") form.excerpt.value = "";
      }
    };
    form.provider.onchange = () => syncConsent(true);
    form.excerpt.oninput = () => syncConsent();
    syncConsent(true);
    busy(
      form,
      async (f) => {
        const excerpt = f.get("excerpt");
        const body = {provider: f.get("provider"), excerpt};
        if (body.provider !== "local") {
          if (!f.has("send_excerpt")) throw Error("Confirm the provider and excerpt before sending.");
          const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(excerpt));
          body.consent_sha256 = Array.from(new Uint8Array(bytes), n => n.toString(16).padStart(2, "0")).join("");
        }
        await api(base + "/draft", body);
        await render();
      },
      "Preparing the payment rule…",
    );
  }
  if ($("#approveform"))
    busy(
      $("#approveform"),
      async (f) => {
        await api(base + "/approve", {
          reviewer: f.get("reviewer"),
          effective_from: f.get("effective_from"),
          sha256: d.draft_sha256,
          confirmed: f.get("confirmed") === "on",
        });
        if (reviewingApproval && previousReview) {
          location.hash = route("deal", d.id) + "/check/" + encodeURIComponent(previousReview.run_id);
        } else await render();
      },
      "Saving approval…",
    );
  if ($("#runform")) bindRunForm(d, base, previousReview, selectedCase);
}
const area = (label, name) =>
  "<label>" +
  label +
  '<textarea class="mono" name="' +
  name +
  '" rows="5"></textarea></label><label class="jsonfile">Import ' +
  esc(name) +
  ' JSON<input data-target="' +
  esc(name) +
  '" type="file" accept=".json,application/json"></label>';
function paymentFields(d) {
  const rule = waterfallRule(d.rule || d.draft);
  if (rule) {
    // Entries are [key, label, hint, source location]. Labels match the verifier's worksheet names exactly.
    const funds = rule.funds_label || "Available funds";
    const payees = rule.steps.flatMap(s => s.payees);
    const dues = rule.steps.filter(s => s.limit === "due").flatMap(s => s.payees);
    return {
      scenario: [["cash", funds, "Funds applied at the first step of the approved priority of payments.",
        "Statement or waterfall worksheet, " + funds + " for this payment date before any step is paid."],
        ...dues.map(p => [p.id, p.label + " due", "Amount due at this step before the funds are applied. Leave blank if the funds do not reach it.",
          "Statement or waterfall worksheet, amount due on the " + p.label + " row."])],
      payouts: payees.map(p => [p.id, p.label + " payment", "Reported or proposed payment at this step.",
        "Statement or waterfall worksheet, amount paid on the " + p.label + " row, or the matching proposed model output."]),
    };
  }
  if (d.kind === "priority_of_payments")
    return {scenario: [["cash", "Available funds", "Funds applied at the first step of the approved priority of payments.",
      "Statement or waterfall worksheet, available funds for this payment date."]], payouts: []};
  if (d.kind === "prepayment_charges_to_class_p" || d.draft?.schema === 2)
    return {
      scenario: [["collected_prepayment_charges", "Prepayment charges collected"]],
      payouts: [["class_p", "Class P payment"]],
    };
  return {
    scenario: [["cash", "Cash available for distribution"], ["class_a_principal", "Class A principal due"], ["class_b_interest", "Class B interest due"]],
    payouts: [["class_a_principal", "Class A principal payment"], ["class_b_interest", "Class B interest payment"]],
  };
}
function dollarFields(group, fields) {
  return '<div class="amount-grid">' + fields.map(([key, label, hint]) =>
    '<label>' + esc(label) + '<span class="amount-control"><span aria-hidden="true">$</span><input inputmode="decimal" autocomplete="off" aria-label="' + esc(label + " in dollars") + '" name="' + group + '_' + key + '"></span><small class="amount-hint">' + esc(hint ?? guidePaymentHints[group][key]) + '</small></label>'
  ).join("") + '</div>';
}
function checklist(fields) {
  const locations = {
    cash: "Trustee waterfall, Remaining Available Funds immediately above the first reviewed step. To reconstruct, subtract all earlier fees and payments from available funds.",
    class_a_principal: "First Priority Principal Payment row, Calculated Amount. Use the applicable principal formula and its opening note balances and adjusted pool balance if calculating upstream.",
    class_b_interest: "Interest – Class B Notes row, Calculated Amount. Use the accrued interest due from the interest worksheet.",
    collected_prepayment_charges: "Certificateholder Account Statement, Deposits → Prepayment Penalties. Sum eligible charges received in the related Prepayment Period.",
    class_p: "Certificateholder Distribution Summary, Class P row, Total Distribution. For a proposal use the corresponding Class P total from the proposed waterfall."
  };
  return panel("What you need for this distribution", pad('<p>Use one payment date and source version. Enter total USD to two decimals. Multiply amounts stated in thousands by 1,000. Leave unknown values blank and enter 0.00 for a confirmed zero.</p>' +
    table(["Worksheet field", "Where to find it"], [
      ["Distribution date", "Statement header or proposed distribution schedule."],
      ["Payment source", "Use actual for trustee-reported amounts or proposed for the model output before payment release."],
      ...fields.scenario.map(([key,label,,where]) => [esc(label), where ? esc(where) : locations[key]]),
      ...fields.payouts.map(([key,label,,where]) => [esc(label), where ? esc(where) : key === "class_p" ? locations.class_p : "Trustee waterfall, Amount Paid on the " + (key === "class_a_principal" ? "First Priority Principal Payment" : "Interest – Class B Notes") + " row, or the matching proposed model output."])
    ]) + '<p>Keep the governing clause, distribution statement and supporting worksheet with the completed check.</p>'));
}
function runForm(d) {
  const fields = paymentFields(d);
  const steps = waterfallRule(d.draft)?.steps;
  // Stress search and the bundled good and faulty calculations cover the two-step priority rule only.
  const priority = !isGeneral(d) && !fields.payouts.some(([key]) => key === "class_p");
  const adapters = (dashboard.adapters || []).filter(a => a.id !== "observed" && (priority || !["good", "faulty"].includes(a.id)))
    .map(a => opt(a.id, a.label)).join("");
  const entitlement = steps
    ? steps.map(s => s.payees.map(p => p.label).join(" + ") + (s.payees.length > 1 ? " (pro rata)" : "")).join(" → ")
    : d.draft?.order?.length
    ? d.draft.order.map(key => payoutLabel(key)).join(" → ")
    : d.draft?.recipient ? "Collected prepayment charges go to " + payoutLabel(d.draft.recipient) : "";
  return checklist(fields) + panel("Review a distribution", pad(
    '<form id="runform">' +
    (entitlement ? '<p class="payment-rule"><strong>Contract entitlement</strong> ' + esc(entitlement) + '</p>' : '') +
    '<label id="casefield">Distribution<select name="case_id">' +
    (d.cases || []).map(c => opt(c.id, c.label)).join("") + opt("", "Enter my own amounts") +
    '</select></label><div class="help" id="caseinfo"></div>' +
    '<label>Payment source<select name="payout_purpose">' + opt("proposed", "Proposed payments · model output") + opt("actual", "Actual payments · trustee statement") + '</select></label>' +
    '<label id="paymentdate" hidden>Distribution date<input type="date" name="distribution_date"></label>' +
    '<div id="scenfield"><h3>Distribution amounts</h3>' + dollarFields("scenario", fields.scenario) + '</div>' +
    '<div id="payfield"><h3>Reported or proposed payments</h3>' + dollarFields("payouts", fields.payouts) + '</div>' +
    '<div id="replayfield" hidden>' + area("Saved finding", "replay") +
    '<label>Payments to replay<select name="replaydriver">' + opt("", "Saved payment amounts") +
    adapters + '</select></label></div>' + feedback +
    '<button class="primary" type="submit">Compare payments</button>' +
    '<details id="spreadsheet"><summary>Import from a spreadsheet</summary><p>Download the template, enter amounts in dollars, then save it as a CSV file. Leave unknown amounts blank.</p>' +
    '<button type="button" id="sheettemplate">Download CSV template</button>' +
    '<label>Import completed CSV<input type="file" name="payment_csv" accept=".csv,text/csv"></label><p id="sheetstatus" role="status"></p></details>' +
    '<details id="reviewoptions"><summary>Review options</summary><label>Review task<select name="mode">' +
    opt("check", "Compare payments with the contract") +
    (priority ? opt("search", "Stress test payment priority") : "") + opt("replay", "Replay a saved finding") +
    '</select></label><label id="driverfield">Payments to compare<select name="driver">' +
    opt("payouts", "Reported or proposed payments") + adapters + '</select></label></details>' +
    '<details id="inputformats"><summary>Other file formats</summary><label>Input format<select name="entry_format">' +
    opt("dollars", "Enter dollar amounts") + opt("json", "Import or edit JSON (integer cents)") +
    '</select></label><div id="rawscenario">' + area("Distribution amounts in cents", "scenario") +
    '</div><div id="rawpayouts">' + area("Reported or proposed payments in cents", "payouts") + '</div></details></form>'
  ));
}
function bindRunForm(d, base, previousReview, selectedCase) {
  const form = $("#runform");
  const fields = paymentFields(d);
  function refresh() {
    const replay = form.mode.value === "replay", search = form.mode.value === "search";
    const check = !replay && !search;
    const raw = form.entry_format.value === "json";
    $("#casefield").hidden = !check;
    $("#paymentdate").hidden = !check || !!form.case_id.value;
    form.distribution_date.required = check && !form.case_id.value;
    form.payout_purpose.disabled = !check || !!form.case_id.value;
    $("#caseinfo").hidden = !check || !$("#caseinfo").textContent;
    $("#replayfield").hidden = !replay;
    $("#scenfield").hidden = !check || raw;
    $("#driverfield").hidden = replay;
    const p = form.driver.querySelector('option[value="payouts"]');
    p.disabled = search;
    if (search && form.driver.value === "payouts")
      form.driver.value = d.id === "ford" ? "faulty" : form.driver.options[1]?.value || "";
    const entered = form.driver.value === "payouts";
    $("#payfield").hidden = !check || !entered || raw;
    $("#payfield h3").textContent = form.payout_purpose.value === "actual" ? "Actual payments from the trustee statement" : "Proposed payments from the model";
    $("#spreadsheet").hidden = !check;
    $("#inputformats").hidden = !check || !operatorTools();
    $("#reviewoptions").hidden = !operatorTools();
    $("#rawscenario").hidden = !raw;
    $("#rawpayouts").hidden = !raw || !entered;
    form.querySelector("[type=submit]").textContent = replay ? "Replay finding" : search ? "Run stress test" : "Compare payments";
  }
  function showDollars(group, data) {
    for (const [key] of fields[group]) {
      const cents = data?.[key];
      form.elements[group + "_" + key].value = Number.isSafeInteger(cents) && cents >= 0
        ? String(cents).padStart(3, "0").replace(/(\d{2})$/, ".$1") : "";
    }
  }
  function amounts(group) {
    const obj = {};
    for (const [key, label] of fields[group]) {
      const cents = paymentSpreadsheet.cents(form.elements[group + "_" + key].value, label);
      if (cents !== undefined) obj[key] = cents;
    }
    return obj;
  }
  function selectCase() {
    const c = (d.cases || []).find(c => c.id === form.case_id.value);
    const enteredStress = c?.id === "ford-stress" && !operatorTools();
    const caseAmounts = enteredStress ? {...c, payouts: {class_a_principal: 0, class_b_interest: 5000000}} : c;
    form.payout_purpose.value = c?.id.startsWith("mastr-") ? "actual" : "proposed";
    form.distribution_date.value = c?.id.startsWith("mastr-") ? "2006-06-26" : "";
    $("#sheetstatus").textContent = "";
    for (const group of Object.keys(fields)) {
      form.elements[group].value = caseAmounts?.[group] ? json(caseAmounts[group]) : "";
      showDollars(group, caseAmounts?.[group]);
    }
    form.driver.value = enteredStress ? "payouts" : c?.adapter || "payouts";
    const description = d.id === "ford" && c?.id === "ford-stress"
      ? "Synthetic $50,000 distribution. Proposed payments are calculated with Class B interest first."
      : c?.description;
    const references = sources(c?.filing_sources);
    $("#caseinfo").innerHTML = c
      ? esc(description) +
        (d.retrospective ? '<p>' + esc(d.retrospective) + '</p>' : '') +
        (references ? '<details><summary>Filing references</summary>' + references + '</details>' : '')
      : "Enter the distribution amounts and payments from your statement in dollars.";
    refresh();
  }
  function custom() {
    form.case_id.value = "";
    form.payout_purpose.disabled = false;
    form.distribution_date.required = form.mode.value === "check";
    $("#paymentdate").hidden = form.mode.value !== "check";
    $("#caseinfo").textContent = form.driver.value === "payouts"
      ? "Enter the distribution amounts and payments from your statement in dollars."
      : "Proposed payments are calculated using " +
        (form.driver.value === "faulty" ? "Class B interest first" :
          form.driver.value === "good" ? "Class A principal first" : form.driver.selectedOptions[0]?.textContent || "the selected calculation") + ".";
    $("#caseinfo").hidden = form.mode.value !== "check";
  }
  for (const group of Object.keys(fields)) {
    for (const [key] of fields[group])
      form.elements[group + "_" + key].oninput = () => {
        custom();
        try { form.elements[group].value = json(amounts(group)); }
        catch { form.elements[group].value = ""; }
      };
  }
  $("#sheettemplate").onclick = () => {
    const url = URL.createObjectURL(new Blob([paymentSpreadsheet.template(fields)], {type: "text/csv;charset=utf-8"}));
    const link = document.createElement("a");
    link.href = url;
    link.download = d.id + "-payment-template.csv";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  form.payment_csv.onchange = async () => {
    const file = form.payment_csv.files[0];
    if (!file) return;
    const status = $("#sheetstatus");
    try {
      const data = paymentSpreadsheet.read(await file.text(), fields);
      for (const group of Object.keys(fields)) {
        form.elements[group].value = json(data[group]);
        showDollars(group, data[group]);
      }
      form.mode.value = "check";
      form.driver.value = "payouts";
      form.entry_format.value = "dollars";
      custom();
      refresh();
      form.querySelector(".feedback").textContent = "";
      status.textContent = "Imported " + file.name + ". Review the amounts, then compare payments.";
    } catch (error) {
      status.textContent = "Spreadsheet not imported. " + error.message;
    } finally {
      form.payment_csv.value = "";
    }
  };
  form.entry_format.onchange = () => {
    if (form.entry_format.value === "dollars") {
      for (const group of Object.keys(fields)) {
        let data;
        try { data = JSON.parse(form.elements[group].value); } catch {}
        showDollars(group, data);
      }
    }
    refresh();
  };
  form.querySelectorAll("[data-target]").forEach((input) =>
    input.addEventListener("change", async () => {
      const file = input.files[0];
      if (!file) return;
      try {
        const text = await file.text();
        JSON.parse(text);
        form.elements.namedItem(input.dataset.target).value = text;
        if (input.dataset.target !== "replay") custom();
        form.querySelector(".feedback").textContent = "";
      } catch {
        form.querySelector(".feedback").textContent =
          "Could not read valid JSON from " + file.name;
      }
    }),
  );
  form.scenario.oninput = custom;
  form.payouts.oninput = custom;
  form.case_id.onchange = selectCase;
  form.payout_purpose.onchange = refresh;
  let checkDriver = form.driver.value;
  form.mode.onchange = () => {
    if (form.mode.value === "check") form.driver.value = checkDriver || "payouts";
    else if (!form.driver.querySelector('option[value="payouts"]').disabled) checkDriver = form.driver.value;
    refresh();
  };
  form.driver.onchange = () => { custom(); refresh(); };
  // A return from a finding restores its inputs, including partially completed statements.
  const prior = previousReview?.deal_id === d.id ? previousReview : null;
  const savedCase = (d.cases || []).find(c => c.id === (prior?.case_id || selectedCase));
  if (selectedCase === "next") form.case_id.value = "";
  if (prior || savedCase) form.case_id.value = savedCase?.id || "";
  selectCase();
  if (prior && !savedCase) {
    const submitted = prior.submitted_inputs || {};
    const priorAmounts = {scenario: submitted.scenario || prior.scenario, payouts: submitted.payouts || prior.submitted_payouts || prior.candidate};
    for (const [group, values] of Object.entries(priorAmounts)) {
      showDollars(group, values);
      form.elements[group].value = values ? json(values) : "";
    }
    const adapter = submitted.adapter || prior.adapter;
    const sameCalculation = Array.from(form.driver.options).some(o => o.value === adapter);
    form.driver.value = sameCalculation ? adapter : "payouts";
    form.payout_purpose.value = submitted.payout_purpose || prior.payout_purpose || (prior.filed_payout ? "actual" : "proposed");
    form.distribution_date.value = prior.distribution_date || submitted.distribution_date || "";
    custom();
    refresh();
  } else if (prior?.adapter && savedCase && prior.adapter !== (savedCase.adapter || "observed")) {
    form.driver.value = prior.adapter === "observed" ? "payouts" : prior.adapter;
    custom();
    refresh();
  }
  busy(
    form,
    async (f) => {
      const mode = f.get("mode");
      const body = { mode };
      if (mode === "check") body.payout_purpose = form.payout_purpose.value;
      function parse(name) {
        if (!String(f.get(name) || "").trim()) return undefined;
        try {
          return JSON.parse(f.get(name));
        } catch {
          throw Error("Enter valid " + name + " JSON.");
        }
      }
      if (mode === "replay") {
        body.replay = parse("replay");
        if (f.get("replaydriver")) body.adapter = f.get("replaydriver");
      } else {
        if (mode === "check" && f.get("case_id"))
          body.case_id = f.get("case_id");
        if (mode === "check" && !body.case_id && f.get("distribution_date"))
          body.distribution_date = f.get("distribution_date");
        if (mode === "check") body.scenario = f.get("entry_format") === "json" ? parse("scenario") : amounts("scenario");
        if (f.get("driver") === "payouts") {
          body.adapter = "observed";
          body.payouts = f.get("entry_format") === "json" ? parse("payouts") : amounts("payouts");
        } else body.adapter = f.get("driver");
      }
      const r = await api(base + "/runs", body);
      location.hash = route("run", r.run_id);
    },
    "Running verification…",
  );
}
function waterfallRule(rule) {
  return rule?.schema === 3 && Array.isArray(rule.steps) ? rule : null;
}
function isGeneral(d) {
  return d?.kind === "priority_of_payments" || !!waterfallRule(d?.rule || d?.draft);
}
function paymentNames(rule) {
  const general = waterfallRule(rule);
  return general ? Object.fromEntries(general.steps.flatMap(s => s.payees.map(p => [p.id, p.label]))) : null;
}
// Shared by the deal page and quick allocation. Unused paragraphs appear before the approval control.
function waterfallTerms(rule) {
  const funds = rule.funds_label || "Available funds";
  const uncited = Array.isArray(rule.uncited_paragraphs) ? rule.uncited_paragraphs : [];
  return (uncited.length ? '<div class="uncited" role="note"><p><strong>Check before approving.</strong> ' +
      (uncited.length === 1 ? "This paragraph of the selected excerpt reads like a payment step, but no drafted step cites it."
        : "These paragraphs of the selected excerpt read like payment steps, but no drafted step cites them.") +
      " Draft again if a step is missing.</p>" + uncited.map(p => '<blockquote class="source">' + esc(p) + "</blockquote>").join("") + "</div>" : "") +
    "<p>Payments from <strong>" + esc(funds) + '</strong>, in order of priority.</p><ol class="waterfall-steps">' +
    rule.steps.map(s => "<li><strong>" + s.payees.map(p => esc(p.label)).join(", ") + '</strong><small class="secondary-line">' +
      (s.limit === "remainder" ? "All remaining " + esc(funds) : s.payees.length > 1 ? "Amounts due, pro rata if the funds are short" : "Amount due") +
      '</small><blockquote class="source">' + esc(s.evidence) + "</blockquote></li>").join("") + "</ol>";
}
function payoutLabel(key, affected = "", names = null) {
  // A general rule names its own payees. Its ids can repeat bundled keys such as class_b_interest.
  if (names) return Object.hasOwn(names, key) ? names[key] : pretty(key);
  if (key === "class_a_principal") return affected.includes("Class A-1") ? "Class A-1 principal" : "Class A principal";
  if (key === "class_b_interest") return "Class B interest";
  if (key === "class_p") return "Class P";
  return pretty(key);
}
function runView(r) {
  const id = encodeURIComponent(r.run_id);
  const known = Number.isSafeInteger;
  const amount = value => known(value) ? money(value) : "Unknown";
  const difference = (expected, proposed) =>
    known(expected) && known(proposed) ? proposed - expected : null;
  const keys = Object.keys({...r.expected, ...r.candidate, ...r.delta_dollars});
  const names = paymentNames(r.rule);
  // Accepted pro rata cent rounding is shown but is not a payment difference.
  const rounded = new Set(Array.isArray(r.rounding_accepted) ? r.rounding_accepted : []);
  const lines = keys.map(key => ({
    key,
    name: payoutLabel(key, r.affected_tranche || "", names),
    expected: r.expected?.[key],
    proposed: r.candidate?.[key],
    delta: difference(r.expected?.[key], r.candidate?.[key]),
  }));
  const exceptions = lines.filter(line => known(line.delta) && line.delta !== 0 && !rounded.has(line.key));
  const variance = delta => known(delta) && delta !== 0
    ? (delta < 0 ? "−" : "+") + amount(Math.abs(delta))
    : amount(delta);
  const total = difference(r.expected_total_cents, r.candidate_total_cents);
  const rows = lines.map(line => [
    esc(line.name), amount(line.expected), amount(line.proposed),
    variance(line.delta) + (rounded.has(line.key) ? " · rounding" : ""),
  ]);
  if (rows.length) rows.push(["<strong>Total reviewed</strong>", amount(r.expected_total_cents),
    amount(r.candidate_total_cents), variance(total)]);
  const shortfalls = exceptions.filter(line => line.delta < 0);
  const lead = (shortfalls.length ? shortfalls : exceptions).reduce((largest, line) =>
    !largest || Math.abs(line.delta) > Math.abs(largest.delta) ? line : largest, null);
  const findings = exceptions.filter(line => line !== lead).map(({name, delta}) =>
    '<li><span>' + esc(name) + '</span><strong>' + amount(Math.abs(delta)) +
    (delta < 0 ? ' shortfall' : ' overpayment') + '</strong></li>').join("");
  const filed = r.filed_payout === true;
  const actual = filed || r.payout_purpose === "actual";
  const title = r.status === "HOLD" ? "Payment review needs more information" :
    lead ? lead.name + (actual ? " was " : " is ") + amount(Math.abs(lead.delta)) +
      (lead.delta < 0 ? " short" : " over the amount due") :
    r.status === "PASS" ? (actual ? "The reported payment matches the amount due" :
      "The proposed payment matches the amount due") : "Payment review";
  const scope = filed ? "Retrospective · June 26, 2006 · " + (r.mode === "replay" ? "Recheck of filed payments" : r.label || "Reported payment") :
    r.synthetic ? "Modeled stress scenario" : [actual ? "Reported payment" : "Proposed payment", reviewDate(r.distribution_date)].filter(Boolean).join(" · ");
  const reason = String(r.reason || "");
  const kind = r.rule?.type || dashboard?.deals.find(d => d.id === r.deal_id)?.kind;
  const fields = paymentFields({ kind, rule: r.rule });
  const general = isGeneral({ kind, rule: r.rule });
  // A general rule needs amounts due only at the steps the funds reach. The verifier names them.
  const prefix = "Supply missing integer-cent fields: ";
  const named = new Set(general && reason.startsWith(prefix) ? reason.slice(prefix.length).split(", ") : []);
  const missing = [];
  if (r.status === "HOLD" && r.mode === "check" && /cent|field|payout|scenario|input/i.test(reason)) {
    for (const [group, entries] of Object.entries(fields)) {
      if (group === "payouts" && r.adapter && r.adapter !== "observed") continue;
      const supplied = r.submitted_inputs?.[group] || (group === "scenario" ? r.scenario : r.submitted_payouts);
      for (const [key, label] of entries) {
        if (general && group === "scenario" && !named.has(key)) continue;
        if (!known(supplied?.[key])) missing.push(general ? label : label.toLowerCase());
      }
    }
  }
  const approvalHold = needsApprovalReview(r);
  const issue = approvalHold || /distribution date/i.test(reason) ? reason :
    /approv|digest|receipt|reviewed draft|reviewed rule/i.test(reason) ? "Review and approve the governing clause before comparing payments." :
    /replay|regression/i.test(reason) ? "Select a saved review for this deal and try again." :
    /no divergence/i.test(reason) ? "The scenarios tested did not reveal a payment difference." :
    /Synthetic search supports/i.test(reason) ? "Use a payment comparison for this clause. Stress testing is available for the Class A and Class B priority rule." :
    /adapter/i.test(reason) ? "Choose an available payment calculation, or enter the proposed payments." :
    missing.length ? "Enter " + missing.join(", ") + " to complete the comparison." :
    /cent|field|payout|scenario|input/i.test(reason) ? "Check the cash amounts and enter a payment for each tranche in the clause." :
    "Check the distribution inputs and the selected payment source.";
  const next = r.status === "HOLD" ? issue : r.status === "BLOCK"
    ? filed ? "Compare the original distribution and the corrective payment in the amended filing."
      : actual ? "Investigate the difference with the trustee or servicer. Check the amended statement and retain both reports." : "Request a correction before payment release, then check the revised proposal."
    : "Attach the report to the distribution record for this payment date.";
  const checkLink = (!isDemo() && approvalHold && r.deal_id ? button(route("deal", r.deal_id) + "/approval/" + id, "Review applicable approval", true) : "") +
    (!isDemo() && r.deal_id && r.mode === "check" ? button(route("deal", r.deal_id) + "/check/" + id, "Correct and recheck") : "") +
    (operatorTools() && r.mode === "check" && r.deal_id && r.scenario && !general ? button(route("releases", r.deal_id) + "/" +
      encodeURIComponent(r.case_id || "saved-" + r.run_id), "Compare releases with these inputs") : "") +
    (!isDemo() && r.deal_id ? button(route("deal", r.deal_id) + "/next", "Check next payment date") : "") +
    (!isDemo() && filed && r.case_id === "mastr-original" ? button("#deal/mastr/case/mastr-amended", "Compare the amended payment") : "");
  const inputSummary = fields.scenario.filter(([key]) => known(r.scenario?.[key])).map(([key, label]) =>
    '<div><dt>' + esc(label) + '</dt><dd>' + amount(r.scenario[key]) + '</dd></div>').join("");
  const filingLinks = sources(r.filing_sources) ||
    (r.rule_provenance?.source_url ? sources([{url:r.rule_provenance.source_url, label:"Source filing"}]) : "");
  return '<div class="result-view">' +
    '<div class="result-heading">' + link("runs", null, "← Review history") + badge(r.status) + '</div>' +
    heading(title, [r.deal_name, scope].filter(Boolean).join(" · "), button("/api/runs/" + id + "/report", "Download report", true)) +
    panel("Payout reconciliation", table(["Tranche", "Payable under clause",
      actual ? "Reported payment" : "Proposed payment", "Difference"],
      rows, "Complete the worksheet to calculate the payable amounts.") +
      (rows.length ? '<p class="result-note">Difference is the payment minus the amount payable under the clause, in USD.</p>' : '') +
      (rounded.size ? '<p class="result-note">Pro rata cent rounding accepted for ' + esc([...rounded].map(key => payoutLabel(key, "", names)).join(", ")) +
        '. Each amount is the exact pro rata share rounded down or up, and the step total matches the approved allocation.</p>' : '') +
      pad((findings ? '<ul class="result-findings">' + findings + '</ul>' : '') +
        (exceptions.length && total === 0 ? '<p>The total balances. The difference is in who receives the cash.</p>' : '') +
        '<p class="result-next"><strong>Next step</strong> ' + esc(next) + '</p><div class="result-check">' + checkLink + '</div>')) +
    panel("What the comparison used", pad('<div class="stack">' +
      (inputSummary ? '<dl class="result-inputs">' + inputSummary + '</dl>' : '') +
      (r.synthetic ? '<p><strong>Synthetic scenario.</strong> The payouts reflect a modeled stress case.</p>' : '') +
      (r.retrospective ? '<p><strong>Timing of the evidence.</strong> ' + esc(r.retrospective) + '</p>' : '') +
      (r.approval_date_basis ? '<p>Applicable rule · ' + esc(r.rule?.rule_version || r.approved_rule_id || "") +
        (r.approval_effective_from ? ' · effective from ' + esc(reviewDate(r.approval_effective_from)) : '') + '</p><p>' + esc(r.approval_date_basis) + '</p>' : '') +
      '<p>Reviewed clause · ' + esc(r.rule?.clause || "Unknown") + '</p>' +
      (r.rule?.support ? '<blockquote class="source">' + esc(r.rule.support) + '</blockquote>' : '') +
      (filingLinks ? '<div class="result-filings"><strong>Filing references</strong><div>' + filingLinks + '</div></div>' : '') +
      '</div>')) +
    (r.search ? panel("Stress scenarios", table(["Synthetic case", "Available cash", "Result"], [
      ["First observed divergence", r.search.first_observed_divergence], ["Stored regression", r.search.regression],
      ["One-cent shortfall", r.search.one_cent_shortfall], ["Fully funded", r.search.fully_funded],
    ].filter(([,v]) => v).map(([label,v]) => [label, amount(v.scenario?.cash), badge(v.status || "Unknown")]),
    "No scenarios recorded.")) : '') +
    (operatorTools() ? '<details class="panel result-audit"><summary>Supporting data files and replay</summary><div class="pad stack">' +
    (r.created_at ? '<p>Recorded · ' + esc(r.created_at) + '</p>' : '') +
    (r.rule_provenance?.approved_rule_sha256 ? '<p>Approved rule SHA-256<br><code>' + esc(r.rule_provenance.approved_rule_sha256) + '</code></p>' : '') +
    '<div class="actions">' + button("/api/runs/" + id + "/audit", "Download audit JSON") +
    (r.replay_available ? button("/api/runs/" + id + "/replay", "Download replay JSON") : '') + '</div>' +
    (r.replay_available ? '<form id="quickreplay">' + feedback + '<button type="submit">Replay this case</button></form>' : '') +
    '<details><summary>Full run result</summary>' + pre(r) + '</details></div></details>' : '') +
    '<footer class="print-reference"><p>Review record · ' + esc(r.run_id) + ' · ' +
    esc(reviewDate(r.created_at)) + '</p>' + (r.rule_provenance?.approved_rule_sha256 ? '<p>Approved rule SHA-256 · ' +
    esc(r.rule_provenance.approved_rule_sha256) + '</p>' : '') + '</footer></div>';
}
async function render() {
  const current = ++version;
  const [page = "overview", rawId, section, recordId] = location.hash.slice(1).split("/");
  const p = page || "overview";
  try {
    session = await api("session");
    if (current !== version) return;
  } catch (error) {
    if (current === version) {
      main.innerHTML = heading("Unable to open workspace", error.message);
      main.setAttribute("aria-busy", "false");
    }
    return;
  }
  $(".local").textContent = isDemo() ? "Public demo · sample data" : session.mode === "local" ? "Local workspace" : session.organization || "Private workspace";
  if (session.mode === "private") {
    const logout = document.createElement("button");
    logout.textContent = "Sign out";
    logout.onclick = async () => { await api("logout", {}); if (typeof allocationEntries !== "undefined") allocationEntries.clear(); location.reload(); };
    $(".local").append(logout);
  }
  const group =
    ["new", "deal", "compare"].includes(p) ? "deals" : ["release-report", "releases"].includes(p) ? "operator" : p === "run" ? "runs" : p;
  document.body.classList.remove("open");
  $("#menu").setAttribute("aria-expanded", "false");
  $("#crumb").textContent =
    { new: "Add a deal", deal: "Deal worksheet", run: "Payment finding", sample: "Sample report", compare: "Select a deal", releases: "Compare releases", "release-report": "Release decision" }[p] ||
    nav[p] ||
    "Review desk";
  $("#nav").innerHTML = Object.entries(nav).filter(([key]) => key !== "operator" || isOperator())
    .map(
      ([key, label]) =>
        '<a href="#' +
        key +
        '" ' +
        (group === key ? 'class="active" aria-current="page"' : "") +
        ">" +
        icon(key) +
        label +
        "</a>",
    )
    .join("");
  main.setAttribute("aria-busy", "true");
  try {
    if (["operator", "releases", "release-report"].includes(p) && !isOperator() || p === "new" && isDemo()) {
      main.innerHTML = heading("This page is unavailable for this account", "") + button("#overview", "Return to review desk");
      return;
    }
    if (p === "allocate") {
      if (typeof allocationView !== "function") {
        main.innerHTML = heading("Restart the workspace launcher", "Restart the local app to enable quick allocations.") + button("#compare", "Detailed payment check", true);
        return;
      }
      const dealId = decodeURIComponent(rawId || "");
      const [db, deal, example] = await Promise.all([
        api("dashboard"), dealId ? api("deals/" + encodeURIComponent(dealId)) : null,
        isDemo() && dealId ? api("deals/" + encodeURIComponent(dealId) + "/allocation-example") : null,
      ]);
      if (version !== current) return;
      dashboard = db;
      main.innerHTML = allocationView(deal, example);
      bindAllocation(deal);
      document.title = "Allocate a payment · Waterfall";
      return;
    }
    if (p === "operator") {
      const [diagnostics, db] = await Promise.all([api("operator"), api("dashboard")]);
      if (current !== version) return;
      main.innerHTML = heading("Operator area", "Review model configuration, workspace diagnostics and release comparisons.") +
        button("#releases", "Compare model releases", true) +
        panel("Model selection and technical checks", table(["Deal", "Controls"], db.deals.map(d => [
          esc(d.name), button(route("deal", d.id) + "/tools", "Open model and review options")
        ]), "Create a deal to configure a technical check.")) +
        panel("Run diagnostics", table(["Distribution", "Evidence"], db.runs.filter(r => r.mode !== "release").map(r => [
          esc(shortDeal(r.deal_id, r.deal_name) + " · " + paymentReviewName(r)),
          button(route("run", r.run_id) + "/tools", "Open audit and replay")
        ]), "Completed runs will appear here.")) +
        panel("Workspace diagnostics", pad(pre(diagnostics)));
      return;
    }
    if (p === "guide" || p === "releases" || p === "release-report") {
      if (p === "guide") {
        main.innerHTML = guideView(session.edition);
        if (!isOperator()) main.querySelector("[data-operator-guide]")?.remove();
        if (isDemo()) main.querySelectorAll('a[href^="#new"]').forEach(a => a.replaceWith(a.textContent));
      }
      else if (p === "releases") await renderReleases(rawId, section);
      else await renderReleaseReport(rawId);
      if (current === version) document.title = $("#content h1").textContent + " · Waterfall";
      return;
    }
    const fromGuide = p === "new" && ["ford", "mastr"].includes(rawId);
    const [db, item, previousReview] = await Promise.all([
      api("dashboard"),
      p === "deal" || p === "run" || fromGuide
        ? api(
            (p === "run" ? "runs/" : "deals/") +
              encodeURIComponent(decodeURIComponent(rawId || "")),
          )
        : Promise.resolve(null),
      p === "deal" && ["check", "approval"].includes(section) && recordId
        ? api("runs/" + encodeURIComponent(recordId)) : Promise.resolve(null),
    ]);
    if (current !== version) return;
    dashboard = db;
    const reviewingApproval = p === "deal" && section === "approval" && previousReview?.deal_id === item?.id && needsApprovalReview(previousReview);
    main.innerHTML =
      p === "new"
        ? createView()
        : p === "deal"
          ? dealView(item, reviewingApproval ? previousReview : null)
          : p === "run"
            ? runView(item)
            : overview(p, rawId, section);
    if (p === "new") bindNew(item);
    if (p === "deal") {
      const startingWorksheet = section === "from-allocation" && typeof allocationWorksheet === "function" ? allocationWorksheet(item) : previousReview;
      bindDeal(item, startingWorksheet, section === "case" ? recordId : section === "next" ? "next" : "", reviewingApproval);
      if (reviewingApproval) $("#applicable-approval").focus();
      if ((item.approved || item.approval_versions?.length) && !isDemo() && ["next", "check", "case", "from-allocation"].includes(section)) {
        const form = $("#runform");
        const dueDate = form.distribution_date;
        let control = form.case_id;
        if (section === "next" || section === "from-allocation") control = dueDate;
        if (section === "check") {
          const amounts = Array.from(form.querySelectorAll(".amount-control input")).filter(input => !input.closest("[hidden]"));
          const reason = previousReview?.reason || "";
          const namedField = amounts.find(input => reason.includes(input.name.replace(/^(scenario|payouts)_/, "")));
          control = /^Enter (?:a valid |the )?distribution date/i.test(reason) && !dueDate.closest("[hidden]") ? dueDate :
            namedField || amounts.find(input => !input.value) || amounts.find(input => input.name.startsWith("payouts_")) || control;
        }
        control.focus();
      }
      if (section === "check" && !$("#runform") && previousReview?.deal_id === item.id) {
        const destination = route("deal", item.id) + "/approval/" + encodeURIComponent(previousReview.run_id);
        $("#applicable-approval").insertAdjacentHTML("afterbegin", button(destination, "Review applicable approval"));
        $("#applicable-approval").focus();
      }
    }
    if (p === "run" && $("#quickreplay"))
      busy(
        $("#quickreplay"),
        async () => {
          const replay = await api(
            "runs/" + encodeURIComponent(item.run_id) + "/replay",
          );
          const result = await api(
            "deals/" + encodeURIComponent(item.deal_id) + "/runs",
            { mode: "replay", replay },
          );
          location.hash = route("run", result.run_id);
        },
        "Replaying case…",
      );
    document.title = $("#content h1").textContent + " · Waterfall";
  } catch (e) {
    if (current === version)
      main.innerHTML =
        heading("Workspace unavailable", e.message) +
        button("#overview", "Workspace overview");
  } finally {
    if (current === version) main.setAttribute("aria-busy", "false");
  }
}
window.addEventListener("hashchange", () => {
  window.scrollTo(0, 0);
  render();
});
render();
