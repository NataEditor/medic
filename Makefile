.PHONY: help install lint format test run migrate revision superadmin demo seed up down logs clean

# Python из виртуального окружения
PY := .venv/Scripts/python
UV := py -3.12 -m uv

help: ## Показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Установить зависимости (uv sync)
	$(UV) sync --dev

lint: ## Проверить код линтером Ruff
	$(PY) -m ruff check .

format: ## Отформатировать код (Ruff)
	$(PY) -m ruff format .

test: ## Запустить тесты unittest
	$(PY) -m unittest discover -s tests -v

run: ## Запустить сервер разработки
	$(PY) -m uvicorn app.main:app --reload --port 8000

migrate: ## Применить миграции Alembic
	$(PY) -m alembic upgrade head

revision: ## Создать миграцию (использование: make revision m="сообщение")
	$(PY) -m alembic revision --autogenerate -m "$(m)"

superadmin: ## Создать супер-админа (интерактивно)
	$(PY) -m app.cli create-superadmin

demo: ## Наполнить базу демо-данными
	$(PY) -m app.cli seed-demo

users: ## Список пользователей
	$(PY) -m app.cli list-users

up: ## Поднять весь стек в Docker
	docker compose up -d --build

down: ## Остановить стек Docker
	docker compose down

logs: ## Логи Docker-сервисов
	docker compose logs -f

clean: ## Удалить кэши Python
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
