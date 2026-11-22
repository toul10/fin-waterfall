# Deploy one private distribution-review desk

This recipe runs one organization’s workspace on a dedicated macOS host. A native Python process serves the dashboard, starts llama.cpp and reads the customer’s persistent directory. nginx terminates HTTPS and passes requests to the dashboard on loopback. The public demo runs as a separate installation using public examples only.

## 1. Prepare the host

Use a dedicated operating-system service account, an encrypted local volume with working file locks, and a separate encrypted backup destination. The private browser hostname in this example is `review.example.org`. Provision DNS and a trusted certificate covering that name. Restrict the customer endpoint to the organization’s network or identity gateway. Expose only the HTTPS listener to its users. Keep model ports and the Python listener on loopback.

Set up the source checkout under the service account. Install Python 3.13, nginx with HTTP SSL, and Poppler for searchable PDF extraction. The local verification used Python 3.13.15, nginx 1.31.8 and the checkout’s macOS llama.cpp build. From the checkout, prepare the environment before loading any customer documents.

```sh
python3.13 -m venv .mcp-venv
.mcp-venv/bin/python -m pip install -c deploy/python313.constraints.txt .
.mcp-venv/bin/python -m pip check
nginx -v
pdftotext -v
```

Provision the approved llama.cpp binary and its matching shared libraries under `.local-runtime/llama/`, and the Qwen2.5-1.5B-Instruct Q4_K_M GGUF under `.local-runtime/models/`. The prepared Mac checkout already has both. Keep the checkout, runtime and installation-specific secrets out of web-accessible directories. For an offline installation, stage these artifacts and the Python wheels on a trusted build machine before transfer. Retain the release source, constraints, model, runtime and their checksums for recovery.

```sh
.local-runtime/llama/llama-server --version
shasum -a 256 .local-runtime/models/qwen2.5-1.5b-instruct-q4_k_m.gguf
```

The model used in local acceptance has SHA-256 `183715c435899236895da3869489cc30ac241476b4971a20285b1a462818a5b4`. The bundled llama.cpp build identifies its revision as `6c01da627`. Pass explicit `--llama-server` and `--model-file` paths to the launcher if your installation uses another layout.

