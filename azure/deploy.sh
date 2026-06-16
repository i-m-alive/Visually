#!/usr/bin/env bash
# ============================================================================
# Visually -> Azure Container Apps deployment (Azure for Students)
#
# Usage:
#   ./deploy.sh            # run every phase in order
#   ./deploy.sh <phase>    # run a single phase, e.g. ./deploy.sh build
#
# Phases: prereqs acr build env storage pgflex data backend agent frontend migrate urls
#
# NOTE: This subscription already has the one allowed Container Apps
# environment (navispark-env). We deploy INTO it, prefixing every app with
# APP_PREFIX so names don't collide. Our apps live in $RG; the environment
# lives in $ACA_ENV_RG.
#
# Re-running: infra phases (acr/storage) are idempotent. Container-app phases
# use "create"; to redeploy an app, delete it first (az containerapp delete)
# or change TAG and use "az containerapp update --image".
# ============================================================================
set -euo pipefail

# Git Bash (MSYS) mangles args/paths that start with "/" (e.g. Azure resource
# IDs). Disable that conversion or every --environment <id> breaks.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

# --- locate folders, load config -------------------------------------------
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"          # repo root (build contexts live here)
cd "$ROOT"

[ -f "$HERE/config.env" ]  || { echo "ERROR: azure/config.env not found"; exit 1; }
source "$HERE/config.env"
if [ -f "$HERE/secrets.env" ]; then
  source "$HERE/secrets.env"
else
  echo "ERROR: azure/secrets.env not found."
  echo "       cp azure/secrets.env.example azure/secrets.env  and fill it in."
  exit 1
fi

PHASE="${1:-all}"
run() { [ "$PHASE" = "all" ] || [ "$PHASE" = "$1" ]; }
log() { echo ""; echo "==> $*"; }
# With MSYS_NO_PATHCONV=1, file-path args aren't translated, so convert YAML
# file paths to native Windows form for az.exe.
winpath() { cygpath -w "$1"; }

# --- prefixed app names ------------------------------------------------------
P="$APP_PREFIX"
APP_PG="${P}postgres";        APP_REDIS="${P}redis"
APP_QUERY="${P}query-executor"; APP_SCHEMA="${P}schema-crawler"; APP_RENDER="${P}render-service"
APP_AGENT="${P}agent-service";  APP_EXPORT="${P}export-service";  APP_FRONT="${P}frontend"
APP_MIGRATE="${P}migrate"

# --- derived value helpers (env lives in ACA_ENV_RG) ------------------------
acr_login()  { az acr show -n "$ACR_NAME" -g "$RG" --query loginServer -o tsv; }
env_domain() { az containerapp env show -n "$ACA_ENV" -g "$ACA_ENV_RG" --query properties.defaultDomain -o tsv; }
env_id()     { az containerapp env show -n "$ACA_ENV" -g "$ACA_ENV_RG" --query id -o tsv; }
agent_fqdn() { az containerapp show -n "$APP_AGENT" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv; }

# ============================================================================
# PHASE: prereqs  — subscription, providers, CLI extension
# ============================================================================
if run prereqs; then
  log "Prereqs: subscription, providers, containerapp extension"
  [ -n "${SUBSCRIPTION_ID:-}" ] && az account set --subscription "$SUBSCRIPTION_ID"
  az account show --query "{sub:name, id:id}" -o table

  az extension add --name containerapp --upgrade --only-show-errors

  for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry Microsoft.Storage; do
    echo "  registering provider $ns ..."
    az provider register --namespace "$ns" --wait
  done
fi

# ============================================================================
# PHASE: acr  — resource group + container registry
# ============================================================================
if run acr; then
  log "Resource group + ACR"
  az group create -n "$RG" -l "$LOCATION" -o none
  if ! az acr show -n "$ACR_NAME" -g "$RG" -o none 2>/dev/null; then
    az acr create -n "$ACR_NAME" -g "$RG" --sku Basic --admin-enabled true -o none
  fi
  az acr update -n "$ACR_NAME" --admin-enabled true -o none
  echo "  ACR login server: $(acr_login)"
