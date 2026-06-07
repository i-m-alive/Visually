#!/bin/bash
set -e

[ ! -f .env ] && cp .env.example .env && echo "Created .env from .env.example — edit it with your ANTHROPIC_API_KEY"

echo ""
echo "Setup steps:"
echo "  1. Edit .env and set ANTHROPIC_API_KEY"
echo "  2. docker compose up -d postgres redis"
echo "  3. docker compose run --rm agent_service alembic upgrade head"
echo "  4. docker compose up"
