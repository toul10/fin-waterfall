# Hosted pilot on Google Cloud

This recipe provisions one authenticated customer organization in a dedicated Google Cloud project. Cloud Run serves the analyst dashboard over HTTPS behind Identity-Aware Proxy (IAP). Cloud SQL for PostgreSQL holds the extracted contract text, exact drafts, consent records, approvals, distribution figures, reports, named accounts and sessions. PDF bytes are processed in memory and discarded after extraction. The image needs no object bucket or writable persistent volume.

Gemini on Vertex AI receives the excerpt shown on the confirmation screen, together with application instructions and an output schema. The approved rule runs in Python. Use synthetic documents until the operator and customer complete the acceptance checks below.

## 1. Choose the project, deployment region and organization

Run the commands yourself in Bash from the checkout. Use an existing billed project, a current `gcloud` CLI, Docker with Buildx, PostgreSQL's `psql` client and the Cloud SQL Auth Proxy v2. The provisioning identity needs permission to enable services, provision Cloud SQL, manage the named IAM bindings, publish the image and deploy Cloud Run using the runtime service account. Separate that identity from the runtime account.

```bash
set -o pipefail
export CLOUDSDK_CORE_PROJECT='replace-with-project-id'
export WF_REGION='europe-west3'
export WF_VERTEX_REGION='europe-west3'
export WF_VERTEX_MODEL='gemini-3.5-flash'
export WATERFALL_ORGANIZATION='Customer Pilot'
export WF_GROUP='reviewers@example.com'
export WF_SERVICE='waterfall'
export WF_SQL='waterfall-pg'
export WF_REPO='waterfall'
export WF_SECRET='waterfall-db-url'
export WF_SA="waterfall-run@${CLOUDSDK_CORE_PROJECT}.iam.gserviceaccount.com"
export WF_PROJECT_NUMBER="$(gcloud projects describe "$CLOUDSDK_CORE_PROJECT" --format='value(projectNumber)')"
export WF_ORIGIN="https://${WF_SERVICE}-${WF_PROJECT_NUMBER}.${WF_REGION}.run.app"
export WF_CONNECTION="${CLOUDSDK_CORE_PROJECT}:${WF_REGION}:${WF_SQL}"
export WF_IMAGE="${WF_REGION}-docker.pkg.dev/${CLOUDSDK_CORE_PROJECT}/${WF_REPO}/pilot"
```

Use an immutable organization name per database. Check the chosen model's regional availability, lifecycle, quota and your customer's processing-location requirements on the deployment date. The examples use a short service name to fit Cloud Run's deterministic HTTPS hostname. [Model locations and capabilities](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-flash) · [Model lifecycle](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions) · [Cloud Run URLs](https://docs.cloud.google.com/run/docs/triggering/https-request)

## 2. Create the runtime identity and database

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com sqladmin.googleapis.com \
  secretmanager.googleapis.com aiplatform.googleapis.com iam.googleapis.com iap.googleapis.com
gcloud iam service-accounts create waterfall-run --display-name='Waterfall hosted pilot'
gcloud iam roles create waterfallDraft --project="$CLOUDSDK_CORE_PROJECT" \
  --title='Draft one payment rule' --stage=GA \
  --permissions=aiplatform.endpoints.predict,serviceusage.services.use
gcloud projects add-iam-policy-binding "$CLOUDSDK_CORE_PROJECT" \
  --member="serviceAccount:$WF_SA" --role="projects/$CLOUDSDK_CORE_PROJECT/roles/waterfallDraft"
gcloud projects add-iam-policy-binding "$CLOUDSDK_CORE_PROJECT" \
  --member="serviceAccount:$WF_SA" --role=roles/cloudsql.client

gcloud sql instances create "$WF_SQL" --database-version=POSTGRES_16 --edition=ENTERPRISE \
  --region="$WF_REGION" --tier=db-custom-2-7680 --availability-type=REGIONAL \
  --assign-ip --connector-enforcement=REQUIRED --deletion-protection \
  --backup-start-time=02:00 --backup-location="$WF_REGION" --retained-backups-count=14 \
  --enable-point-in-time-recovery --retained-transaction-log-days=7