fi

# ============================================================================
# PHASE: build  — build & push the 5 backend/render images (local docker)
# ============================================================================
if run build; then
  # ACR Tasks (az acr build) is disabled on Azure for Students, so build
  # locally with Docker and push to ACR.
  log "Building images locally and pushing to ACR"
  ACR_LOGIN="$(acr_login)"
  az acr login -n "$ACR_NAME"

  build_push() { # <image> <dockerfile> <context>
    echo "  --- building $1 ---"
    docker build -t "$ACR_LOGIN/$1:$TAG" -f "$2" "$3"
    docker push "$ACR_LOGIN/$1:$TAG"
  }
  build_push agent-service   backend/agent_service/Dockerfile   backend
  build_push query-executor  backend/query_executor/Dockerfile  backend
  build_push schema-crawler  backend/schema_crawler/Dockerfile  backend
  build_push export-service  backend/export_service/Dockerfile  backend
  build_push render-service  backend/render_service/Dockerfile  backend/render_service
  echo "  (frontend image is built later, in the 'frontend' phase)"
fi

# ============================================================================
# PHASE: env  — reuse the existing (shared) Container Apps environment
# ============================================================================
if run env; then
  log "Using shared Container Apps environment: $ACA_ENV (rg: $ACA_ENV_RG)"
  echo "  env id    : $(env_id)"
  echo "  env domain: $(env_domain)"
fi

# ============================================================================
# PHASE: storage  — storage account + 2 Azure Files shares, registered in env
# ============================================================================
if run storage; then
  log "Storage account + Azure Files shares"
  if ! az storage account show -n "$STORAGE_ACCT" -g "$RG" -o none 2>/dev/null; then
    az storage account create -n "$STORAGE_ACCT" -g "$RG" -l "$LOCATION" \
      --sku Standard_LRS --kind StorageV2 -o none
  fi
  STORAGE_KEY="$(az storage account keys list -n "$STORAGE_ACCT" -g "$RG" --query '[0].value' -o tsv)"

  for share in "$PG_SHARE" "$UPLOADS_SHARE"; do
    az storage share-rm create --storage-account "$STORAGE_ACCT" -g "$RG" \
      --name "$share" --quota 5 -o none 2>/dev/null || true
  done

  az containerapp env storage set -g "$ACA_ENV_RG" -n "$ACA_ENV" --storage-name "$PG_STORAGE_NAME" \
    --azure-file-account-name "$STORAGE_ACCT" --azure-file-account-key "$STORAGE_KEY" \
    --azure-file-share-name "$PG_SHARE" --access-mode ReadWrite -o none
  az containerapp env storage set -g "$ACA_ENV_RG" -n "$ACA_ENV" --storage-name "$UPLOADS_STORAGE_NAME" \
    --azure-file-account-name "$STORAGE_ACCT" --azure-file-account-key "$STORAGE_KEY" \
    --azure-file-share-name "$UPLOADS_SHARE" --access-mode ReadWrite -o none
  echo "  shares registered in env as $PG_STORAGE_NAME, $UPLOADS_STORAGE_NAME."
fi

