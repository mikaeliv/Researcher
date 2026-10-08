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

Проверьте хранящиеся в Git communities и лимиты в `sources.json`, затем синхронизируйте таблицу Source:

```bash
docker compose exec worker python scripts/seed.py --disable-unlisted
docker compose exec worker celery -A researcher.tasks:celery_app call researcher.tasks.collect_all
```

`--disable-unlisted` выключает, но не удаляет старые источники. Без флага seed оставляет не перечисленные источники как есть.

Не добавляйте credentials в `sources.json`. Не коммитьте `.env` или дампы БД.

## Telegram

Создайте бота через BotFather и приватный канал. Назначьте бота администратором с правом публикации. В `.env` задайте токен, числовой ID канала и свой числовой Telegram ID. Запустите бота и отправьте `/status` в личный чат. Доступные команды: `/status`, `/sources`, `/pause`, `/resume`, `/collect`, `/publish`, `/digest`. Нажатия на кнопки карточек сохраняются как обратная связь. Самообучение рейтинга на реакциях пока не включено.

## Sources v2

- Hacker News использует официальный Ask HN feed и загружает ограниченный набор top-level и nested comments только после первичного фильтра.
- Discourse читает RSS через `feed_url` и `limit`; прежний режим `base_url` + `category` сохранён для Home Assistant Feature Requests. TrueNAS Community и Nextcloud Community добавлены выключенными до ручной оценки.
- Каждый Lemmy community является отдельным Source и настраивается через `base_url` и `community`.
- Stack Exchange Personal Finance (`money`) и Home Improvement (`diy`) используют существующий generic collector. Ключ Stack Apps для публичных вопросов не нужен.

Общие operational limits задаются через `SOURCE_ITEM_LIMIT`, `SOURCE_LOOKBACK_DAYS`, `MAX_COMMENTS_PER_PUBLICATION` и `MAX_COMMENT_DEPTH`. Значения `limit`, `lookback_days`, `max_comments` и `max_comment_depth` можно переопределить в `Source.config`.

Перед записью в БД и вызовом LLM можно безопасно проверить parsing одного источника:

```bash
docker compose exec worker python scripts/smoke_collect.py "Hacker News / Ask HN" --context
```

Для read-only проверки полного Sources v2 pipeline на реальных данных используйте:

```bash
docker compose exec worker python scripts/dry_run_sources.py --limit 10
docker compose exec worker python scripts/dry_run_sources.py \
  --source "Hacker News / Ask HN" --limit 10
```

При повторной проверке уже просмотренные публикации можно пропустить до фильтрации и LLM:

```bash
python scripts/dry_run_sources.py \
  --source "Hacker News / Ask HN" \
  --offset 10 \
  --limit 20
```

Dry-run читает активные `Source` и данные бюджета `AiUsage`. Он не создаёт Publication,
Evidence или Cluster, не меняет Source/cursor и не вызывает embeddings/clustering. Единственная
разрешённая запись — штатная строка `AiUsage`, создаваемая production `analyze()`.
Явный `--source` также позволяет проверить выключенный Source после синхронизации `sources.json`.

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

## Opportunity: controlled dry-run

Второй уровень работает отдельно от production pipeline: strict `Cluster` сохраняет конкретную
проблему, а виртуальная Opportunity объединяет разные проблемы по общим pain, JTBD и outcome.
Production `same_problem()`, пороги 0.35 / top_k=3, eligibility и Telegram publishing не меняются.
Новые таблицы пока не используются задачами Celery или публикацией.

Модели: `Opportunity`, `OpportunityCluster` (unique `cluster_id`: максимум одна Opportunity),
`ClusterOpportunityProfile` (один профиль на Cluster). Профиль включает pain, job, outcome,
audience и context, а Opportunity дополнительно хранит scope, exclusions, description и
representative. Embeddings имеют ту же размерность 1536 и используют существующий provider/model.

Миграция `0002_opportunities.py` добавляет эти таблицы и `sources.source_group_key`, заполняя ключ
для существующих источников. Upgrade не изменяет Cluster/Evidence; downgrade удаляет только новые
таблицы и поле. В `0001` закреплена прежняя схема Source и прежний набор таблиц, чтобы свежая
установка не создавала новые таблицы дважды. Применение миграции — отдельное действие, не dry-run:

