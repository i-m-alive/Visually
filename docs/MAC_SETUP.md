# Visually — Mac Setup & Operations Guide

A complete reference for running, developing, and deploying **Visually** on macOS
(first-time-Mac friendly). Covers local dev, the local vs Azure database, viewing
logs, deployment, and troubleshooting.

> **Architecture in one line:** everything runs in **Docker** — Postgres, Redis,
> 5 backend services, and the Next.js frontend. You do **not** install Python,
> Node, Postgres, or Redis on the Mac directly; Docker provides them all.

| Service | Port | What it does |
|---|---|---|
| frontend | 3000 | Next.js app (the UI) |
| agent_service | 8001 | Auth, projects, AI pipeline, WebSockets |
| query_executor | 8002 | Runs SQL against connected DBs |
| schema_crawler | 8003 | Crawls DB schema + AI metadata |
| render_service | 3001 | Chart → PNG |
| export_service | 3002 | .vly / PDF export |
| postgres | 5432 | Platform DB (users, projects, dashboards) |
| redis | 6379 | Cache + locks + pub/sub |

---

## 1. One-time Mac install

Open **Terminal** (`Cmd+Space` → type "Terminal" → Enter).

### 1.1 Homebrew (Mac package manager)
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```
It asks for your Mac password (typing is invisible — normal). At the end it prints
2 `echo … >> ~/.zprofile` + `eval` lines — **run them** so `brew` is on your PATH.
Verify: `brew --version`.

### 1.2 Core tools
```bash
brew install git azure-cli
brew install --cask docker            # Docker Desktop (the engine that runs everything)
brew install --cask visual-studio-code
brew install --cask tableplus         # optional DB GUI (lighter than pgAdmin)
```

**Do I need to download Docker from the website?** No — `brew install --cask docker`
installs the same Docker Desktop. (Downloading the `.dmg` from docker.com is an
equally fine alternative; pick one, not both.)

**Do I need pgAdmin?** No, it's optional — only a GUI to browse the database.
**TablePlus** (above) is lighter and connects to both your local and Azure
Postgres. If you specifically want pgAdmin: `brew install --cask pgadmin4`.

### 1.3 Start Docker Desktop
Open it from Applications (or `open -a Docker`). Wait until the whale 🐳 icon in
the top menu bar is steady (not animating). **Docker must be running** for anything
below to work. Verify: `docker info` (no error = good).

---

## 2. Get the code

```bash
cd ~/Desktop
git clone https://github.com/i-m-alive/Visually.git
cd Visually
```

---

## 3. The `.env` file (secrets — not in git)

`.env` is **git-ignored**, so it is NOT in the clone. It holds AWS keys, Bedrock
model IDs, JWT/encryption keys, and DB settings. Two ways to get it:

**Option A — copy your existing `.env`** from the Windows machine (USB / Drive /
email) into `~/Desktop/Visually/.env`, then make the **Mac edits** below.

**Option B — create it fresh** from the template and fill in values:
```bash
cp .env.example .env
code .env        # opens it in VS Code
```

### Mac-specific edits to `.env`
After copying, change these (Windows values won't work on Mac / in Docker):

```ini
# 1) Point the platform DB at the Docker "postgres" service (NOT localhost) so the
#    containers can reach it. (See §5 for why.)
DATABASE_URL=postgresql+asyncpg://visually:visually@postgres:5432/visually_platform
DATABASE_SYNC_URL=postgresql://visually:visually@postgres:5432/visually_platform

# 2) Redis service name
REDIS_URL=redis://redis:6379

# 3) Fix the Windows path → a Mac path (or delete this line to use the default)
SCHEMA_CACHE_DIR=/app/.schema_cache

# 4) Redshift via Data API (works without VPN) — already added for you
REDSHIFT_USE_DATA_API=true
REDSHIFT_DATA_API_SECRET_ARN=arn:aws:secretsmanager:us-east-1:971110686091:secret:visually/redshift/svc_powerbi-N0v4de
```
Keep everything else (AWS keys, `BEDROCK_*`, `JWT_SECRET`, `ENCRYPTION_KEY`,
`DEV_MODE`) **exactly as-is**. The `ENCRYPTION_KEY` must stay the same to decrypt
any stored DB passwords.

> 🔐 **Security:** never commit `.env`, never paste it in chats/issues. The AWS
> key in it is long-lived — if it's ever exposed, rotate it in the AWS console.

---

## 4. Run it locally (the dev workflow)

From `~/Desktop/Visually`:

```bash
# Start the DB + cache first
docker compose up -d postgres redis

# Create the tables (run once, and after any new migration)
docker compose run --rm agent_service alembic upgrade head