# ----------------------------------------------------------------------------
# Shared values used by the deploy phases below
# ----------------------------------------------------------------------------
if run data || run backend || run agent || run frontend || run migrate || run urls; then
  ACR_LOGIN="$(acr_login)"
  DOMAIN="$(env_domain)"
  ENV_ID="$(env_id)"
  ACR_USER="$(az acr credential show -n "$ACR_NAME" --query username -o tsv)"
  ACR_PASS="$(az acr credential show -n "$ACR_NAME" --query 'passwords[0].value' -o tsv)"

  # Internal service URLs (deterministic from app name + env domain).
  # MUST be https:// — ACA internal ingress (allowInsecure=false) answers plain
  # HTTP with a 301 redirect to HTTPS. Our httpx clients don't follow it, and a
  # 301 would also strip the POST body, so http:// breaks every internal call
  # (query-executor "Executor 301", schema crawl, render, export).
  QUERY_URL="https://$APP_QUERY.internal.$DOMAIN"
  SCHEMA_URL="https://$APP_SCHEMA.internal.$DOMAIN"
  RENDER_URL="https://$APP_RENDER.internal.$DOMAIN"
  EXPORT_URL="https://$APP_EXPORT.internal.$DOMAIN"

  # Platform DB = Azure managed Postgres (Flexible Server). SSL enforcement is
  # turned OFF on the server so the plain asyncpg/psycopg2 URLs work as-is.
  DB_ASYNC="postgresql+asyncpg://${PG_FLEX_USER}:${PG_FLEX_PASSWORD}@${PG_FLEX_HOST}:5432/${PG_DB_NAME}"
  DB_SYNC="postgresql://${PG_FLEX_USER}:${PG_FLEX_PASSWORD}@${PG_FLEX_HOST}:5432/${PG_DB_NAME}"
  # Redis runs as a container with internal TCP ingress. IMPORTANT: use the short
  # app name, NOT the full .internal.<domain> FQDN. ACA routes HTTP internal traffic
  # via the FQDN fine, but TCP internal traffic over the FQDN times out — the
  # intra-environment short name resolves and connects correctly for TCP.
  REDIS_URL_VAL="redis://$APP_REDIS:6379"

  REG=( --registry-server "$ACR_LOGIN" --registry-username "$ACR_USER" --registry-password "$ACR_PASS" )

  # Common secrets (defined on every python service; harmless if unreferenced)
  SEC=( --secrets
        aws-access-key="$AWS_ACCESS_KEY_ID"
        aws-secret-key="$AWS_SECRET_ACCESS_KEY"
        enc-key="$ENCRYPTION_KEY"
        jwt-secret="$JWT_SECRET" )
  AWS_ENV=( AWS_REGION="$AWS_REGION"
            AWS_ACCESS_KEY_ID=secretref:aws-access-key
            AWS_SECRET_ACCESS_KEY=secretref:aws-secret-key )
  if [ -n "${AWS_SESSION_TOKEN:-}" ]; then
    SEC+=( aws-session-token="$AWS_SESSION_TOKEN" )
    AWS_ENV+=( AWS_SESSION_TOKEN=secretref:aws-session-token )
  fi
  BEDROCK_ENV=( BEDROCK_SONNET_MODEL_ID="$BEDROCK_SONNET_MODEL_ID"
                BEDROCK_HAIKU_MODEL_ID="$BEDROCK_HAIKU_MODEL_ID"
                BEDROCK_VISION_MODEL_ID="$BEDROCK_VISION_MODEL_ID"
                BEDROCK_MAX_TOKENS="$BEDROCK_MAX_TOKENS"
                BEDROCK_TEMPERATURE="$BEDROCK_TEMPERATURE" )
fi

# ============================================================================
# PHASE: pgflex  — managed Postgres (Flexible Server). Postgres can't run on
# Azure Files (SMB blocks initdb's chmod), so the platform DB is managed.
# ============================================================================
if run pgflex; then
  log "Provisioning managed Postgres: $PG_FLEX_NAME"
  az provider register --namespace Microsoft.DBforPostgreSQL --wait
  if ! az postgres flexible-server show -g "$RG" -n "$PG_FLEX_NAME" -o none 2>/dev/null; then
    az postgres flexible-server create \
      --name "$PG_FLEX_NAME" -g "$RG" -l "$LOCATION" \
      --admin-user "$PG_FLEX_USER" --admin-password "$PG_FLEX_PASSWORD" \
      --sku-name Standard_B1ms --tier Burstable \
      --storage-size 32 --version 15 \
      --public-access 0.0.0.0 --yes -o none
  fi
  # Allow connections from Azure resources (the ACA env outbound).
  az postgres flexible-server firewall-rule create -g "$RG" --name "$PG_FLEX_NAME" \
    --rule-name AllowAzure --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 -o none 2>/dev/null || true
  # Drop SSL enforcement so the plain driver URLs connect without extra args.
  az postgres flexible-server parameter set -g "$RG" -s "$PG_FLEX_NAME" \
    --name require_secure_transport --value OFF -o none
  az postgres flexible-server db create -g "$RG" -s "$PG_FLEX_NAME" \
    --database-name "$PG_DB_NAME" -o none 2>/dev/null || true
  echo "  managed Postgres ready at $PG_FLEX_HOST"