gcloud sql databases create waterfall --instance="$WF_SQL"
gcloud sql users set-password postgres --instance="$WF_SQL" --prompt-for-password
```

Create a fresh administrator password in your password manager. This recipe uses a public Cloud SQL address with connector enforcement, an empty authorized-network list and an authenticated, encrypted connector channel. Cloud Run mounts the database socket below `/cloudsql`. Choose network topology and backup location with the customer. [Cloud Run and Cloud SQL](https://docs.cloud.google.com/sql/docs/postgres/connect-run) · [Vertex inference permissions](https://cloud.google.com/vertex-ai/generative-ai/docs/access-control) · [Runtime service identity](https://docs.cloud.google.com/run/docs/securing/service-identity)

On the operator's workstation, sign in with an identity granted Cloud SQL Client for this pilot. Run the proxy in a second terminal with the same `WF_CONNECTION` value. Keep it running through account setup.

```bash
gcloud auth application-default login
cloud-sql-proxy --address 127.0.0.1 --port 15432 "$WF_CONNECTION"
```

In the first terminal open `psql -X -W -h 127.0.0.1 -p 15432 -U postgres -d waterfall`. Run these statements interactively. At `\password` enter a distinct generated database password for `wf_app` and save it in your password manager. The application creates its own tables and status view within this database.

```sql
CREATE ROLE wf_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
REVOKE ALL ON DATABASE waterfall FROM PUBLIC;
GRANT CONNECT ON DATABASE waterfall TO wf_app;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO wf_app;
\password wf_app
\q
```

Build the connection string with the exact app password when prompted. This helper percent-encodes the credentials and socket path. It writes directly into a pipe or shell substitution. Keep shell tracing disabled.

```bash
set +x
wf_dsn() {
  python3 -c 'import getpass, sys, urllib.parse as u
pw = getpass.getpass("wf_app database password: ")
params = {"host": sys.argv[1]}
if len(sys.argv) == 3: params["port"] = sys.argv[2]
print("postgresql://wf_app:" + u.quote(pw, safe="") + "@/waterfall?" + u.urlencode(params), end="")' "$@"
}
wf_dsn "/cloudsql/$WF_CONNECTION" | gcloud secrets create "$WF_SECRET" --replication-policy=automatic --data-file=-
gcloud secrets add-iam-policy-binding "$WF_SECRET" \
  --member="serviceAccount:$WF_SA" --role=roles/secretmanager.secretAccessor
