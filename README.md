# MEDIC — Медицинская Информационная Система (МИС / EMR)

Комплексная система автоматизации процессов для сети медицинских клиник с многоролевым доступом, электронной медицинской картой (ЭМК), расписанием приёмов, учётом выполнения процедур и финансовой отчётностью для бухгалтерии.

---

## 🛠 Технологический стек

* **Бэкенд:** Python 3.12, FastAPI, Pydantic v2
* **База данных:** PostgreSQL 16, SQLAlchemy 2.0 (asyncio + asyncpg), Alembic
* **Кэш и брокер:** Redis 7, RabbitMQ 3.13, Celery, Flower
* **Обработка файлов:** Pillow (оптимизация сканов, создание превью)
* **Качество кода:** uv, Ruff, standard `unittest`
* **Инфраструктура:** Docker, Docker Compose, Nginx, GitHub Actions, Loguru

---

## 👥 Ролевая модель доступа (RBAC)

| Роль | Ключевые возможности |
|---|---|
| **Пациент** (`patient`) | Доступ только к своей карте, просмотр назначений, скачивание результатов анализов, запись к врачу |
| **Врач** (`doctor`) | Просмотр расписания, внесение осмотра в ЭМК (МКБ-10, анамнез), создание/редактирование/отмена назначений |
| **Главный врач / Админ** (`head_doctor`) | Сквозной доступ по всем филиалам сети, добавление клиник/врачей, прайс-лист, сводные финансовые отчёты |
| **Медицинская сестра** (`nurse`) | Просмотр карт и назначений, **отметка о выполнении процедур** (время, расходники, статус) |
| **Ресепшен / Администратор** (`receptionist`) | Создание пациентов и автооткрытие ЭМК, расписание и перенос приёмов, касса (приём оплат, чеки) |
| **Бухгалтерия** (`accountant`) | Отчёты по приёмам врачей, проведённым процедурам, выручка и расчёт зарплат по процентным ставкам врачей |

---

## 📁 Структура проекта

```
MEDIC/
├── app/
│   ├── api/             # FastAPI роутеры (v1) и зависимости (deps.py)
│   ├── core/            # Конфиг (Settings), Loguru, JWT, исключения
│   ├── db/              # Сессии SQLAlchemy (async/sync), Base
│   ├── models/          # SQLAlchemy модели и перечисления (Enums)
│   ├── schemas/         # Pydantic v2 схемы (DTO)
│   ├── services/        # Доменная бизнес-логика (Auth, Patients, Appointments, etc.)
│   ├── repositories/    # Слой доступа к данным (CRUD)
│   ├── tasks/           # Celery приложение, периодические задачи и обработка фото
│   └── main.py          # Точка входа FastAPI, CORS, middleware, handlers
├── tests/               # Тесты unittest (IsolatedAsyncioTestCase)
├── nginx/               # Конфигурация Nginx reverse proxy
├── alembic/             # Миграции базы данных
├── docker-compose.yml   # Запуск всех 7 сервисов (app, db, redis, rabbitmq, worker, flower, nginx)
├── Dockerfile           # Многоступенчатая сборка runtime-образа
└── pyproject.toml       # Зависимости и настройки Ruff
```

---

## 🚀 Быстрый запуск

### 1. Локально с помощью `uv`

```bash
# 1. Клонирование и переход
cd MEDIC

# 2. Создание виртуального окружения и установка зависимостей
py -3.12 -m uv sync --dev

# 3. Настройка переменных окружения
cp .env.example .env

# 4. Запуск тестов
.venv/Scripts/python -m unittest discover -s tests -v

# 5. Запуск линтера Ruff
.venv/Scripts/ruff check .

# 6. Запуск сервера разработки
.venv/Scripts/uvicorn app.main:app --reload --port 8000
```

Интерактивная документация Swagger UI будет доступна по адресу:
👉 `http://localhost:8000/docs`

---

### 2. Запуск полного стека через Docker Compose

Поднимает PostgreSQL, Redis, RabbitMQ, FastAPI, Celery Worker, Celery Beat, Flower и Nginx:

```bash
cp .env.example .env
docker compose up -d --build
```

**Сервисы:**
* API и Swagger: `http://localhost/docs`
* Мониторинг фоновых задач Flower: `http://localhost:5555` (логин/пароль: `admin/admin`)
* Nginx Reverse Proxy: `http://localhost:80`
* RabbitMQ Management: `http://localhost:15672`