fi

# ============================================================================
# PHASE: data  — redis (internal TCP). Postgres is managed (see pgflex).
# ============================================================================
if run data; then
  log "Deploying $APP_REDIS"
  az containerapp create -n "$APP_REDIS" -g "$RG" --environment "$ENV_ID" \
    --image redis:7-alpine \
    --ingress internal --transport tcp --target-port 6379 --exposed-port 6379 \
    --min-replicas 1 --max-replicas 1 --cpu 0.25 --memory 0.5Gi -o none
fi

# ============================================================================
# PHASE: backend  — query_executor, schema_crawler, render_service
# ============================================================================
if run backend; then
  log "Deploying $APP_QUERY, $APP_SCHEMA, $APP_RENDER"

  az containerapp create -n "$APP_QUERY" -g "$RG" --environment "$ENV_ID" \
    --image "$ACR_LOGIN/query-executor:$TAG" "${REG[@]}" \
    --ingress internal --transport http --target-port 8002 \
    --min-replicas 1 --max-replicas 3 --cpu 1.0 --memory 2.0Gi \
    "${SEC[@]}" \
    --env-vars DATABASE_URL="$DB_ASYNC" ENCRYPTION_KEY=secretref:enc-key "${AWS_ENV[@]}" -o none

  az containerapp create -n "$APP_SCHEMA" -g "$RG" --environment "$ENV_ID" \
    --image "$ACR_LOGIN/schema-crawler:$TAG" "${REG[@]}" \
    --ingress internal --transport http --target-port 8003 \
    --min-replicas 0 --max-replicas 1 --cpu 0.5 --memory 1.0Gi \
    "${SEC[@]}" \
    --env-vars DATABASE_URL="$DB_ASYNC" REDIS_URL="$REDIS_URL_VAL" \
               ENCRYPTION_KEY=secretref:enc-key "${AWS_ENV[@]}" "${BEDROCK_ENV[@]}" -o none

  az containerapp create -n "$APP_RENDER" -g "$RG" --environment "$ENV_ID" \
    --image "$ACR_LOGIN/render-service:$TAG" "${REG[@]}" \
    --ingress internal --transport http --target-port 3001 \
    --min-replicas 0 --max-replicas 1 --cpu 0.25 --memory 0.5Gi \
    --env-vars PORT=3001 -o none
fi

# ============================================================================
# PHASE: agent  — agent-service (external) + export-service (internal)
# ============================================================================
if run agent; then
  log "Deploying $APP_AGENT (public) + $APP_EXPORT"

  # agent-service: external ingress + uploads volume -> YAML
  cat > "$HERE/.agent.yaml" <<YAML