```

Use an approved user-managed Secret Manager replication policy when the customer's agreement specifies locations. Cloud Run references a numbered secret version. Store the PostgreSQL administrator password separately. [Secret Manager access](https://docs.cloud.google.com/secret-manager/docs/access-control) · [Cloud Run secrets](https://docs.cloud.google.com/run/docs/configuring/services/secrets)

## 3. Enroll named workspace users

Connect through the local proxy. Install the hosted extra in a local virtual environment if needed. Supply an individual username and password for each operator, reviewer and analyst. The CLI prompts for the account password and confirmation.

```bash
python3 -m venv .hosted-ops-venv
.hosted-ops-venv/bin/python -m pip install '.[hosted]'
export DATABASE_URL="$(wf_dsn 127.0.0.1 15432)"
.hosted-ops-venv/bin/python -m abs_waterfall_check.postgres add-user --username pilot-operator --role operator
.hosted-ops-venv/bin/python -m abs_waterfall_check.postgres add-user --username pilot-reviewer --role reviewer
.hosted-ops-venv/bin/python -m abs_waterfall_check.postgres add-user --username pilot-analyst --role analyst
unset DATABASE_URL
```

For automation, pipe one password line from your secret manager to the same `add-user` command with `--password-stdin`. The `reset-password --username NAME --password-stdin` and `disable-user --username NAME` commands revoke that user's existing app sessions. Run them with the same proxy DSN and organization. Close the proxy when finished.

If the customer uses MCP, provision a distinct database login with permission to connect to this database, use its schema and select from `wf_mcp_status` only. Keep that credential separate from the application DSN. Run `python -m abs_waterfall_check.mcp_server --hosted` over stdio inside the trusted environment with that login’s `DATABASE_URL` and the same `WATERFALL_ORGANIZATION`. The sole verification tool accepts a run ID and returns one status. Restore these grants when restoring a backup that excludes privileges.

## 4. Publish an image and enable HTTPS access

Build from the repository root using the supplied Dockerfile and its allowlisted build context. Choose a new image tag for each release. The service deploys the Artifact Registry digest.

```bash
gcloud artifacts repositories create "$WF_REPO" --repository-format=docker --location="$WF_REGION"
gcloud auth configure-docker "${WF_REGION}-docker.pkg.dev"
docker buildx build --platform linux/amd64 --tag "$WF_IMAGE:pilot-1" --push .
WF_DIGEST="$(gcloud artifacts docker images describe "$WF_IMAGE:pilot-1" --format='value(image_summary.digest)')"
gcloud beta services identity create --service=iap.googleapis.com --project="$CLOUDSDK_CORE_PROJECT"
gcloud run deploy "$WF_SERVICE" --region="$WF_REGION" --image="$WF_IMAGE@$WF_DIGEST" \
  --service-account="$WF_SA" --execution-environment=gen2 --port=8080 \
  --add-cloudsql-instances="$WF_CONNECTION" --set-secrets="DATABASE_URL=$WF_SECRET:1" \
  --set-env-vars="WORKSPACE_ORIGIN=$WF_ORIGIN,WATERFALL_ORGANIZATION=$WATERFALL_ORGANIZATION,GOOGLE_CLOUD_PROJECT=$CLOUDSDK_CORE_PROJECT,GOOGLE_CLOUD_LOCATION=$WF_VERTEX_REGION,GEMINI_MODEL=$WF_VERTEX_MODEL" \
  --cpu=1 --memory=1Gi --concurrency=2 --max-instances=3 --timeout=300s \
  --startup-probe='httpGet.path=/readyz,httpGet.port=8080,periodSeconds=5,timeoutSeconds=3,failureThreshold=36' \
  --liveness-probe='httpGet.path=/healthz,httpGet.port=8080,periodSeconds=30,timeoutSeconds=3,failureThreshold=3' \
  --iap --no-allow-unauthenticated
gcloud run services add-iam-policy-binding "$WF_SERVICE" --region="$WF_REGION" \
  --member="serviceAccount:service-${WF_PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com" \
  --role=roles/run.invoker
gcloud iap web add-iam-policy-binding --resource-type=cloud-run --service="$WF_SERVICE" \
  --region="$WF_REGION" --member="group:$WF_GROUP" --role=roles/iap.httpsResourceAccessor