# Start the whole stack (frontend + all backend services)
docker compose up
```

- First run **builds images** → 5–15 min. After that, startup is seconds.
- Docker auto-merges `docker-compose.override.yml`, which mounts your source code
  and runs everything with **hot reload** — edit code in VS Code and it reloads
  live (backend `--reload`, frontend `npm run dev`). This *is* your dev setup.
- Open **http://localhost:3000**.

Shortcuts (see `Makefile`): `make up`, `make migrate`, `make logs`,
`make restart-backend`, `make down`.

> Your local DB starts **empty** → register a new account in the UI. To copy your
> Azure data down instead, see §6.3.

---

## 5. Local DB vs Azure DB (and the localhost gotcha)

You can point the app at **two** databases by changing `DATABASE_URL` /
`DATABASE_SYNC_URL` in `.env` (then `docker compose up -d` to restart):

### 5.1 Local dev DB (default, recommended for development)
The Postgres container. Data lives in the `postgres_data` Docker volume (survives
restarts).
```ini
DATABASE_URL=postgresql+asyncpg://visually:visually@postgres:5432/visually_platform
DATABASE_SYNC_URL=postgresql://visually:visually@postgres:5432/visually_platform
```

### 5.2 Azure DB (the deployed/production data)
```ini
DATABASE_URL=postgresql+asyncpg://visadmin:<PG_FLEX_PASSWORD>@visually-pg-0615.postgres.database.azure.com:5432/visually_platform
DATABASE_SYNC_URL=postgresql://visadmin:<PG_FLEX_PASSWORD>@visually-pg-0615.postgres.database.azure.com:5432/visually_platform
```
`<PG_FLEX_PASSWORD>` is in `azure/secrets.env`. Azure Postgres has a **firewall** —
add your Mac's public IP first (see §8.6). ⚠️ This is **live production data** —
be careful with writes/migrations.

### 5.3 ⚠️ The `postgres` vs `localhost` rule
- **Inside Docker** (the app containers), the DB host is the compose service name
  **`postgres`** — NOT `localhost`. A container's `localhost` is itself.
- **From your Mac** (TablePlus/pgAdmin/psql on the host), the same DB is at
  **`localhost:5432`** (user `visually`, pass `visually`, db `visually_platform`).

So: `DATABASE_URL` uses `@postgres:5432`; your DB GUI uses `localhost:5432`.

### 5.4 Connect a GUI (TablePlus / pgAdmin)
| Field | Local DB | Azure DB |
|---|---|---|
| Host | `localhost` | `visually-pg-0615.postgres.database.azure.com` |
| Port | `5432` | `5432` |
| User | `visually` | `visadmin` |
| Password | `visually` | `<PG_FLEX_PASSWORD>` |
| Database | `visually_platform` | `visually_platform` |
| SSL | off | required (Azure enforces; for psql add `?sslmode=require`) |

---

## 6. Viewing logs (frontend + backend)

### 6.1 Local logs (Docker)
```bash
docker compose logs -f                      # ALL services, live
docker compose logs -f agent_service        # one backend service
docker compose logs -f frontend             # frontend
docker compose logs -f agent_service schema_crawler   # a few at once
docker compose logs --tail 200 query_executor          # last 200 lines
docker compose ps                            # what's running + health
```
If you ran `docker compose up` (foreground), logs already stream in that terminal;
open a new tab with `Cmd+T` for other commands.

### 6.2 Live Azure logs (deployed app)
```bash
az login        # once per session (opens browser)
az containerapp logs show -g visually-rg -n vly-agent-service --follow --tail 100
az containerapp logs show -g visually-rg -n vly-frontend --tail 100
az containerapp logs show -g visually-rg -n vly-schema-crawler --follow
# system (crashes/restarts) instead of app logs:
az containerapp logs show -g visually-rg -n vly-agent-service --type system --tail 100
```

### 6.3 Copy Azure data → local (optional)
With the Azure firewall open for your IP (§8.6):
```bash
# dump from Azure (uses DATABASE_SYNC_URL-style creds)
pg_dump "postgresql://visadmin:<PG_FLEX_PASSWORD>@visually-pg-0615.postgres.database.azure.com:5432/visually_platform?sslmode=require" -Fc -f azure_dump.dump
# restore into the local Docker postgres (must be running)
docker compose up -d postgres
cat azure_dump.dump | docker compose exec -T postgres pg_restore -U visually -d visually_platform --clean --if-exists
```
(`pg_dump`/`pg_restore` come from `brew install libpq && brew link --force libpq`.)

---

## 7. Deployment (Azure)

There are **two** ways. The repo is wired for **CI/CD via GitHub Actions** (the
"pipeline"), which is the normal path.

### 7.1 Pipeline (recommended) — push to `main`
`.github/workflows/deploy.yml` builds all images, pushes to ACR, rolls every
container app, and runs migrations — on **push/merge to `main`**, or manually from
the **Actions** tab → *Run workflow*.

```bash
git checkout -b my-changes
# ... edit code ...
git add -A && git commit -m "describe change"
git push origin my-changes
# open a PR → merge to main → watch the Actions tab
```
Required repo secrets (GitHub → Settings → Secrets → Actions): `AZURE_CREDENTIALS`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, (`AWS_SESSION_TOKEN` only if temp keys).

### 7.2 Manual deploy from the Mac (`deploy.sh`)
Needs Docker running + Azure login. `deploy.sh` is bash and runs natively on Mac.
```bash
az login                                    # pick "Azure for Students"
cd azure
cp secrets.env.example secrets.env          # first time only — fill AWS/JWT/ENCRYPTION/PG_FLEX_PASSWORD
./deploy.sh                                 # all phases, OR a single phase:
./deploy.sh build      # build & push images
./deploy.sh agent      # redeploy agent-service + export-service
./deploy.sh backend    # query-executor + schema-crawler + render
./deploy.sh frontend   # frontend
./deploy.sh migrate    # alembic upgrade head
./deploy.sh urls       # print the public URLs
```
> Student subs block `az acr build`, so images build on your Mac and push to ACR —
> Docker Desktop must be running.

### 7.3 Env vars on deployed apps (without a full redeploy)
```bash
az containerapp update -n vly-agent-service -g visually-rg \
  --set-env-vars REDSHIFT_USE_DATA_API=true
