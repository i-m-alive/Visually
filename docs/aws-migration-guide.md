# Visually → AWS Migration Guide
> Demo deployment — not production. Goal is a working environment clients can play with.

---

## What we're running

8 services total: Next.js frontend, 4 Python FastAPI backends, 1 Node.js render service, 1 Python+Puppeteer export service, PostgreSQL 15, Redis 7. Currently on Azure Container Apps (student sub). AI inference already goes to AWS Bedrock — we're just moving the containers alongside it.

---

## AWS Services Needed

| Service | Purpose |
|---|---|
| **ECR** | Stores the 6 Docker images |
| **ECS Fargate** | Runs the containers — no EC2 to manage |
| **ALB** | Routes HTTPS traffic to frontend + API |
| **RDS PostgreSQL 15** | Platform database |
| **ElastiCache Redis** | Sessions and caching |
| **S3** | File uploads, exports |
| **Secrets Manager** | DB passwords, JWT secret, encryption keys |
| **VPC** | Private network — internal services stay off the internet |
| **ACM** | Free TLS cert on the load balancer |
| **CloudWatch** | Container logs |

Bedrock is already configured — just need to fix auth (see section 5).

---

## Architecture (simplified)

```
Internet → ALB (HTTPS) → frontend :3000
                       → agent-service :8001
                              │
                    ┌─────────┼─────────┐
               query-executor  schema-crawler  render-service  export-service
                              │
                    RDS Postgres · ElastiCache Redis · S3
                              │
                         AWS Bedrock (already in use)
```

Frontend and agent-service are public. Everything else is internal — no direct internet access.

---

## Setup order

### 1. AWS Account + IAM
- Create account, attach billing method
- Create two IAM roles:
  - `visually-ecs-task-role` — needs `bedrock:InvokeModel`, `s3:*`, `secretsmanager:GetSecretValue`
  - `visually-ecs-execution-role` — needs ECR pull + CloudWatch logs
- Set up GitHub OIDC so CI/CD can deploy without long-lived keys
- Enable Bedrock model access for Claude Sonnet + Haiku in your chosen region

### 2. Networking (VPC)
- 1 VPC, 2 public subnets, 2 private subnets (multi-AZ is an ALB requirement)
- Internet Gateway for public subnets, NAT Gateway for private subnets
- Security groups: ALB → public ECS services → private ECS services → RDS/Redis

### 3. ECR — image registry
- 6 repositories: `visually/frontend`, `visually/agent-service`, `visually/query-executor`, `visually/schema-crawler`, `visually/render-service`, `visually/export-service`
- These replace `visuallyacr0615.azurecr.io` from Azure

### 4. Data layer
- **RDS** `db.t3.micro` PostgreSQL 15 in private subnet — run `alembic upgrade head` after provisioning
- **ElastiCache** `cache.t3.micro` Redis 7 in private subnet
- **S3** bucket (`visually-uploads-demo`) with all public access blocked

### 5. Secrets Manager
Store these — ECS injects them at container startup:

| Secret key | What it holds |
|---|---|
| `visually/database-url` | PostgreSQL asyncpg connection string |
| `visually/redis-url` | ElastiCache endpoint |
| `visually/jwt-secret` | JWT signing key |
| `visually/encryption-key` | Fernet key for DB password encryption |

Remove `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` from your task definitions entirely — the ECS task role handles Bedrock auth automatically.

### 6. ECS Cluster + services

Fargate sizing for demo (scale down to save cost):

| Service | vCPU | Memory |
|---|---|---|
| frontend | 0.25 | 512 MB |
| agent-service | 0.5 | 1 GB |
| query-executor | 0.5 | 1 GB |
| schema-crawler | 0.25 | 512 MB |
| render-service | 0.25 | 512 MB |
| export-service | 0.5 | 1 GB |

Use **ECS Service Connect** or **Cloud Map** so services can find each other by name (e.g. `http://render-service:3001` → resolved inside VPC).

### 7. ALB + TLS
- ALB in public subnets, ACM cert on the listener
- Two routing rules: default → frontend, `/api/*` and `/agent/*` → agent-service
- WebSocket (`/agent/stream`) works out of the box on ALB HTTP/1.1

### 8. CI/CD — update GitHub Actions

Only three things change in `.github/workflows/deploy.yml`:

1. Replace `azure/login` with `aws-actions/configure-aws-credentials` (OIDC)
2. Replace ACR push with ECR push (`aws-actions/amazon-ecr-login`)
3. Replace ACA update commands with `aws ecs update-service --force-new-deployment`

GitHub secrets needed: `AWS_ROLE_ARN`, `AWS_REGION`, `ECR_REGISTRY`, `ECS_CLUSTER`

> `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_WS_URL` are baked into the Next.js build — pass them as Docker build args in the workflow, not Secrets Manager.

---

## Rough cost (demo level)

Around **$50–70/month** at demo scale with minimal traffic. Main cost drivers are the ALB (~$18) and NAT Gateway (~$32). RDS and ElastiCache are Free Tier eligible for the first 12 months.

---

## Checklist

- [ ] AWS account created, MFA on root
- [ ] IAM roles: `ecs-task-role`, `ecs-execution-role`, `github-actions-role` (OIDC)
- [ ] Bedrock model access enabled (Sonnet + Haiku)
- [ ] VPC + subnets + security groups
- [ ] 6 ECR repositories
- [ ] RDS `db.t3.micro` + Alembic migrations run
- [ ] ElastiCache `cache.t3.micro`
- [ ] S3 bucket
- [ ] Secrets Manager entries created
- [ ] ECS cluster + 6 Fargate services deployed
- [ ] ALB + ACM cert + routing rules
- [ ] `deploy.yml` updated to target AWS
- [ ] Full CI/CD run passes
- [ ] App accessible via ALB URL — smoke test login, AI query, export
