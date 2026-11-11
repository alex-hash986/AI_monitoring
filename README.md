# AI Market Watch: Платформа автоматизированного мониторинга конкурентов и стратегического ценообразования

![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![PostgreSQL / TimescaleDB](https://img.shields.io/badge/PostgreSQL%20%2F%20TimescaleDB-316192?logo=postgresql&logoColor=white)
![Celery / Redis](https://img.shields.io/badge/Celery%20%2F%20Redis-3F7D20?logo=redis&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![OpenAI API](https://img.shields.io/badge/OpenAI-API-202123)

AI Market Watch помогает командам ритейла отслеживать цены и акции конкурентов. Категорийные менеджеры видят динамику по товарам, маркетинг получает единую ленту промо, а руководство может опираться на историю изменений и рекомендации при пересмотре цен.

## Бизнес-задачи

| Задача | Как её решает платформа |
| --- | --- |
| Оперативный мониторинг цен | Celery Beat запускает сбор каждые 30 минут. Streamlit показывает последний сохранённый срез, историю и дельты. При необходимости сбор можно запустить вручную. |
| Выявление возможного демпинга | Падение цены более чем на **10%** относительно предыдущего реального среза вызывает Telegram-алерт. По этому сигналу команда выбирает товары для проверки. |
| Сбор промо-акций | Скрейперы читают веб-каталоги и открытые каналы Telegram через t.me/s/. В ленте можно фильтровать акции и посты по конкурентам. |
| Поддержка решений о ценах | AI-отчёт содержит резюме, тренды и рекомендации с обоснованием, приоритетом и целевой метрикой. Плановый отчёт формируется раз в сутки по UTC. Ручной запуск создаёт новый отчёт. |

## Архитектура

~~~mermaid
flowchart LR
    Beat["Celery Beat"] --> Queue["Redis"]
    Queue --> Worker["Celery Worker"]
    Worker -->|Запуск сбора| Scrapers["Scrapers · веб и Telegram"]
    Scrapers -->|Наблюдения| Worker
    Worker --> DB["PostgreSQL / TimescaleDB"]
    DB --> LLM["LLM Engine"]
    LLM -->|Отчёт| DB
    DB --> Dashboard["Streamlit"]
    Dashboard -->|Ручной запуск| Queue
    Worker -->|Падение цены более чем на 10%| Bot["Telegram Bot"]
~~~

Скрейперы извлекают данные из JSON-LD, Microdata, HTML-карточек и публичных постов Telegram. TimescaleDB хранит историю цен в гипертаблице. PostgreSQL-запрос с `LATERAL JOIN` выбирает текущий и предыдущий сопоставимые срезы для расчёта дельт. PostgreSQL Advisory Locks защищают сидирование и цикл мониторинга от параллельного запуска.

### Работа при сбоях и ограничениях

- **Graceful Degradation.** Если веб-каталог недоступен, скрейпер может сохранить явно помеченные демо-наблюдения. Такие цены исключаются из расчёта алертов. Ошибка отдельного источника отражается в результате задачи.
- **AI без ключа.** OpenAI API возвращает отчёт по JSON Schema. Если `OPENAI_API_KEY` пуст, система формирует детерминированный локальный отчёт. При ошибке OpenAI уже записанные наблюдения остаются в базе.
- **Локальный режим SQLite.** Python и Streamlit по умолчанию используют `monitoring_demo.db`. Сбор выполняется синхронно, а аналитика формирует локальный отчёт. Для этого режима достаточно Python и зависимостей проекта.

## Быстрый запуск

### Docker Compose

Нужны запущенный Docker Engine с поддержкой Linux-контейнеров и Docker Compose. Из корня проекта скопируйте `.env.example` в `.env` и задайте надёжный `POSTGRES_PASSWORD`. Для анализа через OpenAI заполните `OPENAI_API_KEY` и при необходимости измените `OPENAI_MODEL`. Для Telegram-алертов заполните `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID`.

~~~bash
docker compose up --build -d
docker compose ps -a
~~~

Сервис `migrate` применяет миграции Alembic. В списке сервисов проверьте, что он завершился с кодом 0. Откройте [Streamlit](http://localhost:8501), перейдите во вкладку **Управление** и запустите мониторинг. На пустой базе система добавит TechStore и GadgetHub. Для подключения реальных источников заполните `website_url` и `tg_channel` в таблице `competitors`.

### Локально через SQLite

Из корня проекта с Python 3.12 выполните команды в PowerShell:

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_local_demo.py
.\.venv\Scripts\python.exe -m streamlit run src/dashboard/app.py --server.address 127.0.0.1 --server.port 8501
~~~

В macOS и Linux используйте `.venv/bin/python` вместо .\.venv\Scripts\python.exe. Скрипт создаёт таблицы, выполняет один демо-цикл и сохраняет отчёт. Интерфейс доступен на [localhost:8501](http://localhost:8501).

## Документация

[Техническое задание в формате DOCX](./ТЗ_AI_система_мониторинга_конкурентов.docx)

## Скриншоты интерфейса

Здесь можно разместить график и таблицу цен, ленту акций и карточки AI-рекомендаций.
