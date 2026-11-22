# Run the hosted edition on your machine

The local stack runs the hosted Python app, PostgreSQL 16 and a fake Vertex Gemini service. Use synthetic documents here. The fake exposes the last 256 request bodies in memory so you can inspect exactly what the local app sent. It has no Google credentials. The production image includes the verifier, browser app, public examples and Poppler. Compose mounts the fake server from the checkout into a separate test container.

## Start and sign in

Install Docker with Compose v2 or later, Python 3 and a local Docker engine. With Colima, start the engine with `colima start --activate=false`. Run from the repository root. Choose a personal username when provisioning the first account.

```sh
sh scripts/hosted-local.sh up
sh scripts/hosted-local.sh add-user --username reviewer --role reviewer
```

Open the workspace at the address printed by the launcher. Use `127.0.0.1` consistently so the browser and the server agree on the canonical origin. Analysts can prepare terms and check distributions. Reviewers can also approve terms. Operators additionally get release comparisons, diagnostics and raw audit downloads. Add additional users with the same command and `--role analyst` or `--role operator`. [Hosted dashboard](http://127.0.0.1:8820/) · [Readiness](http://127.0.0.1:8820/readyz)

The local database has no published port. The app and fake bind their published ports to loopback. Set `HOSTED_PORT` and `FAKE_PORT` in your shell before launching if 8820 or 8829 is occupied. The app reads its HTTP port from `PORT` inside the container. Compose persists organization data in the `fin-waterfall-local_reviews` volume. Keep the generated `private/hosted-local/compose.env` with that volume. The launcher creates no default user or password for sign-in.

```sh
sh scripts/hosted-local.sh restart
sh scripts/hosted-local.sh reset-password --username reviewer
sh scripts/hosted-local.sh down
sh scripts/hosted-local.sh up
```

`down` stops the containers and keeps the database volume. Restarting the fake clears the request log. Disabling a user or resetting their password revokes their existing sessions. The private Mac installation has its own account file and filesystem store. [Self-hosted Qwen installation, HTTPS and backup](README.md)

## Check what leaves the app

1. Open a new deal. Supply a synthetic PDF containing the rule and a cover-page marker. Enter a distribution figure in the quick workflow. Select only the operative paragraphs, then choose **Review excerpt for Gemini**. Read the displayed text before authorizing a send. Every character in that selection, including numbers typed inside it, is part of the request.
2. Compare the fake’s request count before and after preview. Editing the excerpt requires a fresh preview and confirmation. To rehearse an outage, run the second command below before choosing **Send to Gemini**.
3. After the simulated failure, reopen the deal and review the retained excerpt. Confirm again and inspect the successful request. Its user message contains the selected text. The other payload fields contain application instructions and the response format. File bytes, surrounding source, deal metadata and worksheet figures are never appended by the application.
4. Approve the successful draft and check the distribution. Download the report, restart the app, then reopen the saved report and approval. Compare the provider request count across these operations.

To rehearse a full priority of payments, paste or upload `fixtures/synthetic-priority-of-payments.txt` in **Allocate a payment** and tick **Allocate through every step of the priority of payments**. Select Section 8.2(c) through step (viii). The fake returns each numbered paragraph as a step, so the draft lists eight steps with two pro rata steps. It does not check the response format the way Vertex does. Draft the same synthetic clause with the real model before relying on a deployment.

```sh
curl -fsS http://127.0.0.1:8829/requests | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["requests"]))'
curl -fsS -X POST http://127.0.0.1:8829/fail-next
curl -fsS http://127.0.0.1:8829/requests | python3 -m json.tool
```

## Verify examples and persistence

In **Review history**, open Ford’s synthetic stress review. It shows $50,000 due to Class A principal and $50,000 submitted to Class B interest. Both totals are $50,000. MASTR’s retrospective Class P check uses $246,253.94 of collections published after the original distribution. The original $190,392.82 payment gives BLOCK. The corrected $246,253.94 gives PASS.

For the automated checks, install the hosted dependencies in the test virtual environment and supply a disposable PostgreSQL database whose test role may create schemas. The PostgreSQL tests create and remove their own schemas. They run alongside the verifier, historical, privacy, HTTP access, backup and launcher tests. Setting the optional `WATERFALL_TEST_MCP_USER` to a dedicated login in that test database also checks its status-view-only database permissions.

```sh
.mcp-venv/bin/python -m pip install -c deploy/python313.constraints.txt -c deploy/hosted.constraints.txt '.[hosted]'
WATERFALL_TEST_DATABASE_URL="$TEST_DATABASE_URL" .mcp-venv/bin/python -m unittest discover -s tests -v
node --test tests/*.test.cjs
```

For deployment use an organization-specific database, a real Vertex model and the platform service account. Configure HTTPS and organization-level access, run the restore drill, and check live model behavior with synthetic inputs before collecting confidential files. [Google Cloud deployment and recovery](cloud-run.md)