```bash
.venv/bin/alembic upgrade head
# Обратная миграция при необходимости: .venv/bin/alembic downgrade 0001
```

Для Docker используется существующий сервис `migrate` (`docker compose run --rm migrate`).

`source_group_key` задан для всех десяти текущих источников в `sources.json` и поддерживается seed:
все Lemmy categories → `lemmy`, все Stack Exchange sites → `stackexchange`, Ask HN → `hackernews`,
Home Assistant → `homeassistant`, TrueNAS → `truenas`, Nextcloud → `nextcloud`, Proxmox → `proxmox`.
Для старых Reddit/YouTube/App Store используется platform kind; неизвестные RSS/Discourse с URL
группируются по hostname. Без URL используется общий kind, чтобы не завышать diversity.
Dry-run читает цепочку Cluster → Evidence → Publication → Source и считает множество ключей
по всему membership — эквивалент `COUNT(DISTINCT source_group_key)`, а не число Source.id.

После отдельного применения миграции запустите из корня проекта:

```bash
OPPORTUNITY_CANDIDATE_THRESHOLD=0.40 \
OPPORTUNITY_CANDIDATE_TOP_K=5 \
OPPORTUNITY_MATCH_CONFIDENCE=0.70 \
.venv/bin/python scripts/dry_run_opportunity_clustering.py \
  --limit 50 --max-ai-usd 2 --cache .var/opportunity_profiles
```

В Docker с обновлённым образом:

```bash
docker compose exec \
  -e OPPORTUNITY_CANDIDATE_THRESHOLD=0.40 \
  -e OPPORTUNITY_CANDIDATE_TOP_K=5 \
  -e OPPORTUNITY_MATCH_CONFIDENCE=0.70 \
  worker python scripts/dry_run_opportunity_clustering.py --limit 50 --max-ai-usd 2
```

`--source-groups lemmy,truenas,nextcloud,proxmox` выбирает уникальные Clusters, у которых хотя бы
один Evidence связан с указанной `source_group_key`. Пробелы удаляются, повторяющиеся ключи
объединяются; пустые и неизвестные ключи вызывают CLI-ошибку до AI processing. Сортировка по ID и
`--limit` применяются после фильтра. Для выбранного Cluster сохраняются все Evidence/source groups.
Без фильтра прежнее поведение сохраняется; без `--limit` читаются все подходящие Clusters, включая
уже опубликованные. Перед AI выводятся `Source group filter: ...` и `Clusters selected: N`.

Следующий controlled run для self-hosted vertical:

```bash
python scripts/dry_run_opportunity_clustering.py \
  --source-groups lemmy,truenas,nextcloud,proxmox \
  --max-ai-usd 2 \
  --cache .var/opportunity_profiles
```

`--no-cache` отключает локальный кэш. JSON-кэш хранит только Cluster profiles/embeddings и
инвалидируется при изменении problem/description/audience, моделей, размерности или CACHE_VERSION.
При изменении инструкций profile generation нужно увеличить CACHE_VERSION. Opportunity semantics,
matches и final summary проверяются заново в каждом запуске.

Скрипт абстрагирует `Cluster.title`/description/audience через `build_cluster_opportunity_profile()`.
В embedding input попадают только нормализованные pain, job, outcome, audience и context с
фиксированными метками. Сырые Publication/Evidence и старый Cluster embedding не используются.

Candidate distance < 0.40 и top_k=5 дают только дешёвый retrieval. `same_opportunity()` использует
отдельный structured output; принятие требует `same_opportunity=True`, confidence >= 0.70 и
`too_broad_if_merged=False`. Общая технология, аудитория или категория недостаточны.
После каждого принятого attach `build_opportunity()` обновляет общую семантику и embedding без
генерации title. Следующий Cluster сравнивается с этой общей Opportunity, а не с последним членом.
Connected components не используются.

