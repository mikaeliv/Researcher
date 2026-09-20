#!/usr/bin/env bash
set -euo pipefail
mkdir -p backups
docker compose exec -T db pg_dump -U researcher -Fc researcher > "backups/researcher-$(date -u +%Y%m%d-%H%M%S).dump"
find backups -type f -name 'researcher-*.dump' -mtime +14 -delete