On a new Mac, stage this exact GGUF and the llama.cpp runtime before moving the installation offline. Run these commands in the checkout on the staging machine. Omit the download when transferring the already verified model from the prepared Mac. The model registry supplies the weights. The running application uses llama.cpp. [Qwen model artifact](https://ollama.com/library/qwen2.5:1.5b/blobs/183715c43589) · [llama.cpp installation](https://github.com/ggml-org/llama.cpp/blob/master/docs/install.md)

```sh
brew install cmake poppler
mkdir -p .local-runtime/models
git clone https://github.com/ggml-org/llama.cpp.git .local-runtime/llama-src
git -C .local-runtime/llama-src checkout --detach 6c01da627
cmake -S .local-runtime/llama-src -B .local-runtime/llama-build -DCMAKE_BUILD_TYPE=Release
cmake --build .local-runtime/llama-build --config Release --target llama-server -j 4
QWEN_SHA=183715c435899236895da3869489cc30ac241476b4971a20285b1a462818a5b4
QWEN_FILE=.local-runtime/models/qwen2.5-1.5b-instruct-q4_k_m.gguf
curl -fL "https://registry.ollama.ai/v2/library/qwen2.5/blobs/sha256:$QWEN_SHA" -o "$QWEN_FILE.download"
echo "$QWEN_SHA  $QWEN_FILE.download" | shasum -a 256 -c - && mv "$QWEN_FILE.download" "$QWEN_FILE"
```

On this fresh build, pass `--llama-server .local-runtime/llama-build/bin/llama-server` to the private launcher. Keep the associated build libraries with it. The prepared checkout’s launcher already selects its working binary by default.

## 2. Create the organization and its accounts

Choose an empty instance directory on the encrypted volume. The operator provisions a unique username for each individual and uses the same organization name for every account. The commands prompt for passwords. Reuse this directory and organization name across normal restarts.

```sh
umask 077
INSTANCE=/Volumes/WaterfallPrivate/customer
mkdir -p "$INSTANCE"
.mcp-venv/bin/python -m abs_waterfall_check.access add-user --file "$INSTANCE/users.json" --organization "Example organization" --username alice --role analyst
.mcp-venv/bin/python -m abs_waterfall_check.access add-user --file "$INSTANCE/users.json" --organization "Example organization" --username morgan --role reviewer
.mcp-venv/bin/python -m abs_waterfall_check.access add-user --file "$INSTANCE/users.json" --organization "Example organization" --username sam --role operator
```

| Role | Work available through the private server |
| --- | --- |
| Analyst | Create a deal, draft a clause locally, check distributions, reopen organization reports and download PDFs. |
| Reviewer | Analyst functions plus explicit approval of the reviewed draft and effective date. The server records the signed-in identity. |
| Operator | Reviewer functions, diagnostics, installed adapter/model selection, release comparisons and raw audits or replay downloads. |

The account file contains salted scrypt hashes. The process loads it on startup. An HTTPS session has an eight-hour lifetime and uses an HttpOnly, Secure, SameSite=Strict cookie. Sign-out revokes that session. Restarting the backend revokes all sessions. The store records its organization and takes an exclusive serving lock. All users in an organization share its deals and distribution reviews. Give each additional organization its own host or isolated instance, account file, storage and keys.

## 3. Start the private workspace and HTTPS listener

Run these two foreground commands in separate terminals from the checkout. Use the private service account for the app. Give the proxy account access to its own certificate, key and runtime directory. It needs a network connection to the backend only. Use the exact public origin, including the HTTPS port when applicable. Choose fixed backend ports for predictable startup and health checks.

```sh
umask 077
INSTANCE=/Volumes/WaterfallPrivate/customer
.mcp-venv/bin/python scripts/start_app.py --mode private \
  --users "$INSTANCE/users.json" --store "$INSTANCE/store" \
  --port 8810 --model-port 8092 --origin https://review.example.org:8443
```

```sh
.mcp-venv/bin/python scripts/serve_https.py --hostname review.example.org \
  --listen 0.0.0.0 --port 8443 --upstream-port 8810 \
  --cert /secure/tls/fullchain.pem --key /secure/tls/key.pem \
  --runtime-dir /secure/proxy/review
```

The proxy launcher validates the generated nginx configuration before starting. It preserves the original Host, negotiates TLS 1.2 or 1.3, rate-limits sign-in, disables access logs and sets HSTS. Private drafting runs local Qwen with a fresh process-local authentication token on each launch. The launcher verifies that inference rejects an anonymous request and suppresses model logs. For a verification-only installation, use the same private launcher with `--without-qwen` and omit the inference health check. Payment models used by operators run under the installation’s control. Run trusted adapters under the same egress restrictions as the application. The asset bundle, uploaded source text, payment worksheets, approval receipts and run records stay in the installation. Downloaded reports and CSVs go to the analyst’s device.

Manage these foreground commands with the host’s service supervisor under their designated service identities. Set its working directory to the checkout, give it the required executable search path, arrange restart after failure or reboot, and test recovery after a planned restart. Use protected local service logs. Keep DNS, TLS rotation, the runtime, volume unlock and service configuration in the host recovery plan.

## 4. Verify health and access

```sh
curl --fail --silent --show-error https://review.example.org:8443/healthz
curl --silent --output /dev/null --write-out '%{http_code}\n' https://review.example.org:8443/api/dashboard
curl --fail --silent --show-error http://127.0.0.1:8092/health
```

The first call must return `{"status": "ok"}` and the second must return HTTP 401 before sign-in. The third checks the model locally. For a private CA add `--cacert /path/to/ca.pem` to HTTPS calls. Monitor the HTTPS and inference checks separately. Exercise an authenticated synthetic distribution after install or restore to verify disk access, approval selection and PDF generation.

Open the private HTTPS URL, sign in and select **Check a distribution**. A reviewer approves the draft and its first covered date. A later distribution can reuse these terms. Save one run ID and its approved rule SHA-256 from a PDF to check against a future restore.

For a customer-owned local MCP client, launch the following stdio server with the restricted service identity. Configure its working directory and absolute paths in the client. The verification tool returns only a status. It does not require a network-facing MCP listener.

```sh
.mcp-venv/bin/python -m abs_waterfall_check.mcp_server --store /Volumes/WaterfallPrivate/customer/store
```

## 5. Offer the public examples separately

On a separate host, or in an isolated environment with its own service account and filesystem, deploy the public checkout and Python environment. Give this instance access to the public fixtures and its own TLS material only. Keep the customer volume, account file, service credentials, model and backup destination outside its environment.

```sh
.mcp-venv/bin/python scripts/start_app.py --mode demo --port 8811 --origin https://demo.example.org:8444
```

```sh
.mcp-venv/bin/python scripts/serve_https.py --hostname demo.example.org \
  --listen 0.0.0.0 --port 8444 --upstream-port 8811 \
  --cert /secure/demo-tls/fullchain.pem --key /secure/demo-tls/key.pem \
  --runtime-dir /secure/proxy/demo
```

Check `https://demo.example.org:8444/healthz` and open the demo in a signed-out browser. Ford and both MASTR reports are available. The server builds its read-only example results from the bundled verifier and approvals in temporary storage. It accepts no upload, private run ID or write operation. For a loopback preview, use `.mcp-venv/bin/python scripts/start_app.py --mode demo --port 8790` and open [the sample desk](http://127.0.0.1:8790/).

## 6. Back up and restore

Stop the private backend and all writers, including CLI jobs and account provisioning. Pause automatic restarts while backing up. The backup tool acquires the same exclusive lock as the server. It copies the account file, deal sources, retained draft and rule versions, approvals, and runs. Supply a new archive filename outside the instance on each backup.

```sh
umask 077
INSTANCE=/Volumes/WaterfallPrivate/customer
BACKUPS=/Volumes/WaterfallBackups
ARCHIVE="$BACKUPS/review-$(date -u +%Y%m%dT%H%M%SZ).zip"
.mcp-venv/bin/python scripts/instance_backup.py create "$INSTANCE" "$ARCHIVE"
```

This ZIP contains confidential plaintext and password hashes. Use encrypted backup storage and encrypted transfer. Protect the backup repository and its independent integrity record. The internal SHA-256 manifest checks for corruption during restore. Back up certificate material and host configuration through the organization’s separate secrets-recovery process.

Restore a trusted backup into a **new** directory on the encrypted volume. The restore verifies every file against the inventory and refuses to overwrite an existing installation.

```sh
RECOVERED=/Volumes/WaterfallPrivate/customer-restored
.mcp-venv/bin/python scripts/instance_backup.py restore "$ARCHIVE" "$RECOVERED"
.mcp-venv/bin/python scripts/start_app.py --mode private \
  --users "$RECOVERED/users.json" --store "$RECOVERED/store" \
  --port 8810 --model-port 8092 --origin https://review.example.org:8443
```

Use the existing HTTPS proxy, sign in again, reopen the saved run and compare the status, amounts and approval digest with the retained report. After acceptance, update the supervised backend to use the restored paths and resume backups. Keep the old directory until the owner accepts the recovery.

## Before confidential use

Complete the customer’s deployment and access review. On the target host, verify disk and backup encryption, controlled egress, separate demo and customer service identities, TLS renewal, file/device retention, named-account provisioning and revocation, monitoring and recovery from an independent backup. Integrate the customer’s MFA or SSO gateway if its policy requires one. Have the deal owner validate the applicable clauses, effective dates and upstream worksheet fields, including any terms outside the two supported rules. Test the target environment’s availability and load requirements.

Local acceptance exercised the native app and nginx with a local test certificate, separate demo and private processes, signed-in analyst and reviewer, operator restrictions, offline backup, restart, restored login, PDF downloads and status-only MCP. The customer’s DNS, certificates, network controls and service supervision are deployment steps.
