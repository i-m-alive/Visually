.PHONY: up down migrate logs restart-backend

up:
	docker compose up -d

down:
	docker compose down

migrate:
	docker compose run --rm agent_service alembic upgrade head

logs:
	docker compose logs -f agent_service schema_crawler

restart-backend:
	docker compose restart agent_service query_executor schema_crawler