properties:
  managedEnvironmentId: $ENV_ID
  configuration:
    activeRevisionsMode: Single
    registries:
      - server: "$ACR_LOGIN"
        username: "$ACR_USER"
        passwordSecretRef: acr-pass
    secrets:
      - name: acr-pass
        value: "$ACR_PASS"
      - name: aws-access-key
        value: "$AWS_ACCESS_KEY_ID"
      - name: aws-secret-key
        value: "$AWS_SECRET_ACCESS_KEY"
      - name: aws-session-token
        value: "${AWS_SESSION_TOKEN:-}"
      - name: enc-key
        value: "$ENCRYPTION_KEY"
      - name: jwt-secret
        value: "$JWT_SECRET"
  template:
    containers:
      - name: agent-service
        image: $ACR_LOGIN/agent-service:$TAG
        command:
          - uvicorn
        args:
          - agent_service.main:app
          - "--host"
          - 0.0.0.0
          - "--port"
          - "8001"
        resources:
          cpu: 1.0
          memory: 2.0Gi
        env:
          - name: DATABASE_URL
            value: "$DB_ASYNC"
          - name: DATABASE_SYNC_URL
            value: "$DB_SYNC"
          - name: REDIS_URL
            value: "$REDIS_URL_VAL"
          - name: AWS_REGION
            value: "$AWS_REGION"
          - name: AWS_ACCESS_KEY_ID
            secretRef: aws-access-key
          - name: AWS_SECRET_ACCESS_KEY
            secretRef: aws-secret-key
          - name: AWS_SESSION_TOKEN
            secretRef: aws-session-token
          - name: JWT_SECRET
            secretRef: jwt-secret
          - name: ENCRYPTION_KEY
            secretRef: enc-key
          - name: BEDROCK_SONNET_MODEL_ID
            value: "$BEDROCK_SONNET_MODEL_ID"
          - name: BEDROCK_HAIKU_MODEL_ID
            value: "$BEDROCK_HAIKU_MODEL_ID"
          - name: BEDROCK_VISION_MODEL_ID
            value: "$BEDROCK_VISION_MODEL_ID"
          - name: BEDROCK_MAX_TOKENS
            value: "$BEDROCK_MAX_TOKENS"
          - name: BEDROCK_TEMPERATURE
            value: "$BEDROCK_TEMPERATURE"
          - name: QUERY_EXECUTOR_URL
            value: "$QUERY_URL"
          - name: SCHEMA_CRAWLER_URL
            value: "$SCHEMA_URL"
          - name: RENDER_SERVICE_URL
            value: "$RENDER_URL"
          - name: EXPORT_SERVICE_URL
            value: "$EXPORT_URL"
          - name: AGENT_SERVICE_URL
            value: "PLACEHOLDER_SET_AFTER_CREATE"
          - name: LOCAL_UPLOADS_DIR
            value: "/app/uploads"
          - name: S3_ENDPOINT_URL
            value: ""
          - name: DEV_MODE
            value: "false"
        volumeMounts:
          - volumeName: uploads
            mountPath: /app/uploads
    scale:
      minReplicas: 1
      maxReplicas: 3
    volumes:
      - name: uploads
        storageType: AzureFile
        storageName: $UPLOADS_STORAGE_NAME
YAML
  az containerapp create -n "$APP_AGENT" -g "$RG" --environment "$ENV_ID" --yaml "$(winpath "$HERE/.agent.yaml")" -o none
  rm -f "$HERE/.agent.yaml"
  # external ingress via CLI (extension rejects ingress in YAML)
  az containerapp ingress enable -n "$APP_AGENT" -g "$RG" \
    --type external --transport auto --target-port 8001 -o none

  AGENT_PUBLIC="https://$(agent_fqdn)"
  echo "  agent-service public URL: $AGENT_PUBLIC"
  # Now that we know our own public FQDN, set the self-referential URLs.
  az containerapp update -n "$APP_AGENT" -g "$RG" \
    --set-env-vars AGENT_SERVICE_URL="$AGENT_PUBLIC" AGENT_SERVICE_PUBLIC_URL="$AGENT_PUBLIC" -o none

  # export-service: internal ingress + uploads volume + needs agent public URL
  cat > "$HERE/.export.yaml" <<YAML
