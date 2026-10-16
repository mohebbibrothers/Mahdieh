# ══════════════════════════════════════════════════════════════════════════════
# مهدیه — دستورهای توسعه و انتشار
# ══════════════════════════════════════════════════════════════════════════════
.DEFAULT_GOAL := help
PY := uv run python
MANAGE := $(PY) src/manage.py
SHELL := /bin/bash
TMP ?= /tmp

.PHONY: help
help: ## نمایش این راهنما
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ── نصب و راه‌اندازی ──────────────────────────────────────────────────────────
.PHONY: install
bootstrap: ## راه‌اندازی کامل محیط بومی (uv + Postgres + Redis + مهاجرت + دادهٔ پایه)
	./scripts/dev_bootstrap.sh

install: ## نصب وابستگی‌ها با uv (قفل‌شده)
	uv sync --all-extras

.PHONY: env
env: ## ساخت .env از نمونه (اگر وجود نداشته باشد)
	@test -f .env || (cp .env.example .env && echo "✅ .env ساخته شد — مقادیر توسعه از پیش امن هستند")

.PHONY: services-up
services-up: ## بالا آوردن Postgres/Valkey/MinIO/Mailpit
	docker compose up -d postgres valkey minio mailpit

.PHONY: services-down
services-down: ## خاموش کردن سرویس‌های محلی
	docker compose down

.PHONY: migrate
migrate: ## اجرای مهاجرت‌ها
	$(MANAGE) migrate

.PHONY: makemigrations
makemigrations: ## ساخت مهاجرت‌های جدید
	$(MANAGE) makemigrations

.PHONY: seed
seed: ## دادهٔ نمونه (۳ طبقه و ۵ واحد) — فاز ۲
	@echo "ℹ️  در فاز ۲ پیاده‌سازی می‌شود؛ ساختار فیزیکی در docs/architecture/01-DOMAIN-MODEL.md تعریف شده است."

.PHONY: run
run: ## اجرای سرور توسعه
	$(MANAGE) runserver 0.0.0.0:8000

.PHONY: shell
shell: ## پوستهٔ تعاملی جنگو
	$(MANAGE) shell_plus --ipython || $(MANAGE) shell

# ── کیفیت ────────────────────────────────────────────────────────────────────
.PHONY: lint
lint: ## بررسی لینت (بدون اصلاح خودکار — همان کاری که CI می‌کند)
	uv run ruff check .
	uv run ruff format --check .

.PHONY: format
format: ## اصلاح خودکار قالب و واردات
	uv run ruff check --fix .
	uv run ruff format .

.PHONY: typecheck
typecheck: ## بررسی نوع سخت‌گیرانه
	uv run mypy

.PHONY: test
test: ## همهٔ تست‌ها روی Postgres/Valkey واقعی
	uv run pytest -q

.PHONY: test-fast
test-fast: ## فقط تست‌های سریع (واحد + معماری)
	uv run pytest -q -m "unit or architecture"

.PHONY: test-cov
test-cov: ## تست با پوشش شاخه‌ای
	uv run pytest -q --cov --cov-report=term-missing

.PHONY: security
security: ## اسکن امنیتی کد و وابستگی‌ها (گیت واقعی، بدون «|| true»)
	uv run bandit -q -c pyproject.toml -r src
	uv export --no-emit-project --no-hashes --no-dev --format requirements-txt > "$(TMP)/prod-requirements.txt"
	uv run pip-audit --strict -r "$(TMP)/prod-requirements.txt"

.PHONY: django-check
django-check: ## بررسی سیستم جنگو و نبود drift مهاجرت
	$(MANAGE) check
	$(MANAGE) makemigrations --check --dry-run

.PHONY: schema
schema: ## بازتولید قرارداد OpenAPI
	$(MANAGE) spectacular --file schema.yaml --validate

# ── گیت‌های یکپارچه ──────────────────────────────────────────────────────────
.PHONY: check
check: lint typecheck test-fast django-check ## گیت سریع پیش از کامیت

.PHONY: verify
verify: lint typecheck test-cov django-check security ## گیت کامل (همان چیزی که CI اجرا می‌کند)

# ── عملیات ───────────────────────────────────────────────────────────────────
.PHONY: outbox-relay
visual: ## بازرسی چشمی خودکار: صفحه‌ها را در مرورگر واقعی می‌گیرد و تصویر می‌سازد
	@echo "→ بازرسی چشمی (نیازمند سرور در حال اجرا و playwright نصب‌شده)"
	uv run python scripts/visual_check.py --base-url http://127.0.0.1:8000 --out $(CURDIR)/_qa

outbox-relay: ## اجرای یک چرخهٔ رلهٔ Outbox
	$(MANAGE) outbox_relay --limit 200 --once

.PHONY: audit-verify
audit-verify: ## بررسی یکپارچگی زنجیرهٔ حسابرسی و لنگرها
	$(MANAGE) audit_verify

# ── عملیات (فاز ۸: آمادگی تولید) ────────────────────────────────────────────
.PHONY: backup
backup: ## پشتیبان تأییدشده از پایگاه‌داده (هش + بیانیه + صحت‌سنجی)
	scripts/db_backup.sh --label manual

.PHONY: restore-latest
restore-latest: ## بازیابی تازه‌ترین پشتیبان (با پشتیبان نجات و تأیید زنجیره)
	scripts/db_restore.sh --latest

.PHONY: dr-drill
dr-drill: ## تمرین بازیابی در پایگاه‌دادهٔ قرنطینه + گزارش در docs/drills/
	scripts/dr_drill.sh

.PHONY: deploy-smoke
deploy-smoke: ## بالا آوردن کارگر تولیدی و اثبات قرارداد استقرار (TLS/هدرها/دارایی‌ها)
	scripts/deploy_smoke.sh

.PHONY: load
load: ## بارسنجی سبک روی سرور محلی + گزارش در docs/load/
	scripts/load_test.py --base-url http://127.0.0.1:8000 --users 16 --seconds 20 \
		--report docs/load/$(shell date +%F)-local.md

.PHONY: clean
clean: ## پاک‌سازی فایل‌های موقت
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov coverage staticfiles
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
