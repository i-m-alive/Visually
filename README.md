# Visually — AI-Powered Analytics Platform

Phase 1: Core Query & Visualization

## Quick Start

```bash
# 1. Copy and configure environment
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY and ENCRYPTION_KEY

# 2. Generate an encryption key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Paste the output as ENCRYPTION_KEY in .env

# 3. Start infrastructure
docker compose up -d postgres redis

# 4. Run migrations
docker compose run --rm agent_service alembic upgrade head

# 5. Start all services
docker compose up
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| Frontend | 3000 | Next.js app |
| Agent Service | 8001 | Auth, Projects, AI Pipeline |
| Query Executor | 8002 | SQL execution engine |
| Schema Crawler | 8003 | Database schema analysis |
| Render Service | 3001 | Chart-to-PNG renderer |

## Pipeline

```
User text → Intent Classifier (haiku) 
         → Schema Fetcher 
         → Query Agent (sonnet) 
         → SQL Executor 
         → Chart Renderer (Node.js) 
         → Validator Agent (sonnet) 
         → WebSocket broadcast → Frontend
```

## Development

```bash
make up          # Start all services
make migrate     # Run DB migrations  
make logs        # Tail service logs
make restart-backend  # Restart Python services
```

## Phase 1 Acceptance Criteria

- [x] User registration & login with JWT
- [x] Project + connection management
- [x] AI schema crawl (PostgreSQL + MySQL)
- [x] Natural language → SQL → chart pipeline
- [x] WebSocket real-time pipeline streaming
- [x] Validation with one automatic retry
- [x] Schema Explorer UI
- [x] Agent Reasoning Drawer