properties:
  managedEnvironmentId: $ENV_ID
  configuration:
    activeRevisionsMode: Single
    registries:
      - server: "$ACR_LOGIN"
        username: "$ACR_USER"
        passwordSecretRef: acr-pass
    secrets:
      - name: acr-pass
        value: "$ACR_PASS"
  template:
    containers:
      - name: export-service
        image: $ACR_LOGIN/export-service:$TAG
        resources:
          cpu: 1.0
          memory: 2.0Gi
        env:
          - name: EXPORT_SERVICE_URL
            value: "$EXPORT_URL"
          - name: AGENT_SERVICE_URL
            value: "$AGENT_PUBLIC"
          - name: LOCAL_UPLOADS_DIR
            value: "/app/uploads"
          - name: S3_ENDPOINT_URL
            value: ""
        volumeMounts:
          - volumeName: uploads
            mountPath: /app/uploads
    scale:
      minReplicas: 0
      maxReplicas: 1
    volumes:
      - name: uploads
        storageType: AzureFile
        storageName: $UPLOADS_STORAGE_NAME
YAML
  az containerapp create -n "$APP_EXPORT" -g "$RG" --environment "$ENV_ID" --yaml "$(winpath "$HERE/.export.yaml")" -o none
  rm -f "$HERE/.export.yaml"
  az containerapp ingress enable -n "$APP_EXPORT" -g "$RG" \
    --type internal --transport http --target-port 3002 -o none
fi

# ============================================================================
# PHASE: frontend  — build image with public agent URL baked in, then deploy
# ============================================================================
if run frontend; then
  log "Building + deploying $APP_FRONT"
  AGENT_PUBLIC="https://$(agent_fqdn)"
  WS_PUBLIC="wss://$(agent_fqdn)"
  echo "  baking NEXT_PUBLIC_API_URL=$AGENT_PUBLIC"

  az acr login -n "$ACR_NAME"
  docker build -t "$ACR_LOGIN/frontend:$TAG" \
    --build-arg NEXT_PUBLIC_API_URL="$AGENT_PUBLIC" \
    --build-arg NEXT_PUBLIC_WS_URL="$WS_PUBLIC" \
    -f frontend/Dockerfile frontend
  docker push "$ACR_LOGIN/frontend:$TAG"

  az containerapp create -n "$APP_FRONT" -g "$RG" --environment "$ENV_ID" \
    --image "$ACR_LOGIN/frontend:$TAG" "${REG[@]}" \
    --ingress external --transport auto --target-port 3000 \
    --min-replicas 0 --max-replicas 2 --cpu 0.5 --memory 1.0Gi -o none

  echo "  frontend URL: https://$(az containerapp show -n "$APP_FRONT" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)"
fi

# ============================================================================
# PHASE: migrate  — run alembic once via a Container Apps Job
# ============================================================================
if run migrate; then
  log "Running database migrations (alembic upgrade head)"
  # Note: --command/--args must come last and avoid leading-dash tokens
  # (argparse treats "-c" as a flag). alembic is on PATH with WORKDIR /app.
  az containerapp job delete -n "$APP_MIGRATE" -g "$RG" --yes -o none 2>/dev/null || true
  az containerapp job create -n "$APP_MIGRATE" -g "$RG" --environment "$ENV_ID" \
    --image "$ACR_LOGIN/agent-service:$TAG" "${REG[@]}" \
    --trigger-type Manual --replica-timeout 1800 --replica-retry-limit 1 \
    --cpu 0.5 --memory 1.0Gi \
    --secrets enc-key="$ENCRYPTION_KEY" \
    --env-vars DATABASE_SYNC_URL="$DB_SYNC" DATABASE_URL="$DB_ASYNC" ENCRYPTION_KEY=secretref:enc-key \
    --command "alembic" --args "upgrade" "head" \
    -o none
  az containerapp job start -n "$APP_MIGRATE" -g "$RG" -o none
  echo "  migration job started. Check status with:"
  echo "    az containerapp job execution list -n $APP_MIGRATE -g $RG -o table"
fi

# ============================================================================
# PHASE: urls  — print the public endpoints
# ============================================================================
if run urls; then
  log "Deployment endpoints"
  echo "  Frontend : https://$(az containerapp show -n "$APP_FRONT" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null)"
  echo "  API      : https://$(az containerapp show -n "$APP_AGENT" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null)"
fi

log "Done (phase: $PHASE)."