`validate_opportunity()` проверяет соответствие каждого concrete Cluster точному pain/job/outcome.
Outliers исключаются и остаются отдельными виртуальными singleton; профиль пересобирается и
проверяется повторно. Неизвестные outlier IDs, low confidence, слишком broad группа и ошибки
проверки не разрешают attach. Финальная проверка предшествует выбору ближайшего к centroid
ClusterOpportunityProfile representative и единственной генерации title/description. Final summary
не может менять уже проверенную семантику; такой ответ отмечается как ошибка.

Обе DB-сессии запускают PostgreSQL `SET TRANSACTION READ ONLY` с `autoflush=False`; в конце
выполняется rollback. Не сохраняются Opportunity, membership, профили или даже `AiUsage`.
Скрипт не импортирует publishing/Telegram. Единственная запись — локальный JSON-кэш.
`--max-ai-usd` ограничивает оценочные расходы текущего запуска, также учитывая оставшийся
сохранённый месячный бюджет. Последний API-вызов может превысить лимит; ставки унаследованы из
`ai.py`. Незаписанные расходы других dry-run не входят в месячный бюджет. API-вызовы оплачиваются
реально, поэтому выводится локальная оценка стоимости.

Отчёт содержит все счётчики кандидатов/решений/ошибок, размеры групп, source diversity, подробные
multi-cluster Opportunities и top 20 ближайших отклонённых кандидатов с причиной/confidence/broad
flag. `Same-source candidate pairs` и `Cross-source candidate pairs` делят все retrieved candidates
после threshold/top_k: cross-source означает непустую симметрическую разность source-group sets
нового Cluster и всей candidate Opportunity. Пересекающиеся, но разные множества тоже cross-source.
Блок `CLOSEST CROSS-SOURCE CANDIDATES` показывает top 20 по расстоянию, включая accepted pairs,
с группами обеих сторон и результатом LLM. При раннем attach оставшиеся retrieved candidates
помечаются `NOT EVALUATED`; ошибки LLM — `ERROR`. Диагностика хранит снимок Opportunity до attach.
`same_opportunity true/false` отражает bool модели до confidence/broad/validation veto.
Расстояние и confidence каждого члена относятся к проверке при его присоединении; для seed
singleton они отсутствуют. Ошибки профилей явно отмечают пропущенные Clusters. Ошибка финальной
валидации разбивает группу на singletons; ошибка summary оставляет проверенную группу с
`[summary unavailable]`. При наличии ошибок результат следует считать частичным.

Пример формата на условных трёх Clusters (это не результат запуска на production данных):

```text
Clusters processed: 3
Cluster opportunity profiles: 3
Embedding candidate pairs: 2
same_opportunity calls: 2
same_opportunity true: 1
same_opportunity false: 1
same_opportunity errors: 0
Virtual opportunities: 2
Opportunities with >=2 clusters: 1
Singletons: 1
Opportunities with >=2 distinct source groups: 1
Opportunities with >=3 distinct source groups: 0
Largest opportunity: 2

=== Opportunity 1 ===
Title:
Operate workloads consistently across small virtualization hosts
Underlying pain:
Host differences require manual coordination of workload operations.
JTBD:
Move and deploy workloads across hosts without reconciling each host manually.
Desired outcome:
Treat several hosts as one manageable environment.
Audience:
Operators of small self-hosted virtualization environments.
Scope:
Multi-host workload migration and deployment.
Exclusions:
Network configuration and unrelated self-hosting jobs.
Clusters: 2
Evidence: 4
Distinct source groups: 2
Source groups:
- lemmy
- proxmox

[Cluster 1]
problem: VM migration between hosts is difficult
[Cluster 2]
problem: LXC deployment across several hosts is manual

=== CLOSEST REJECTED CANDIDATES ===
embedding distance: 0.2100
Cluster A: 3
problem: Proxmox network configuration is difficult
Cluster B / Opportunity clusters: [1, 2]
same_opportunity confidence: 0.93
reason: Shared technology, but a different job and desired outcome.
too_broad_if_merged: False
```

Unit tests используют mocked AI, не оценивают качество живой LLM: positive pain/JTBD, same technology
с разными jobs, broad veto, разные outcomes, confidence threshold, semantic chaining, outliers,
centroid representative, source grouping, кэш, ошибки и отсутствие DB-записей. Схема/constraint
проверяются на SQLite, upgrade/downgrade — через PostgreSQL SQL generation.
