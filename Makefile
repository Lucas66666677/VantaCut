.PHONY: up down logs migrate shell-backend shell-frontend clean

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f backend worker frontend

migrate:
	docker compose run --rm backend alembic upgrade head

shell-backend:
	docker compose exec backend bash

shell-frontend:
	docker compose exec frontend sh

clean:
	docker compose down -v

