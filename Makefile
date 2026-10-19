# ══════════════════════════════════════════════════════════════════════════════
# مهدیه — دستورهای توسعه و انتشار
# ══════════════════════════════════════════════════════════════════════════════
.DEFAULT_GOAL := help
PY := uv run python
MANAGE := $(PY) src/manage.py
PYTEST := uv run pytest
SHELL := /bin/bash
TMP ?= /tmp

.PHONY: help
help: ## نمایش این راهنما
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ── نصب و راه‌اندازی ──────────────────────────────────────────────────────────
.PHONY: install
bootstrap: ## راه‌اندازی کامل محیط بومی (uv + MariaDB + مهاجرت + جدول کش + دادهٔ پایه)
	./scripts/dev_bootstrap.sh

install: ## نصب وابستگی‌ها با uv (قفل‌شده)
	uv sync --all-extras

.PHONY: env
env: ## ساخت .env از نمونه (اگر وجود نداشته باشد)
	@test -f .env || (cp .env.example .env && echo "✅ .env ساخته شد — مقادیر توسعه از پیش امن هستند")

.PHONY: services-up
services-up: ## بالا آوردن MariaDB/MinIO/Mailpit
	docker compose up -d mariadb minio mailpit

.PHONY: services-down
services-down: ## خاموش کردن سرویس‌های محلی
	docker compose down

.PHONY: migrate
migrate: ## اجرای مهاجرت‌ها + جدول کش (بی‌اثر اگر از قبل باشد)
	$(MANAGE) migrate
	$(MANAGE) createcachetable

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
test: ## همهٔ تست‌ها روی MariaDB واقعی
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
schema: ## بازتولید قرارداد OpenAPI در docs/api/openapi.yaml (بی‌هشدار، معتبر)
	$(MANAGE) spectacular --file docs/api/openapi.yaml --validate --fail-on-warn
	@echo "✅ سند API به‌روز شد: docs/api/openapi.yaml"

# ── گیت‌های یکپارچه ──────────────────────────────────────────────────────────
.PHONY: check
check: lint typecheck test-fast django-check ## گیت سریع پیش از کامیت

.PHONY: verify
verify: lint typecheck test-cov django-check security ## گیت کامل (همان چیزی که CI اجرا می‌کند)

.PHONY: readiness
readiness: ## ساخت برگهٔ آمادگی انتشار در docs/release/readiness.md (اجرا، نه ادعا)
	uv run python scripts/readiness_report.py
	@echo "✅ برگهٔ آمادگی در docs/release/readiness.md آماده است."

.PHONY: healthcheck
healthcheck: ## پایش سلامت سرویس در حال اجرا (۰ سالم · ۱ افت‌کرده · ۲ ناسالم)
	$(MANAGE) healthcheck --base-url http://127.0.0.1:8000 --json
	@echo "کد خروجی همین فرمان، قرارداد پایش است: ۰ سالم · ۱ افت‌کرده · ۲ ناسالم"

.PHONY: secrets-baseline
secrets-baseline: ## بازتولید خط پایهٔ رازها (پس از ویرایش فایل‌های نشان‌دار)
	uvx detect-secrets scan --baseline .secrets.baseline
	@echo "✅ .secrets.baseline تازه شد — به همان کامیت اضافه‌اش کنید (git add .secrets.baseline)."


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
dr-drill: ## تمرین بازیابی در پایگاه‌دادهٔ قرنطینه (MAHDIEH_DRILL_DB) + گزارش در var/drills/
	scripts/dr_drill.sh

.PHONY: deploy-smoke
deploy-smoke: ## بالا آوردن کارگر تولیدی و اثبات قرارداد استقرار (TLS/هدرها/دارایی‌ها)
	scripts/deploy_smoke.sh

.PHONY: load
load: ## بارسنجی سبک روی سرور محلی + گزارش در docs/load/
	$(PY) scripts/load_test.py --base-url http://127.0.0.1:8000 \
		--users $(USERS) --seconds $(SECONDS) \
		--report docs/load/$(shell date +%F)-load.md

.PHONY: load-stress
load-stress: ## بارسنجی فشار: ۶۴ کاربر × ۳ پاس روی سرور تولیدی (:8010) + مدل ظرفیت
	$(PY) scripts/load_test.py --base-url http://127.0.0.1:8010 \
		--users 64 --seconds 15 --repeats 3 --secure --host-header mahdie-shohada.ir \
		--server-cpus $(shell nproc) \
		--report docs/load/$(shell date +%F)-load-64u-capacity.md

expire-holds: ## آزادسازی نگه‌داشت‌های منقضی (در تولید: mahdieh-holds.timer)
	$(MANAGE) expire_holds

report: ## گزارش اکسل از خط فرمان (FROM/TO به شکل YYYY-MM-DD · OUT=مسیر)
	$(MANAGE) export_report --from $(FROM) --to $(TO) --out $(OUT)

reminders: ## یادآور پیامکی پیش از پذیرش (ARGS="--dry-run" برای فهرست بدون ارسال)
	$(MANAGE) send_booking_reminders $(ARGS)

feedback-requests: ## درخواست نظرسنجی پس از اقامت (ARGS="--dry-run" برای فهرست بدون ارسال)
	$(MANAGE) send_feedback_requests $(ARGS)

retention: ## نگهداری داده: کمینه‌سازی هویت زائر + پاک‌سازی دفترها (ARGS="--dry-run" یا "--stats")
	$(MANAGE) retention_sweep $(ARGS)

.PHONY: sms-patterns
sms-patterns: ## وضعیت کدهای الگوی پیامک و کدهای گم‌شده (ARGS="--request" برای پیام درخواست)
	$(MANAGE) sms_patterns $(ARGS)

.PHONY: sms-selfcheck
sms-selfcheck: ## آزمون سالم‌بودن پیامک (TO=0912… یک پیام واقعی می‌فرستد)
	$(MANAGE) sms_selfcheck $(if $(TO),--to $(TO) --confirm,)

.PHONY: chaos
chaos: ## تست‌های آشوب (تزریق خرابی سرویس بیرونی/صف)
	$(PYTEST) -m chaos src/tests/test_chaos.py

.PHONY: preflight
preflight: ## بازرسی پیش از انتشار روی سرور تولید (فقط خواندنی)
	scripts/preflight_production.sh $(ARGS)

.PHONY: gate
gate: ## دروازهٔ انتشار تولید (STRICT=1 برای سخت‌گیرانه)
	$(MANAGE) production_check $(if $(STRICT),--strict,)

.PHONY: dast
dast: ## کاوش امنیتی روی سامانهٔ در حال اجرا (BASE=http://127.0.0.1:8000)
	uv run python scripts/dast_probe.py --base-url $(or $(BASE),http://127.0.0.1:8000)

.PHONY: sms-golive
sms-golive: ## فعال‌سازی کدهای الگو روی سرور (CODES="key=code,…" · ARGS="--check" برای بازرسی)
	scripts/sms_golive.sh $(ARGS) $(if $(CODES),--codes "$(CODES)",)

.PHONY: clean
clean: ## پاک‌سازی فایل‌های موقت
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov coverage staticfiles
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
