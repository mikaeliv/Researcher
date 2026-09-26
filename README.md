# Researcher

Сервис собирает публичные сообщения, извлекает подтверждённые проблемы, группирует похожие свидетельства и публикует карточки в закрытый Telegram-канал. Основной набор источников v2: Ask HN, Discourse, Lemmy и выбранные сайты Stack Exchange. Старые RSS, Reddit, YouTube и App Store collectors сохранены, но не входят в активный набор v2.

## Быстрый старт

Нужны Docker Engine + Compose v2. В каталоге проекта:

```bash
cp .env.example .env
# задать одинаковый пароль в POSTGRES_PASSWORD и DATABASE_URL; заменить все токены и owner ID
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8000/health
```

Создайте `sources.json` на основе `sources.example.json`, проверьте communities и лимиты и загрузите:

```bash
docker compose cp sources.json worker:/tmp/sources.json
docker compose exec worker python scripts/seed.py /tmp/sources.json --disable-unlisted
docker compose exec worker celery -A researcher.tasks:celery_app call researcher.tasks.collect_all
```

`--disable-unlisted` выключает, но не удаляет старые источники. Без флага seed оставляет не перечисленные источники как есть.

Не коммитьте `.env`, локальный `sources.json` с приватными данными или дампы БД.

## Telegram

Создайте бота через BotFather и приватный канал. Назначьте бота администратором с правом публикации. В `.env` задайте токен, числовой ID канала и свой числовой Telegram ID. Запустите бота и отправьте `/status` в личный чат. Доступные команды: `/status`, `/sources`, `/pause`, `/resume`, `/collect`, `/publish`, `/digest`. Нажатия на кнопки карточек сохраняются как обратная связь. Самообучение рейтинга на реакциях пока не включено.

## Sources v2

- Hacker News использует официальный Ask HN feed и загружает ограниченный набор top-level и nested comments только после первичного фильтра.
- Discourse настраивается через `base_url` и `category`. Home Assistant Feature Requests используется как исторический архив, а не как постоянный свежий feed.
- Каждый Lemmy community является отдельным Source и настраивается через `base_url` и `community`.
- Stack Exchange Personal Finance (`money`) и Home Improvement (`diy`) используют существующий generic collector. Ключ Stack Apps для публичных вопросов не нужен.

Общие operational limits задаются через `SOURCE_ITEM_LIMIT`, `SOURCE_LOOKBACK_DAYS`, `MAX_COMMENTS_PER_PUBLICATION` и `MAX_COMMENT_DEPTH`. Значения `limit`, `lookback_days`, `max_comments` и `max_comment_depth` можно переопределить в `Source.config`.

Перед записью в БД и вызовом LLM можно безопасно проверить parsing одного источника:

```bash
docker compose exec worker python scripts/smoke_collect.py "Hacker News / Ask HN" --context
```

## Как работает обработка

1. Celery Beat опрашивает источники каждые три часа. `fetch()` сохраняет title, original post, metadata, URL и дату. Каждый внешний ID уникален внутри источника.
2. Каждые десять минут воркер проверяет до 100 новых записей. Явный мусор получает stage `filtered`; сомнительные публикации проходят дальше. Для кандидатов `fetch_context()` получает ограниченный набор comments/replies.
3. LLM отдельно возвращает pain classification и `product_solvable`. `filtered` означает отказ до LLM, `rejected` — отказ после LLM. Точная цитата обязательна.
4. Для принятой боли создаётся embedding. Ближайший кластер находится в PostgreSQL/pgvector; clustering v2 не изменён.
5. Оценка учитывает число сигналов, число источников, заявленные потери, обходное решение и готовность платить. Публикация требует как минимум два свидетельства из двух настроенных источников и проходной балл.
6. В 09:30, 13:30 и 18:30 UTC публикуется не больше `MAX_DAILY_POSTS` карточек в сутки. Дайджест публикуется в понедельник в 10:00 UTC.

## Операционные детали

- `OPENAI_API_KEY` оплачивается отдельно от подписки ChatGPT. Цены моделей могут меняться; коэффициенты расчёта в `ai.py` обновляйте перед запуском. `MONTHLY_AI_BUDGET_USD` ограничивает оценённые расходы, но при параллельной работе не гарантирует жёсткий потолок у провайдера. Установите лимит и в кабинете провайдера.
- `EMBEDDING_DIMENSIONS` оставьте 1536: такая размерность в миграции. Для другой потребуется миграция.
- YouTube Data API расходует квоту на каждый канал, uploads-плейлист и набор комментариев. При пяти каналах, пяти видео, двух сортировках и опросе раз в три часа получается около 480 запросов в сутки.
- App Store RSS публичен, но доступность, число отзывов и структура выдачи могут меняться. Это экспериментальный источник; проверьте выбранные приложения до включения.
- Обработка Reddit требует собственного одобренного API-доступа и соблюдения правил платформы; без него удалите источник из конфигурации.
- В случае сбоя после успешной отправки в Telegram и до фиксации транзакции сообщение может продублироваться при повторной попытке. Проверьте канал и БД перед ручным повтором `/publish`.
- `/pause` останавливает сбор со всех источников; уже собранные публикации могут продолжить обработку. Отключайте beat/worker для полной остановки.
- `scripts/backup.sh` создаёт дамп БД. Копируйте дампы на отдельный сервер или S3; проверьте восстановление через `pg_restore` на отдельной БД.
- `/health` проверяет только процесс API. Состояние очереди и БД смотрите через `docker compose ps`, логи и `/status`.

## Локальная разработка

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
ruff check src tests scripts
pytest -q
```

CI выполняет линтер, тесты и сборку Docker. Deploy запускается вручную из GitHub Actions после подготовки VPS и секретов; порядок действий — в `OPERATIONS.md`.