```
⚠️ A later `deploy.sh`/pipeline run re-applies env from `deploy.sh`/secrets, so put
permanent vars there too (already done for `REDSHIFT_USE_DATA_API` + the secret ARN).

---

## 8. Troubleshooting

### 8.1 "Cannot connect to the Docker daemon"
Docker Desktop isn't running. Open it (`open -a Docker`), wait for the steady whale.

### 8.2 Port already in use (5432/3000/8001…)
Something else is using the port. Find & stop it, or stop the conflicting app:
```bash
lsof -i :5432        # see what holds the port
docker compose down  # stop our stack
```

### 8.3 Azure CLI login expired / "az: command not found"
```bash
az login                          # re-auth (opens browser)
az account show                   # confirm the right subscription
az account set --subscription "Azure for Students"
brew install azure-cli            # if the command is missing
```

### 8.4 AWS / Bedrock errors (chat, charts, schema match fail)
The AWS key in `.env` (local) or the ACA secrets (deployed) is invalid/expired.
- Local: check `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` in `.env`. Long-lived
  `AKIA…` keys don't expire; temporary `ASIA…` keys + `AWS_SESSION_TOKEN` do —
  clear the session token line if using permanent keys.
- Deployed: update the GitHub secret(s) and re-run the pipeline, or
  `az containerapp update … --set-env-vars …` then restart.

### 8.5 Redshift "connection time out"
The Serverless workgroup is private (VPC-only). Ensure `REDSHIFT_USE_DATA_API=true`
is set so it goes through the public Data API (already in your `.env` and on the
deployed agent/query/crawler apps). The IAM key needs `redshift-data:*`,
`redshift-serverless:GetCredentials`, `secretsmanager:GetSecretValue` on the ARN.

### 8.6 Azure Postgres firewall (connecting from the Mac)
```bash
MYIP=$(curl -s https://api.ipify.org)
az postgres flexible-server firewall-rule create -g visually-rg -n visually-pg-0615 \
  --rule-name mac-$(echo $MYIP | tr . -) --start-ip-address $MYIP --end-ip-address $MYIP
# remove it when done:
az postgres flexible-server firewall-rule delete -g visually-rg -n visually-pg-0615 --rule-name <name> --yes
```

### 8.7 Migrations / schema out of date
```bash
docker compose run --rm agent_service alembic upgrade head      # local
./deploy.sh migrate                                             # azure
```

### 8.8 Rebuild after dependency changes / weird state
```bash
docker compose build --no-cache      # rebuild images
docker compose down                  # stop
docker compose up -d                 # start fresh
```

### 8.9 Reset the local database (wipe & recreate)
```bash
docker compose down
docker volume rm visually_postgres_data     # name may be <folder>_postgres_data; check: docker volume ls
docker compose up -d postgres redis
docker compose run --rm agent_service alembic upgrade head
```

---

## 9. Command cheat-sheet

```bash
# ── Local dev ──────────────────────────────────────────────
docker compose up -d                 # start everything (background)
docker compose up                    # start in foreground (see logs)
docker compose down                  # stop everything
docker compose ps                    # status
docker compose logs -f <service>     # tail a service's logs
docker compose restart agent_service # restart one service
docker compose run --rm agent_service alembic upgrade head   # migrate
docker compose build --no-cache      # rebuild images

# ── Azure ──────────────────────────────────────────────────
az login
az containerapp logs show -g visually-rg -n vly-agent-service --follow --tail 100
az containerapp list -g visually-rg --query "[].name" -o tsv
cd azure && ./deploy.sh urls         # print live URLs
cd azure && ./deploy.sh agent backend frontend   # redeploy app code

# ── Git ────────────────────────────────────────────────────
git pull                             # get latest
git checkout -b feature/x            # new branch
git add -A && git commit -m "msg" && git push origin feature/x
```

---

## 10. Quick mental model

- **Develop locally** → `docker compose up`, edit code (hot-reloads), DB = local
  Postgres container, browse at `localhost:3000`.
- **Inspect prod data** → point `DATABASE_URL` at Azure (open firewall first), or
  use TablePlus against the Azure host.
- **Ship** → push to `main` (pipeline) or `./deploy.sh` from the Mac.
- **Debug prod** → `az containerapp logs show … --follow`.
