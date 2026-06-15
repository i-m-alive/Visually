# Deploying Visually to Azure Container Apps

Deploys the full stack to **Azure Container Apps (ACA)** on an Azure for Students
subscription, with images in **Azure Container Registry (ACR)** and the platform
database on **Azure Database for PostgreSQL Flexible Server**.

## Live URLs (this deployment)

- **Frontend:** https://vly-frontend.braveglacier-c2d0e1cf.southeastasia.azurecontainerapps.io
- **API:**      https://vly-agent-service.braveglacier-c2d0e1cf.southeastasia.azurecontainerapps.io

Run `./deploy.sh urls` to re-print these.

## What gets created

| Component | Azure resource | Ingress | Notes |
|---|---|---|---|
| vly-frontend | Container App | **public** | Next.js; API URL baked in at build time |
| vly-agent-service | Container App | **public** | API + WebSockets (`/agent/stream`) |
| vly-query-executor | Container App | internal | scales to zero when idle |
| vly-schema-crawler | Container App | internal | scales to zero when idle |
| vly-render-service | Container App | internal | scales to zero when idle |
| vly-export-service | Container App | internal | shares `uploads` volume with agent |
| vly-redis | Container App | internal TCP | always-on, no persistence |
| visually-pg-0615 | PostgreSQL Flexible Server | — | **managed**, Burstable B1ms, the platform DB |
| (images) | Container Registry `visuallyacr0615` | — | Basic SKU |
| `uploads` | Storage account `visuallystor0615` | — | Azure Files share for screenshots/exports |

All apps run in the **shared** Container Apps environment `navispark-env`
(student subs allow only one per subscription), prefixed `vly-` to avoid
collisions. LLM calls still go to **AWS Bedrock** — AWS keys are ACA secrets.

## ⚠️ Important: AWS credentials are temporary

The AWS keys currently deployed are **temporary STS credentials** (`ASIA…` +
session token). They **expire** (usually within hours). When they do, anything
that calls Bedrock (chart generation, chat, schema matching) starts failing with
expired-token errors — login/register and the UI keep working.

For a standing deployment, create a long-lived **IAM user** with Bedrock access,
put `AKIA…` keys (no session token) in `secrets.env`, and re-apply:

```bash
# update secrets.env (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, blank AWS_SESSION_TOKEN)
az containerapp update -n vly-agent-service -g visually-rg \
  --set-env-vars AWS_ACCESS_KEY_ID=secretref:aws-access-key  # (and re-set secrets)
```
Easiest is to re-run `./deploy.sh agent backend` after editing secrets — see below.

## Cost notes (student subscription)

- Always-on: **Postgres Flexible Server (B1ms)** + **redis** container. Everything
  else scales to **zero** when idle, so you mostly pay when actually using it.
- First request to an idle service has a few-seconds cold start — normal (~15-20s).
- To cut cost when not demoing: **stop the database**
  `az postgres flexible-server stop -g visually-rg -n visually-pg-0615`
  (auto-restarts after 7 days, or `... start`), and set redis to zero:
  `az containerapp update -n vly-redis -g visually-rg --min-replicas 0`.
- To delete **everything we created**: `az group delete -n visually-rg --yes`
  (this does NOT touch the shared `navispark-env` in `navispark-rg`, but our
  apps live in it — delete them with
  `az containerapp delete -n vly-... -g visually-rg` if needed).

## Prerequisites

- Azure CLI (`az version`) + `containerapp` extension (auto-installed in `prereqs`).
- **Docker Desktop running** — images build locally (ACR Tasks/`az acr build` is
  blocked on student subs).
- Logged in: `az login` on the "Azure for Students" subscription.

## First-time deploy

```bash
cd azure
cp secrets.env.example secrets.env      # then fill AWS keys, JWT, ENCRYPTION_KEY, PG_FLEX_PASSWORD
# review names in config.env (ACR_NAME / STORAGE_ACCT / PG_FLEX_NAME must be globally unique)
./deploy.sh                              # runs all phases in order
```

Phases (run individually as `./deploy.sh <phase>`):