gcloud run services describe "$WF_SERVICE" --region="$WF_REGION" --format='value(status.url)'
```

Use the configured `WF_ORIGIN` for browser traffic. IAP authenticates the customer's Google group. The app then enforces the signed-in workspace user's assigned role and records the approver's name. Follow Google's custom OAuth client setup for external identities or projects outside a Google organization. Enable the customer's MFA policy at the identity provider. The Cloud Run service account obtains Application Default Credentials from the platform. Omit `GOOGLE_APPLICATION_CREDENTIALS`, API keys and `GEMINI_FAKE_ENDPOINT` in the deployed revision. [IAP on Cloud Run](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run) · [Health checks](https://docs.cloud.google.com/run/docs/configuring/healthchecks) · [Gemini API request format](https://cloud.google.com/vertex-ai/generative-ai/docs/reference/rest/v1/projects.locations.publishers.models/generateContent)

## 5. Accept the pilot with synthetic data

1. Open the HTTPS origin as an approved group member and sign in as an analyst. Open a second browser profile outside the group and verify that IAP denies entry. Confirm that an analyst cannot approve or reach operator actions.
2. Upload synthetic Ford text with a distinctive line outside the two payment steps. Stop at excerpt preview. Check Vertex usage, confirm one send, and review the verbatim evidence and model result. Repeat with Class P. Verify structured output, region, service-account permissions, response time and the customer's Vertex data-governance settings on the live project.
3. Approve the exact draft under a reviewer account with an effective date. Verify a Ford $50,000 misallocation and its correction. The MASTR original payout should show a retrospective $55,861.12 shortfall and the amended payout should match. Payment checks should leave Vertex generation counts unchanged. Download the PDF and reopen the review after rolling a new revision.
4. Inspect Cloud Run request/error metrics, Cloud SQL capacity, backup status, IAP membership and access logs. Configure alerts for persistent 5xx errors, unhealthy instances, backup failures and cost. Keep request-body capture disabled. Decide who can read the database and backups, retain or delete documents, rotate credentials and respond to incidents. The pilot serializes workspace edits across instances.

## 6. Back up, rehearse restore and roll back

Take an on-demand backup before migrations or pilot acceptance. Record the successful backup ID, replayable run ID, approved rule hash, app image digest and secret version. Automated backups run daily and transaction logs support a seven-day PITR window in this recipe.

```bash
gcloud sql backups create --instance="$WF_SQL" --description='pre-pilot verification'
gcloud sql backups list --instance="$WF_SQL"
gcloud sql instances describe "$WF_SQL" --format='yaml(settings.backupConfiguration)'
```

Rehearse recovery into a fresh isolated instance with the team's chosen recovery time in UTC. Keep the restored database confined to the same customer's authorized operators.

```bash
export WF_RECOVERY_TIME='replace-with-UTC-RFC3339-time-within-retention'
export WF_RESTORE='waterfall-restore-drill'
gcloud sql instances clone "$WF_SQL" "$WF_RESTORE" --point-in-time="$WF_RECOVERY_TIME"
gcloud sql instances describe "$WF_RESTORE" --format='value(connectionName)'
```

To test a specific backup instead, provision a **new empty restore instance** of the same PostgreSQL version using the instance-creation command above with a unique target name. Set `WF_BACKUP_ID` to the intended source backup. The restore command overwrites the target.

```bash
gcloud sql backups restore "$WF_BACKUP_ID" --backup-instance="$WF_SQL" --restore-instance="$WF_RESTORE"
```

Start a proxy on a different local port for the restored instance. Connect using `wf_app`, check `wf_workspace.organization` and compare the saved run and approval. A restoration also restores sessions and outstanding confirmations. Before bringing a restored database online, run the following **on the isolated restore target** as `wf_app` to invalidate both. Check the restored account roster against current access approvals.

```sql
BEGIN;
DELETE FROM wf_sessions;
DELETE FROM wf_documents WHERE collection='draft-consents';
COMMIT;
```

For recovery cutover, quiesce writes, record the agreed recovery point, create a new DSN secret version for the restored socket using `wf_dsn` and `gcloud secrets versions add "$WF_SECRET" --data-file=-`. Deploy a revision with the restored instance attachment and new secret version. Verify a known report and hash before readmitting analysts. Keep the original database for the agreed recovery window. [PostgreSQL PITR](https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/configure-pitr) · [Backup restore](https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/restoring)

For an application rollback against a compatible database schema, identify the last accepted revision by digest, then route traffic back to it.

```bash
gcloud run revisions list --service="$WF_SERVICE" --region="$WF_REGION"
gcloud run services update-traffic "$WF_SERVICE" --region="$WF_REGION" \
  --to-revisions="$WF_PREVIOUS_REVISION=100"
```

Cloud Run traffic rollback preserves the current database. Before rolling back across a schema or database cutover, confirm the image, schema, database attachment and secret version for the target revision. The cloud deployment, live model quality, cloud access policies, monitoring, regional controls and restore drill remain operator acceptance steps. [Cloud Run rollback](https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration)
