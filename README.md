# Fin Waterfall

Fin Waterfall checks asset-backed securities payments against reviewer-approved payment rules. Given the available cash and amounts due, it calculates the expected allocation and compares it with submitted or modeled payouts for each covered tranche.

It is built for the people who sign off on a distribution: trustee and servicer operations, investor-reporting and structured-finance analysts, and the teams that release changes to payment models. It can also allocate a single payment before it is made.

## Why a matching total is not enough

The Ford example starts from the Ford Credit Auto Owner Trust 2025-C indenture, §8.2(c)(iv)–(v). Class A first-priority principal is paid before Class B interest. The case is a **synthetic stress test based on a real indenture**. The $50,000.00 cash constraint and the $100,000.00 Class A priority amount are hypothetical. The $111,056.33 Class B interest figure appears in the [September 2026 investor report](https://www.sec.gov/Archives/edgar/data/2092555/000209255526000043/fcaot2025-c10xdinvestorrep.htm).

| Synthetic inputs | Required by the contract | Faulty payment model |
| --- | --- | --- |
| $50,000.00 available before the two steps · Class A principal due $100,000.00 · Class B interest due $111,056.33 | Class A-1 principal $50,000.00 · Class B interest $0.00 | Class A-1 principal $0.00 · Class B interest $50,000.00 |

Both pay $50,000 in total. A check of the total passes, yet the entire $50,000 goes to the wrong tranche. Fin Waterfall returns **BLOCK**: Class A-1 principal is $50,000.00 short and Class B interest is $50,000.00 over.

The faulty model also matches all 70 class-level amounts in five filed Ford monthly reports (May to September 2026), because first-priority principal was zero in each of those periods. A historical match therefore misses the reversed priority. The synthetic search finds it, starting at $0.01 of cash.

The MASTR Asset Backed Securities Trust 2006-HE1 example is a **retrospective check**. It uses filed SEC data. On June 26, 2006, Class P received $190,392.82. The amended Form 10-D/A later reported $246,253.94 of qualifying prepayment charges, so the original payment was $55,861.12 short. The corrected figure became available after the distribution, so this shows what a check using the later figure finds, not what could have been caught on the day.

## How it works

1. **AI drafts the rule.** An analyst selects the governing excerpt of the contract. A language model proposes a structured rule and cites the source paragraph for each step. The self-hosted edition uses Qwen through a private llama.cpp process. The hosted edition uses Gemini on Vertex AI, and only after the analyst has reviewed the exact excerpt and confirmed that it may be sent.
2. **A human approves it.** A reviewer compares the draft with the source and approves that exact draft with its first covered distribution date. The approval stores a SHA-256 digest of the approved rule. Changing the rule requires a new approval.
3. **The Python engine calculates the payouts.** A deterministic engine applies the approved rule in integer cents. The model calculates no payouts, and approved rules can be checked with drafting disabled.

Each review returns **PASS**, **BLOCK** or **HOLD** with the per-tranche difference in integer cents. It records the approved rule and its digest, the governing clause, the inputs and the result. Completed reviews can be downloaded as PDF reports.

## Try it from a fresh clone

You need Python 3.11 or later. Node.js is needed only for the browser-script tests. Clone the repository, then run these commands from its root. If `python3 --version` reports an older release, use a newer interpreter such as `python3.13`.

```sh
python3 -m venv .mcp-venv
. .mcp-venv/bin/activate
python -m pip install .
```

Start the read-only sample desk. It runs the bundled Ford and MASTR examples, with no sign-in, uploads or saved history. Press Ctrl-C to stop it.

```sh
python scripts/start_app.py --mode demo --port 8790
```

Open [the quick allocation](http://127.0.0.1:8790/#allocate), [the detailed payment check](http://127.0.0.1:8790/#compare) or [the analyst guide](http://127.0.0.1:8790/#guide). The detailed examples show the submitted payment, the expected payment and the difference. The guide includes the governing excerpts, statement rows, units and filled CSVs.

Reproduce the Ford finding from the command line:

```sh
abs-waterfall-check history --adapter good --output out/history-good.json
abs-waterfall-check history --adapter faulty --output out/history-faulty.json
abs-waterfall-check search --adapter faulty --output out/case.json
abs-waterfall-check check out/case.json --adapter good
abs-waterfall-check check out/case.json --adapter faulty
```

Both history runs print `PASS: 70/70 class-level comparisons`. The search and the faulty check exit **1** (BLOCK). The correct check exits **0** (PASS). In CI, `check` exits **2** for HOLD, which covers malformed evidence, unavailable adapters and rule mismatches. Run the test suites with:

```sh
python -m unittest discover -s tests -v
node --test tests/*.test.cjs
```

The commands in the rest of this README use `.mcp-venv/bin/python`, which works without activating the environment.

## Supported rules

| Rule | What the application verifies |
| --- | --- |
| Two-step payment priority (Ford style) | Allocates the cash available immediately before the two steps, in the approved order, up to Class A first-priority principal due and Class B accrued interest due. Both amounts due are calculated upstream. |
| Class P prepayment charges (MASTR style) | Pays the period's qualifying collected prepayment charges to Class P. |
| Priority of payments, every step (opt-in) | Applies the available funds through each approved step in order. A step pays its amount due, or, for a final remainder step, all remaining funds. Payees that share a step are paid pro rata by amount due when the funds are short, with largest-remainder cent rounding. A submitted split counts as a match when each amount is the exact share rounded down or up and the step total is exact. The review lists any paragraph of the selection that no drafted step cites. |

## Current limits

- Amounts due come from the statement. The application does not calculate interest, fees, principal formulas or other upstream obligations.
- A general priority of payments has no triggers, conditions or percentage splits. If the order changes after an event, such as an event of default, approve a separate deal for each order and review the trigger separately.
- Counterexample search, stress tests and model release comparisons cover the two single-clause rules only.
- The Ford history covers five pre-maturity periods with zero first-priority principal. Reserve mechanics, cleanup calls, acceleration and full-deal stress are outside scope. See [Historical inputs and coverage](#historical-inputs-and-coverage).
- Uploaded contracts must be searchable PDFs or UTF-8 text. PDF import requires Poppler's `pdftotext`. For a scan, verify a transcription against the signed original.
- PASS means agreement within the tested scope. It is not legal or investment advice.

## What has been tested

| Area | Status |
| --- | --- |
| Fresh-clone run, November 22, 2026 | On macOS with Python 3.14.8, from a clean copy of the repository, the commands above ran as shown. `python -m unittest discover -s tests` ran 106 tests: 98 passed and the 8 PostgreSQL tests were skipped because `WATERFALL_TEST_DATABASE_URL` was not set. `node --test tests/*.test.cjs` ran 21 tests and all 21 passed. |
| Separate PostgreSQL 16 run, November 19, 2026 | Against PostgreSQL 16.16 in a throwaway local container, with Python 3.13, all 106 Python tests passed and none were skipped. The 8 PostgreSQL tests in `tests/test_hosted_postgres.py` passed: `test_restart_preserves_exact_bytes_and_shared_engine_reports`, `test_concurrent_workers_cannot_approve_two_versions_for_the_same_date`, `test_rollback_and_checkpoint`, `test_one_confirmed_send_across_two_workers_and_provider_failure`, `test_general_priority_of_payments_survives_restart`, `test_hosted_mcp_stdio_returns_only_status`, `test_hosted_roles_and_separate_demo_http_server` and `test_durable_sessions_password_rotation_and_revocation`. |
| GitHub Actions | `.github/workflows/verify.yml` installs the package and runs the Python suite on Python 3.13 on each push and pull request. It sets no database, so the 8 PostgreSQL tests are skipped there. |
| Local Qwen drafting | A real local Qwen model drafted the Ford payment order and the MASTR Class P rule, from an uploaded PDF and a pasted clause. The general priority of payments has been drafted only by test models. |
| Gemini on Vertex AI | The hosted edition, the consent flow and single-use confirmation, outage and retry, and PostgreSQL persistence are tested locally against a fake Vertex service in Docker. **No request has yet been sent to the real Vertex AI service.** Live draft quality, the model's output limits, IAM and IAP permissions and regional availability are unverified. |
| Live demo | The read-only demo runs locally with the command above. **There is no public hosted demo yet.** The Cloud Run runbook in [deploy/cloud-run.md](deploy/cloud-run.md) has not been run. |

## Editions

| Workspace | Who can use it | Where the information goes |
| --- | --- | --- |
| Read-only demo | Anyone who runs it can open the bundled Ford and MASTR examples. | Public examples live in a read-only snapshot. Uploads, drafting, operator tools and customer history are unavailable. |
| Hosted pilot | Named analysts, reviewers and operators at one organization per database. | The application extracts the PDF in memory. PostgreSQL stores the source text, filename and fingerprint, drafts, approvals, accounts, sessions and reviews. Gemini on Vertex AI receives the exact excerpt confirmed for that request, with standard instructions and a response schema. |
| Self-hosted | The same roles and review workflow on the customer's installation. | Source text, accounts, approvals and payment records stay in the customer's persistent directory. Qwen drafts through a private llama.cpp listener on the same host. |

### Try the hosted pilot locally

This runs the hosted edition with PostgreSQL and the fake Vertex service, so no data leaves your machine. Start a local Docker engine with Compose. With Colima, use `colima start --activate=false`. From the repository root, run the following commands. The second command creates your named reviewer and prompts for a password the first time.

```sh
sh scripts/hosted-local.sh up
sh scripts/hosted-local.sh add-user --username reviewer --role reviewer
```

Open the [hosted workspace](http://127.0.0.1:8820/), upload a synthetic PDF or paste a clause, and choose **Review excerpt for Gemini**. Check the displayed text, then confirm and send it. Review the draft beside the source, approve it, then enter the distribution. The fake drafts the supported rules from the selected paragraphs. It does not check the response format as Vertex does. [Local configuration, persistence and acceptance checks](deploy/hosted-local.md) · [Google Cloud deployment runbook (not yet run)](deploy/cloud-run.md)

## Check your own distribution

Use a named account in a private workspace. For a local evaluation, create the accounts once from the repository root. Substitute your organization and usernames, and enter each password at its prompt.

```sh
umask 077
mkdir -p private/pilot
.mcp-venv/bin/python -m abs_waterfall_check.access add-user --file private/pilot/users.json --organization "Pilot" --username analyst --role analyst
.mcp-venv/bin/python -m abs_waterfall_check.access add-user --file private/pilot/users.json --organization "Pilot" --username reviewer --role reviewer
.mcp-venv/bin/python scripts/start_app.py --mode private --users private/pilot/users.json --store private/pilot/store --port 8791 --model-port 8093
```

Drafting a new deal in private mode needs the local llama.cpp binary and a GGUF model in `.local-runtime/`. See [Local launch options for development](#local-launch-options-for-development). Open the private workspace and sign in. For another payment date, select the same approved deal. One organization shares the deals and saved reviews in its instance. A reviewer can both approve terms and check payments. [Private dashboard](http://127.0.0.1:8791/) · [Private installation, HTTPS, backup and restore](deploy/README.md)

**For a quick allocation**, select a saved deal, or provide a searchable contract PDF, a text file or a clause. Enter the payment date and amount. For a new source, the application looks for a supported clause, prepares the rule with local Qwen and presents it for dated reviewer approval. If several passages could apply, select the governing excerpt. The result shows how much each covered class receives and any cash left for later steps.

- For Class P, enter the period's qualifying collected prepayment charges. That amount goes to Class P.
- For payment priority, enter the cash left just before the two covered steps. The application asks for an amount due only when it can change the allocation. For example, if cash is $50,000 and Class A is first and due $100,000, Class A receives $50,000, and no Class B figure is needed. For the five bundled Ford statement dates, entering the filed cash with the original approval also supplies that period's dues and a filing reference.
- For every step of a priority of payments, tick **Allocate through every step of the priority of payments** before reading the contract. Select the complete clause, from its opening sentence to the last numbered step. After approval, enter the available funds and each amount due from the statement. Leave a step blank if the funds do not reach it. To try it, use the synthetic contract in `fixtures/synthetic-priority-of-payments.txt`.

**For a detailed payment check**, use the original worksheet.

1. Choose **Detailed payment check**, then select a saved deal or set up a new one. **What you'll need** lists the evidence. For a new deal, upload a searchable contract PDF or UTF-8 text file, or paste the governing clause.
2. Prepare the rule with local Qwen. Compare the draft with the selected source. A signed-in reviewer or operator confirms the exact draft and enters its first covered distribution date.
3. Select the payment date and whether the amounts are proposed or actual. Read the deal's checklist and enter total US dollars to two decimals, or import the Excel-compatible CSV. Use zero for a confirmed zero and leave missing figures blank. The approved terms effective on the payment date determine the expected allocations.
4. A **PASS** lets you download the PDF. A **BLOCK** shows the affected tranche, the signed difference and the clause. Choose **Correct and recheck** to create a new review. A **HOLD** takes you back to the missing inputs or approval. Each completed review keeps its exact approved rule and SHA-256 digest, and earlier reports stay in the review history.

## Local launch options for development

The original trusted desktop workspace is available with `.mcp-venv/bin/python scripts/start_app.py` at [the local dashboard](http://127.0.0.1:8765/). This unsigned mode is for a trusted development machine. For the customer deployment use private mode and the HTTPS recipe above. The Python package and PDF dependency install with `python -m pip install .` in the intended environment.

The launcher uses the local llama.cpp binary and GGUF in `.local-runtime/`. Alternatively, pass `--llama-server /path/to/llama-server --model-file /path/to/model.gguf`, or use `--use-running-model --model your-model-alias --model-port 8080` in trusted development mode for an existing loopback model. Private mode manages its own authenticated llama.cpp process. Pass `--without-qwen` in private mode to check saved approved deals with drafting disabled. If a port is busy, use the printed workspace URL for the matching instance or select `--port 0` and/or `--model-port 0` to allocate free ports. Keep the same store to reopen the same records and stop its existing process before moving it to a new port.

The local MCP server reads the installation’s store via `.mcp-venv/bin/python -m abs_waterfall_check.mcp_server --store /path/to/instance/store`. Its verification response is one of `PASS`, `BLOCK` or `HOLD`. Operators can configure an additional trusted payment program with `--adapter 'My calculator=json:python3 /absolute/path/to/calculator.py'`. Counterexample search applies to the two-step priority rule. [End-to-end Qwen and Ford CLI demo](#run-the-end-to-end-qwen-demo) · [MASTR CLI demo](#run-the-mastr-2006-he1-class-p-demo)

## Drafting options in the trusted development workspace

Self-hosted private installations use local Qwen for drafting. The hosted pilot offers Gemini on Vertex AI. The optional OpenAI and Claude settings below belong to the original unsigned development workspace. Saved approvals work without any drafting provider. To run the development app without starting Qwen, use:

```sh
.mcp-venv/bin/python scripts/start_app.py --without-qwen
```

To enable a development provider, set its API key through your local environment and set the corresponding model variable to a model ID available to your API account that supports structured outputs. Restart the development launcher after changing the environment.

| Choice under **Drafting provider** | API key environment variable | Model environment variable |
| --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | `OPENAI_DRAFT_MODEL` |
| Claude | `ANTHROPIC_API_KEY` | `ANTHROPIC_DRAFT_MODEL` |

On the deal page, review the exact excerpt, select the provider and confirm that it may receive that excerpt. Editing the text or switching providers clears the confirmation. The backend sends the numbered paragraphs and drafting instructions to the provider via HTTPS. Only the local backend reads the API keys. Approval, independent payment calculations, model comparison and audit storage all run locally. The exact provider and requested model are recorded in the draft. OpenAI requests use `store: false`. [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create) · [Claude Messages API](https://platform.claude.com/docs/en/api/messages/create)

## Compare releases

From a payment result, choose **Compare releases with these inputs** to carry its worksheet into the model comparison. The report shows each candidate payout less the approved payout (`delta_cents`) and the change from the current version (`candidate_minus_current_cents`).

Opening **Operator → Compare releases** selects the deal’s saved payment inputs and synthetic boundary cases. Opening it from a payment result selects that distribution. Use the checkboxes to change the scope. **Try an incomplete case** demonstrates a HOLD and the worksheet action needed to complete the check.

The local development and private launchers start three example payment services on loopback when no versions are configured. Open **Operator → Compare releases**, select the approved Ford deal, choose `example-pricing` versions **1.0** and **1.1**, and choose **Compare payouts**. The saved $50,000 stress and the generated boundaries use the same approved clause. In the stress case v1.0 pays Class A-1 principal, while v1.1 pays Class B interest. Both pay $50,000 in total. Choose that saved failure, select **1.2**, and choose **Replay against candidate**. Replay uses the captured current-version observation and calls the selected candidate on the original inputs. The full release run can then be repeated with the correction.

- **BLOCK** means the verifier confirmed a candidate mismatch. Inspect the per-class amounts and the governing clause. Other incomplete cases are listed as well.
- **HOLD** means required evidence is incomplete and no candidate mismatch has been confirmed. The report names the affected case, version or missing input and gives the next action.
- **PASS** means the candidate matched the approved allocation on every selected case and the required observations were available. A current-version mismatch is recorded separately, so a correction can pass.

### Connect a local payment service

Configure each version with a loopback HTTP endpoint at startup. The following commands run the standalone example services on fixed ports (one per terminal):

```sh
.mcp-venv/bin/python scripts/model_service.py --port 9011 --model-id desk --version 1.0 --behavior good
.mcp-venv/bin/python scripts/model_service.py --port 9012 --model-id desk --version 1.1 --behavior faulty
.mcp-venv/bin/python scripts/model_service.py --port 9013 --model-id desk --version 1.2 --behavior good
```

Start the dashboard in a fourth terminal. These registrations replace the auto-started examples. The command also works on a Mac without a local language model:

```sh
.mcp-venv/bin/python scripts/start_app.py --without-qwen \
  --pricing-model desk@1.0=http://127.0.0.1:9011/calculate \
  --pricing-model desk@1.1=http://127.0.0.1:9012/calculate \
  --pricing-model desk@1.2=http://127.0.0.1:9013/calculate
```

The release verifier sends one HTTP POST per case and version. Its JSON body is the scenario itself, in nonnegative **integer cents**. The only scenario fields are the three cash-and-due fields below for a payment-priority rule or `collected_prepayment_charges` for Class P. Each service returns HTTP 200 with a JSON object holding its configured model ID, version string and exact tranche payouts. Model identity is checked against the startup configuration.

| Rule | POST request body | Example response from the current version |
| --- | --- | --- |
| Payment priority | `{"cash":5000000,"class_a_principal":10000000,"class_b_interest":11105633}` | `{"model_id":"desk","version":"1.0","payouts":{"class_a_principal":5000000,"class_b_interest":0}}` |
| Class P entitlement | `{"collected_prepayment_charges":24625394}` | `{"model_id":"desk","version":"1.0","payouts":{"class_p":24625394}}` |

Calls go directly to the configured loopback address with a two-second socket timeout and a 64 KiB response limit. Missing, additional or duplicate payout fields, invalid amounts, a model/version mismatch, a redirect or an unavailable endpoint leave the observation incomplete. Endpoints receive only the numeric inputs. The clause, rule digest, expected payouts and results remain in the verifier. To exercise a timeout, start an example with `--delay 4` and register its port and version.

### Run the same release check from the CLI

Start the fixed-port services above. After launching the dashboard once, its default store contains the receipted Ford and MASTR rules. List the available case IDs, run a single saved case and replay its exported failure:

```sh
.mcp-venv/bin/python -m abs_waterfall_check release-cases --store private/web --deal ford

.mcp-venv/bin/python -m abs_waterfall_check release-check --store private/web --deal ford \
  --baseline-id desk --baseline-version 1.0 --baseline-endpoint http://127.0.0.1:9011/calculate \
  --candidate-id desk --candidate-version 1.1 --candidate-endpoint http://127.0.0.1:9012/calculate \
  --case-id ford-stress --export-case out/ford-release-failure.json

.mcp-venv/bin/python -m abs_waterfall_check release-replay out/ford-release-failure.json \
  --store private/web --deal ford \
  --candidate-id desk --candidate-version 1.2 --candidate-endpoint http://127.0.0.1:9013/calculate
```

The faulty check exits **1**, the corrected replay exits **0**, and an incomplete check exits **2**. Repeat `--case-id` to select several cases. Omitting it with `--deal` runs the deal’s saved distributions and generated boundary cases. Use `--case-id missing-input` to demonstrate HOLD. For a rule in a CLI-only approval store use `--rule-id` in place of `--deal` and optionally `--cases FILE` containing an array of objects with `id`, `label` and `scenario`. Each confirmed failing case can be downloaded from the review. The CLI’s `--export-case` writes the first failure. Use `--output FILE` for the full comparison.

## Synthetic search details

The synthetic search fixes Class A priority principal due at $100,000 and Class B interest due at $111,056.33. Cash is the amount available immediately before the two steps. The search checks zero, the first positive cent, $100 increments, and both sides of the full-funding threshold. The first divergence in ascending cash is $0.01. Moving down from full funding, the first divergence occurs at $211,056.32, a one-cent shortfall. The synthetic Class A-1 opening balance covers the full priority principal due, so §8.2(d) assigns the entire Class A payment to A-1. At $50,000 cash, the reference pays all $50,000 to Class A-1 principal and the faulty adapter pays all $50,000 to Class B interest. Both pay $50,000 in total. The report records candidate minus expected by payment, using signed decimal dollar strings. The bundled [synthetic regression](fixtures/ford-synthetic-regression.json) can also be checked directly. The search exits HOLD if its bounded probes produce no discrepancy.

## Candidate adapter and rule

Supply `--adapter your_module:calculate` with the module importable in the local Python environment. The callable receives a fresh dictionary of nonnegative integer cents with keys `cash`, `class_a_principal`, and `class_b_interest`. The latter two are amounts due. It returns a dictionary with both payment keys and their paid cents. The bundled `good` and `faulty` adapters implement the candidate interface independently of the reference allocator. Load only trusted local adapter code and run it under your environment's egress restrictions.

To test a separate program, use `--adapter 'json:python3 fixtures/json_payment_adapter.py faulty'` or select `good`. The command receives one scenario as JSON on stdin and returns one JSON payout object on stdout. The verifier starts it without a shell and allows five seconds per scenario.

Rule schema 1 describes the two supported payments with an ordered list, deal and rule versions, clause, and local approval metadata. The engine pays each step up to the lower of cash remaining and the amount due. The Ford rule tests indenture §8.2(c)(iv)–(v). Its runtime input is the first-priority Class A amount due, aggregated across the senior classes, and the Class B interest due. Synthetic checks stop at the two payment steps and use the stated A-1 balance to identify the senior class affected. The rule format supports only the ordering of those two obligations. [Ford execution indenture, §§8.2(c) and (d)](https://www.sec.gov/Archives/edgar/data/1129987/000110465925116294/tm2530629d11_ex4-1.htm)

Rule schema 2 routes `collected_prepayment_charges`, in integer cents, to `class_p`. Use `register --adapter 'observed:/path/to/payouts.json'` to compare an approved rule with a local JSON snapshot of a filed payout.

## Historical inputs and coverage

Each of five rows in [the replay fixture](fixtures/ford-history.json) links to its SEC report. Section II supplies total collections, purchase amounts, cleanup call, and reserve draw. Section III supplies trustee fees due and the **Calculated Amount** of A-2b interest. Section IV supplies **Actual** principal and interest for all seven classes as the expected outputs. Section V supplies opening note balances, opening and closing pool balances, and closing adjusted pool balance. Sections VI and VII supply target overcollateralization and opening and specified reserve balances. Payment dates come from the report headers and the preceding payment schedule. [May](https://www.sec.gov/Archives/edgar/data/2092555/000209255526000026/fcaot2025-c10xdinvestorrep.htm) · [June](https://www.sec.gov/Archives/edgar/data/2092555/000209255526000031/fcaot2025-c10xdinvestorrep.htm) · [July](https://www.sec.gov/Archives/edgar/data/2092555/000209255526000035/fcaot2025-c10xdinvestorrep.htm) · [August](https://www.sec.gov/Archives/edgar/data/2092555/000209255526000039/fcaot2025-c10xdinvestorrep.htm) · [September](https://www.sec.gov/Archives/edgar/data/2092555/000209255526000043/fcaot2025-c10xdinvestorrep.htm)

The prospectus supplies A-1 at 3.993% actual/360, A-2a at 3.99%, A-3 at 3.90%, A-4 at 3.98%, and B at 4.22% on 30/360, plus C at 0% and the monthly servicing fee of 1% annually on the opening pool. The replay derives both priority principal amounts and the regular principal cap from reported balances. Principal follows §8.2(d), including the proportional A-2a/A-2b split. Calculations round to cents using Decimal half-up, with A-2b receiving the remainder of the A-2 split to conserve cash. [Prospectus, Payment Dates and Interest Accrual, Servicing Fee, and Description of the Notes](https://www.sec.gov/Archives/edgar/data/2092555/000110465925114588/tm2530629d6_424b2.htm)

Coverage is limited to the five pre-maturity periods with full Class A interest and zero priority principal, reserve movement, and cleanup call. Independent SOFR fixing and A-2b accrual, loan-level yield supplement and target derivation, fee caps and arrears, acceleration, final maturity, and full-deal stress or reserve mechanics are outside scope. The A-2b comparison independently checks the allocation of its reported accrued amount. The five periods illustrate why a historical match can miss the reversed priority.

## Local operator CLI workflow

Use a customer-owned directory accessible only to the local analyst and MCP server account. These commands use the bundled synthetic private fixture. Review the proposed order, exact section, and verbatim evidence in the draft before running approval.

```sh
digest=$(abs-waterfall-check extract fixtures/synthetic-private-contract.txt \
  --section 'Section 4.2' --deal 'Synthetic private deal' --deal-version 1 \
  --rule-version 1 --output private/draft.json)
cat private/draft.json
rule_id=$(abs-waterfall-check approve private/draft.json \
  --sha256 "$digest" --reviewer 'Local analyst' --store private)
abs-waterfall-check register fixtures/synthetic-private-scenario.json \
  --rule-id "$rule_id" --store private --adapter faulty
```

Approval binds the reviewer to the digest of the reviewed draft and writes a rule under `private/rules/` and its receipt under `private/approvals/`. For `search` and `check`, use `--store private --rule-id "$rule_id"` to select it. Local audits include the rule ID, SHA-256 of the approved rule file, SHA-256 of the reviewed draft, and clause reference. Replay requires the approval that generated the case. Reapprove any draft whose older stored rule predates approval receipts.

Registration verifies the payout locally and prints the opaque run ID and status. Review the complete result in `private/runs/<run_id>.json`. A registered HOLD also has a local report with the failure reason. Restrict write access to the store to trusted local operators. Approval is an operator action under these filesystem permissions.

For local inference, install the runtime and download the model during setup. Bind the runtime to loopback.

- **Ollama.** Start `OLLAMA_NO_CLOUD=1 OLLAMA_HOST=127.0.0.1:11434 ollama serve` and add `--model <installed-model-name>` to the extraction command. This backend calls `/api/generate`.
- **llama.cpp.** Start `llama-server --host 127.0.0.1 --port 8080 --model /path/to/model.gguf --alias local-extractor` and add `--backend llama.cpp --model local-extractor` to the extraction command. This backend calls `/v1/chat/completions`. Use an endpoint accessible only to trusted local callers.

Both backends use a direct connection to `127.0.0.1`. Use `--port` to override the default. The model proposes the payment order and identifies the supporting paragraphs. The CLI copies that passage directly from the source so the draft retains a verbatim excerpt. The default deterministic stub supplies the fixture's order for CI. Review either draft before approving it. After setup, you can block all external network access while leaving loopback available for inference.

### Run the end-to-end Qwen demo

Start Qwen2.5 1.5B Instruct Q4_K_M with an already downloaded GGUF. Keep the server running while you execute the extraction in a second terminal.

```sh
llama-server --host 127.0.0.1 --port 8080 --ctx-size 4096 \
  --model /path/to/qwen2.5-1.5b-instruct-q4_k_m.gguf --alias qwen-local
```

Save Ford indenture §8.2(c)(iv) and (v) as two paragraphs in `out/ford-excerpt.txt`. Once the loopback server is running, reproduce the demo from the repository root with one command. [Ford execution indenture](https://www.sec.gov/Archives/edgar/data/1129987/000110465925116294/tm2530629d11_ex4-1.htm)

```sh
python scripts/demo_ford_release.py --excerpt out/ford-excerpt.txt --reviewer "Your name"
```

Review each displayed draft and type `approve` to approve its displayed digest. The demo uses the new Ford approval to find and export a synthetic regression, replays it against both JSON adapters, and verifies that the opposite approval flips the expected payouts on the same inputs. It then runs the test suite, including the historical and MCP privacy tests. The demo succeeds when the correct Ford adapter passes and the reversed adapter returns the expected release failure, exit 1. The audit directory printed by the command contains both drafts, approval receipts, the replayable case, and `summary.json`.

### Run the MASTR 2006-HE1 Class P demo

For the June 26, 2006 distribution, the prospectus supplement directs all mortgage-loan prepayment charges received during the related Prepayment Period to Class P. Save the paragraph at lines 4630–4633, under **Description of the Certificates → Overcollateralization Provisions**, as `out/mastr-excerpt.txt`. With the same loopback Qwen server running, use this command and review the draft before typing `approve`. [Prospectus supplement](https://www.sec.gov/Archives/edgar/data/1352928/000088237706000631/d439989_424b5.txt)

```sh
python scripts/demo_mastr_class_p.py --excerpt out/mastr-excerpt.txt --reviewer "Your name"
```

The common input for both checks is **$246,253.94** from Form 10-D/A Exhibit 99.1, **Certificateholder Account Statement → Certificate Account → Deposits → Prepayment Penalties** (line 515). The original observed payout is **$190,392.82** from the Class P row in the original Exhibit 99.1, **Certificateholder Distribution Summary → Total Distribution** (line 224). The same row and column in the amended exhibit report **$246,253.94** (line 235). The verifier returns `BLOCK` with a **$55,861.12 shortfall** for the original payout and `PASS` for the amended payout. [Original Form 10-D](https://www.sec.gov/Archives/edgar/data/1352928/000105640406002258/mab06he1_10d-200606.txt) · [Amended Form 10-D/A](https://www.sec.gov/Archives/edgar/data/1352928/000105640406002437/mab06he1_10da-200606.txt)

The original distribution was June 26, the original report was filed July 6, and the trust paid the **$55,861.12** Class P correction on July 13. The amended report, signed July 14, made the corrected collection figure available after the original distribution. Its payout total includes the corrective payment. This is a retrospective check using the later collection figure. [Form 10-D/A, Part I, Item 1 and signature page](https://www.sec.gov/Archives/edgar/data/1352928/000105640406002437/mab06he1_10da-200606.txt)

The demo prints a local `summary.json` path with the filing references, rule SHA-256, and links to both run reports. Its scope is this period's Class P prepayment charges. Both runs use the same approved rule and collection input.

Configure the local MCP client to launch the following command, using the absolute path to the store and the executable in the installed environment:

```sh
abs-waterfall-mcp --store /absolute/path/to/private
```

The single tool is `verify_distribution` with `{"run_id":"<32 lowercase hex characters>"}`. It reads the completed local verification status and emits exactly one text content item containing PASS, BLOCK, or HOLD. Unknown runs, missing evidence, and invalid tool arguments return HOLD. The integration tests start the SDK stdio server and check all three statuses, invalid tool arguments, and the output and error streams for malformed protocol requests.