```
prereqs   subscription + resource providers + containerapp extension
acr       resource group + container registry
build     build & push 5 backend/render images (local Docker -> ACR)
env       resolve the shared Container Apps environment
storage   storage account + uploads file share
pgflex    managed Postgres Flexible Server (+ db, firewall, SSL-off)
data      redis container
backend   query-executor, schema-crawler, render-service
agent     agent-service (public) + export-service
frontend  build frontend (bakes public API URL) + deploy
migrate   alembic upgrade head (one-off Container Apps Job)
urls      print public URLs
```

## GitHub Actions (CI/CD)

`.github/workflows/deploy.yml` builds all 6 images on the runner, pushes to ACR,
rolls each container app to the new image, and runs migrations — on push/merge to
`main` (or manually via the Actions tab → Run workflow).

**One-time setup:**
1. A service principal `visually-gha` (Contributor on `visually-rg`) was created
   for CI. Its credential JSON is in `DEPLOYMENT_INFO.local.md` (git-ignored).
2. Add these repo secrets (GitHub → **Settings → Secrets and variables →
   Actions → New repository secret**):
   | Secret | Purpose |
   |---|---|
   | `AZURE_CREDENTIALS` | service-principal JSON (Azure login) |
   | `AWS_ACCESS_KEY_ID` | Bedrock — set on agent/query/schema each run |
   | `AWS_SECRET_ACCESS_KEY` | Bedrock |
   | `AWS_SESSION_TOKEN` | only for TEMPORARY keys; leave unset for permanent IAM keys |
3. Push/merge to `main` → the workflow runs.

The workflow re-applies the AWS credentials to the 3 AWS-calling apps
(`vly-agent-service`, `vly-query-executor`, `vly-schema-crawler`) on every run —
so **rotating expiring keys = update the GitHub secret(s) and re-run**. JWT_SECRET
and ENCRYPTION_KEY stay on the apps untouched. Watch runs in the **Actions** tab.
Rotate the SP secret with `az ad sp credential reset --id <clientId>`.

## After a code change (manual)

```bash
# backend service (example: agent)
az acr login -n visuallyacr0615
docker build -t visuallyacr0615.azurecr.io/agent-service:v2 -f backend/agent_service/Dockerfile backend
docker push visuallyacr0615.azurecr.io/agent-service:v2
az containerapp update -n vly-agent-service -g visually-rg --image visuallyacr0615.azurecr.io/agent-service:v2

# frontend: rebuild with the SAME build args, then update
```
(Or bump `TAG` in config.env and re-run `./deploy.sh build` + the deploy phases.)

## Useful commands

```bash
az containerapp logs show -n vly-agent-service -g visually-rg --follow   # tail logs
az containerapp job execution list -n vly-migrate -g visually-rg -o table
./deploy.sh urls
```

## Hard-won gotchas baked into this kit

- **Region policy:** the subscription only allows `malaysiawest, southeastasia,
  eastasia, koreacentral, austriaeast`. We use `southeastasia`.
- **One ACA environment per subscription** — we reuse the existing `navispark-env`
  and prefix every app `vly-`.
- **`az acr build` is blocked** on student subs → images are built locally with Docker.
- **Git Bash mangles `/...` args** (resource IDs) → the script sets
  `MSYS_NO_PATHCONV=1` and converts YAML file paths with `cygpath`.
- **The containerapp extension's YAML parser** rejects inline flow maps (`{ }`)
  and the `ingress` block → YAML is block-style and ingress is set via
  `az containerapp ingress enable` after create.
- **Postgres can't run on Azure Files** (SMB blocks `initdb`'s chmod) → the DB is
  a managed Flexible Server with `require_secure_transport=OFF`.
- **Next.js bakes `NEXT_PUBLIC_*` at build time** → the frontend image is built
  after agent-service exists, with its public FQDN as a build arg.
- **Next prod build** fails on lint/type errors → `next.config.mjs` sets
  `eslint.ignoreDuringBuilds` + `typescript.ignoreBuildErrors`.
