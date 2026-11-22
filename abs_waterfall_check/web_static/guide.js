"use strict";

const guidePaymentHints = {
  scenario: {
    cash: "Cash left immediately before the two reviewed steps, after earlier fees and interest.",
    class_a_principal: "First Priority Principal Payment due under the clause, before applying the cash limit.",
    class_b_interest: "Accrued interest due to Class B for this distribution, before applying the cash limit.",
    collected_prepayment_charges: "Mortgage-loan prepayment charges received during the related Prepayment Period.",
  },
  payouts: {
    class_a_principal: "Reported or proposed principal allocated at the first-priority step only.",
    class_b_interest: "Reported or proposed interest payment to Class B at the reviewed step.",
    class_p: "Total reported or proposed distribution to Class P for this payment date.",
  },
};

function guideView(edition = "development") {
  const draftingStep = {
    hosted: 'Choose <strong>Review excerpt for Gemini</strong>. Read the exact text in the preview. Choose <strong>Edit excerpt</strong> to change it, or confirm the checkbox and choose <strong>Send to Gemini</strong>. The confirmed excerpt is the only customer data Gemini receives. The workspace extracts the text from your file and retains the text with your deal. If drafting is interrupted, reopen the saved deal, review the excerpt again and retry.',
    "self-hosted": 'Choose <strong>Prepare payment rule</strong> to draft the selected terms with Qwen inside your organization’s installation. Quick allocation prepares the local draft after reading a supported contract.',
    demo: 'The public demo opens the saved, approved Ford and MASTR examples. To set up your own deal or draft a rule you need an account in a private workspace.',
  }[edition] || 'Choose <strong>Prepare payment rule</strong> to extract the order or recipient from your selected clause.';
  return heading("Analyst guide", "Trace a payment from its governing clause through the distribution statement and the review decision.") + `
    <nav class="guide-jump" aria-label="Guide sections">
      <button type="button" data-guide-section="contract">Contract and approval</button>
      <button type="button" data-guide-section="distribution">Distribution inputs</button>
      <button type="button" data-guide-section="mastr">MASTR example</button>
      <button type="button" data-guide-section="ford">Ford example</button>
      <button type="button" data-guide-section="decision">Results and release</button>
    </nav>
    <section class="guide-block"><h2>Choose how to review the distribution</h2>
    <p><a href="#allocate">Allocate a payment</a> calculates what each covered class should receive. Select a saved deal or add a contract, then enter the payment date and the money available under the clause. A reviewer approves new terms once. For Class P, use only qualifying prepayment charges. For Ford, use cash remaining before the two reviewed steps. If an upstream amount due is needed, the worksheet asks for it in priority order. For every step of a longer priority of payments, tick <strong>Allocate through every step of the priority of payments</strong>, select the complete clause and enter each amount due from the statement.</p>
    <p><a href="#compare">Detailed payment check</a> compares the expected payments with an independently prepared proposal or trustee statement. Use it to enter the full worksheet, import a CSV, investigate a difference and save the PDF report. In the private workspace, a calculated allocation can carry its known inputs into this check.</p></section>
    <section class="guide-block"><h2 id="guide-contract" tabindex="-1">Prepare the contract and the evidence</h2>
    <p>Start with the executed agreement or prospectus supplement in force on the distribution date. Identify the legal trust name, series, document date and section. Open the trustee or servicer statement for the same trust and payment date. Keep the filing URL, exhibit, row, column, report version and date each figure became available in your working papers.</p>
    <p>In <a href="#new">New deal</a>, enter the trust name and a clause reference detailed enough to find again. Choose <strong>Payment priority</strong> for a rule ordering Class A principal and Class B interest, or <strong>Prepayment charges to Class P</strong> for a rule directing all such receipts to Class P. Choose <strong>Priority of payments · every step</strong> to approve the complete ordered list of payees. Payees paid pro rata stay in one step. Amounts due still come from the statement. If the order depends on triggers, conditions or percentage splits, escalate it for separate analysis.</p>
    <p>Paste the governing text or upload a searchable PDF or UTF-8 text file. Save the contract, then narrow the excerpt on the deal page to exact, consecutive text from the imported source. Keep the numbering, recipient, timing, qualifiers and adjacent steps. For a scanned or poorly extracted page, compare a transcription with the original and use the paste option.</p>
    <p>${draftingStep}</p><p>Compare the quoted evidence and the proposed order or recipient with the signed document. For a priority of payments, check each step against its quoted paragraph. If the review lists a paragraph that no step cites, draft again. If they differ, correct the excerpt and regenerate. Once they agree, have a reviewer enter the first payment date covered by the clause, confirm the review and approve. The approval record identifies the reviewed draft version. The payment engine uses the approved terms to calculate every later distribution without calling AI.</p></section>
    <section class="guide-block"><h2 id="guide-distribution" tabindex="-1">Enter a distribution</h2>
    <p>Open the approved deal and choose <strong>Enter my own amounts</strong>. For actual payments, enter the distribution date on the trustee statement. For proposed payments, enter the distribution date the model is projecting. Model preparation and filing dates serve different purposes. Choose actual for a trustee statement or proposed for a model output. The checker uses the latest approved version effective on or before that date. Use one distribution period and one documented version of each amount. Under <strong>Distribution amounts</strong>, enter the remaining cash and amounts already calculated as due on the trustee statement or supporting worksheet. For Class P, enter the period’s qualifying collections from the account statement. Under <strong>Reported or proposed payments</strong>, enter the amounts paid on the statement or produced by the model being reviewed. The checker uses the first group to calculate the approved payouts and compares them with the second group.</p>
    <p>The form and CSV use total USD to two decimal places. If the source reports thousands of dollars, multiply by 1,000 first. The optional JSON editor uses integer cents ($123.45 is 12345). A confirmed zero is 0.00. Leave an unavailable amount blank and obtain the supporting worksheet. Under <strong>Import from a spreadsheet</strong>, download the deal template or use a filled file below. Preserve the two headers and item names. Import the CSV, confirm the date and values, then choose <strong>Compare payments</strong>. The CSV contains amounts only. Enter the distribution date separately after every CSV import. Only the bundled Ford stress case can run undated, using its original approved terms.</p><p><strong>How the approved payout is calculated.</strong> For payment priority, pay the first obligation the smaller of its amount due and available cash. Subtract that payment from cash, then pay the second obligation up to its amount due from the remainder. For Class P, the expected payment equals the full amount of qualifying prepayment charges collected.</p></section>
    <section class="guide-block"><h2 id="guide-mastr" tabindex="-1">Worked example · MASTR Class P</h2><p>Recreate the June 26, 2006 payment in a new deal. The original and amended reports provide two outcomes for the same distribution.</p>
    <details open><summary>Copy the governing clause into a new deal</summary>
    <dl><dt>Deal name</dt><dd>MASTR Asset Backed Securities Trust 2006-HE1</dd><dt>Payment rule</dt><dd>Prepayment charges to Class P</dd><dt>Clause reference</dt><dd>Prospectus Supplement, February 22, 2006 — Description of the Certificates — Overcollateralization Provisions, lines 4630–4633</dd><dt>Clause text</dt><dd><blockquote id="mastr-guide-clause">On each Distribution Date, the trust administrator will withdraw from the distribution account all amounts representing prepayment charges, if any, in respect of the Mortgage Loans received during the related Prepayment Period and will distribute these amounts to the holders of the Class P Certificates.</blockquote><button type="button" data-copy-clause="mastr">Copy clause text</button> <span role="status" id="mastr-copy-status"></span></dd></dl>
    <p><a href="#new/mastr">Start a new deal with this clause</a>. Verify that the draft assigns all charges for the related period to Class P. <a href="https://www.sec.gov/Archives/edgar/data/1352928/000088237706000631/d439989_424b5.txt">Read the governing supplement</a>.</p></details>
    <details open><summary>Trace the two dollar amounts</summary><dl>
    <dt>Prepayment charges collected</dt><dd>${guidePaymentHints.scenario.collected_prepayment_charges} Enter 246253.94 from the amended Exhibit 99.1, Certificateholder Account Statement → Certificate Account → Deposits → Prepayment Penalties (line 515). Principal prepaid is a different account item.</dd>
    <dt>Class P payment</dt><dd>${guidePaymentHints.payouts.class_p} In Exhibit 99.1, Certificateholder Distribution Summary, locate Class P (MAB6HE1P0) and read Total Distribution. Enter 190392.82 from the original (line 224), or 246253.94 from the amendment (line 235).</dd></dl>
    <p><a href="/mastr-original.csv" download>Original payment CSV</a> · <a href="/mastr-amended.csv" download>Amended payment CSV</a></p></details>
    <p><strong>Why the dates matter.</strong> The original Form 10-D, filed July 6, reported the June 26 payment. Using the corrected collection later disclosed in Form 10-D/A gives a $55,861.12 shortfall (BLOCK). The trust paid the difference on July 13. The amendment, signed July 14, includes that correction in the Class P total (PASS). Both comparisons are retrospective. A June 26 reviewer would need contemporaneous collection evidence to run the same check. See Part I, Item 1 and the signature page. <a href="https://www.sec.gov/Archives/edgar/data/1352928/000105640406002258/mab06he1_10d-200606.txt">Original distribution report</a> · <a href="https://www.sec.gov/Archives/edgar/data/1352928/000105640406002437/mab06he1_10da-200606.txt">Amended report and correction</a>.</p></section>
    <section class="guide-block"><h2 id="guide-ford" tabindex="-1">Worked example · Ford payment priority</h2>
    <p>Ford Credit Auto Owner Trust 2025-C’s November 1, 2025 indenture places the First Priority Principal Payment fourth and Class B accrued interest fifth. Select both numbered paragraphs of §8.2(c)(iv)–(v). Follow §8.2(d) for allocation within principal. The comparison covers cash at those two steps. <a href="https://www.sec.gov/Archives/edgar/data/1129987/000110465925116294/tm2530629d11_ex4-1.htm">Read the execution indenture</a>.</p>
    <details open><summary>Copy the two governing steps into a new deal</summary>
    <blockquote id="ford-guide-clause">(iv) fourth, for allocation as principal under Section 8.2(d), the First Priority Principal Payment;

(v) fifth, to the Noteholders of Class B Notes, the Accrued Note Interest for the Class B Notes;</blockquote>
    <button type="button" data-copy-clause="ford">Copy clause text</button> <span role="status" id="ford-copy-status"></span>
    <p><a href="#new/ford">Start a new deal with this clause</a>. Verify that the draft places the First Priority Principal Payment before Class B interest.</p></details>
    <details open><summary>Read the five fields from the waterfall</summary><p>In the May 2026 collection report, the header gives June 15, 2026 as the payment date. Section III, Distributions, supplies all five fields for this historical comparison.</p>
    <dl><dt>Cash available for distribution</dt><dd>${guidePaymentHints.scenario.cash} Read Remaining Available Funds on the row immediately above First Priority Principal Payment, after Class A-4 interest. The filed balance is $31,531,970.01. If reconstructing, use available funds less all actual upstream payments.</dd>
    <dt>Class A principal due</dt><dd>${guidePaymentHints.scenario.class_a_principal} Read Calculated Amount on the First Priority Principal Payment row ($0.00). For the bundled pre-maturity periods the calculation is the excess, floored at zero, of aggregate opening Class A balances over closing adjusted pool balance (Section V). Regular principal and aggregate Class A principal distributions include other steps.</dd>
    <dt>Class B interest due</dt><dd>${guidePaymentHints.scenario.class_b_interest} Read Calculated Amount on Interest – Class B Notes ($111,056.33).</dd>
    <dt>Class A principal payment</dt><dd>${guidePaymentHints.payouts.class_a_principal} Read Amount Paid on First Priority Principal Payment ($0.00), or the matching output from the proposed waterfall. Section IV reports $31,420,913.68 for A-1 principal in this period, which includes regular principal.</dd>
    <dt>Class B interest payment</dt><dd>${guidePaymentHints.payouts.class_b_interest} Read Amount Paid on Interest – Class B Notes ($111,056.33), or the proposed output for that line.</dd></dl>
    <p><a href="https://www.sec.gov/Archives/edgar/data/2092555/000209255526000026/fcaot2025-c10xdinvestorrep.htm">May collection report, June 15 payment, Sections III–V</a></p></details>
    <p><strong>Try a cash shortfall.</strong> The filled CSV is a synthetic stress case without a filing payment date. It uses $50,000 remaining cash, $100,000 of first-priority principal due, and $111,056.33 of Class B interest due. The proposed model pays $0 to principal and $50,000 to B interest. The approved rule allocates the entire $50,000 to principal. Both totals balance, but the recipient differences produce BLOCK. The A-1 opening balance in this scenario covers the senior allocation. To check these amounts against a newly set up or amended deal, import the CSV, choose proposed payments and enter the modeled payment date covered by that approval. June 15, 2026 is the filed distribution date for the historical example above. Use it for a modeled scenario only if your proposal targets that date. <a href="/ford-stress.csv" download>Ford synthetic stress CSV</a>.</p></section>
    <section class="guide-block"><h2 id="guide-decision" tabindex="-1">Resolve the result and use it in a release check</h2>
    <dl><dt>PASS · amounts match</dt><dd>The reviewed lines match this approved rule for these inputs. Record the payment date, source versions and result. Other waterfall steps and periods require their own evidence.</dd>
    <dt>HOLD · review incomplete</dt><dd>Read the stated reason. For missing amounts, return to the distribution and fill the named fields from the worksheet. For an approval or model error, verify the approved version or ask the model owner to restore the calculation. Run again when the evidence is available.</dd>
    <dt>BLOCK · payments differ</dt><dd>Read each recipient’s expected payment, reported or proposed payment and difference (reported or proposed minus expected). Recheck units, period, row, column and clause. Retain the original finding and route a confirmed mismatch to the servicer or model owner. Rerun with the documented correction and retain both results.</dd></dl>
    <div data-operator-guide><h3>Compare two model versions</h3>
    <p>From a payment result, choose <strong>Compare releases with these inputs</strong> to start with that distribution selected. You can add other cases before running the comparison.</p>
    <ol><li>Open <a href="#releases">Compare releases</a> and select the <strong>Approved deal</strong>. Choose the deployed calculation as <strong>Current version</strong> and the proposed calculation as <strong>Candidate version</strong>. In the bundled Ford example, start with v1.0 and v1.1.</li>
    <li>Under <strong>Payment cases</strong>, select a saved payment to reuse a worksheet’s inputs, or choose generated boundary cases. The boundary cases are synthetic. For Ford they vary cash around zero, first-priority funding and full funding. Class P cases vary collections. Each model receives the same case inputs and calculates its own payouts.</li>
    <li>Choose <strong>Compare payouts</strong>. Read <strong>Candidate less approved</strong> for divergence from the clause, and <strong>Change from current</strong> for the impact of switching versions. A zero change can still mask a shared error. The decision tests the candidate against the approved payout.</li>
    <li>If the candidate differs, select a <strong>Failed case</strong> and choose <strong>Download failed case</strong> to retain the captured inputs and payouts. Under <strong>Recheck the failed case</strong>, choose the <strong>Corrected candidate</strong> (v1.2 for the bundled Ford correction) and <strong>Replay against candidate</strong>. The replay reuses the captured current payout. A PASS covers this one case. Compare v1.2 across the full original selection before deciding on release.</li></ol>
    <p>If a case has missing inputs or a version cannot return usable payouts, it is incomplete. When no candidate difference is established, an incomplete comparison gives HOLD. When another selected case establishes a candidate difference, the overall decision is BLOCK and the incomplete rows still need resolution. Correct the source inputs or service, rerun all affected cases, and keep the original report with the new one using <strong>Download comparison</strong>.</p>
    <p><a href="#releases/ford">Compare Ford releases</a> · <a href="#releases/mastr">Compare MASTR releases</a></p></div>
    <p><a href="#new">Create a deal</a> · <a href="#deals">Open an existing deal</a> · <a href="#runs">Review history</a></p></section>`;
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-guide-section]");
  if (!button) return;
  const section = document.getElementById("guide-" + button.dataset.guideSection);
  if (section) {
    section.focus({preventScroll: true});
    section.scrollIntoView({block: "start"});
  }
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-copy-clause]");
  if (!button) return;
  const deal = button.dataset.copyClause;
  const clause = document.getElementById(deal + "-guide-clause");
  const status = document.getElementById(deal + "-copy-status");
  try {
    await navigator.clipboard.writeText(clause.textContent);
    status.textContent = "Clause copied.";
  } catch {
    const range = document.createRange();
    range.selectNodeContents(clause);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    status.textContent = "Clause selected. Use your device’s Copy command.";
  }
});
