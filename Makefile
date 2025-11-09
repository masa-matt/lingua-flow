# Use bash so we can "source" .env
SHELL := /bin/bash
PYTHON ?= python
SRC     := src
ENVFILE ?= .env

# Helper: run a python module with .env exported (if present)
define RUNPY
	set -a; [ -f $(ENVFILE) ] && . $(ENVFILE); set +a; \
	$(PYTHON) $(1)
endef

.PHONY: help input output output-dry patterns patterns-dry seed-ngsl seed-nawl seed-spoken seed-all preview-ngsl csv-ngsl words-reset words-reset-all words-export words-summary apply-counts unapply-counts vocab-notes setup-notion venv

help:
	@echo ""
	@echo "english-web3 Makefile"
	@echo "----------------------------------------"
	@echo "make input URL=<url> LEVEL=B1             # 記事取り込み→B1リライト→Articlesへ"
	@echo "make input-dry URL=<url> LEVEL=B1         # [1-4のみ] リライト結果をログ出力、保存/更新なし"
	@echo "make input-no-countup URL=<url> LEVEL=B1  # [1-5まで] Words721カウント更新をスキップ"
	@echo "make output ARTICLE=<notion_page_id>      # アウトプット支援（保存あり）"
	@echo "make output-dry ARTICLE=<page_id>         # アウトプット支援（保存/カウントなし）"
	@echo "make patterns                             # Web→Patternsシード投入"
	@echo "make patterns-dry                         # シードの内容確認のみ（保存なし）"
	@echo "make seed-ngsl                            # Words CSV に NGSL をマージ"
	@echo "make seed-nawl                            # Words CSV に NAWL をマージ"
	@echo "make seed-spoken                          # Words CSV に NGSL Spoken をマージ"
	@echo "make seed-all                             # NGSL/NAWL/Spokenを一括マージ"
	@echo "make preview-ngsl                         # 取得内容のプレビュー"
	@echo "make csv-ngsl                             # NGSL の生CSVを保存"
	@echo "make words-reset                          # Words CSV のカウンタをゼロ化"
	@echo "make words-reset-all                      # Words CSV を全削除"
	@echo "make words-export                         # Words CSV の内容を表示"
	@echo "make words-summary                        # リストごとの語数サマリ"
	@echo "make apply-counts ARTICLE=<id>            # 既存ArticlesページにWordsカウント適用"
	@echo "make unapply-counts ARTICLE=<id>          # 既存ArticlesページのWordsカウント減算"
	@echo "make vocab-notes ARTICLE=<id>             # Geminiと語彙質問→VocabNotesへ保存"
	@echo "  (例: make vocab-notes ARTICLE=<id> XLUNG=Japanese で指定言語の訳も追加)"
	@echo "make setup-notion PARENT=<page_id>        # Articles/Patterns/Outputs DBを作成し.env更新"
	@echo "make venv                                 # .venv 作成＆依存インストール"
	@echo "----------------------------------------"

# === インプット（記事→B1リライト→Articles作成） ===
# 例: make input URL="https://www.coindesk.com/..." LEVEL=B1
input:
	@if [ -z "$(URL)" ]; then echo "❌ URL を指定してください: make input URL=<url> LEVEL=B1"; exit 1; fi
	@$(call RUNPY,$(SRC)/pipeline.py --url "$(URL)" --level $(or $(LEVEL),B1))

# 1〜4のみ実行してリライト本文をログ出力、保存/更新なし
input-dry:
	@if [ -z "$(URL)" ]; then echo "❌ URL を指定してください: make input-dry URL=<url> LEVEL=B1"; exit 1; fi
	@$(call RUNPY,$(SRC)/pipeline.py --url "$(URL)" --level $(or $(LEVEL),B1) --dry-run-input)

# 6だけスキップ（1〜5は実行し、Words721カウント更新のみ行わない）
input-no-countup:
	@if [ -z "$(URL)" ]; then echo "❌ URL を指定してください: make input-no-countup URL=<url> LEVEL=B1"; exit 1; fi
	@$(call RUNPY,$(SRC)/pipeline.py --url "$(URL)" --level $(or $(LEVEL),B1) --skip-word-count)

# === アウトプット支援（あなたが作文→Gemini添削→Outputs作成＆Words721加算） ===
# 例: make output ARTICLE=2a5f436ffd0a8123456789abcdef
output:
	@if [ -z "$(ARTICLE)" ]; then echo "❌ ARTICLE (Notion Article ID) を指定: make output ARTICLE=<id>"; exit 1; fi
	@$(call RUNPY,$(SRC)/output_assistant.py --article-id $(ARTICLE) $(ARGS))

# 保存/カウント更新を行わない安全モード
# 例: make output-dry ARTICLE=...   /   make output ARTICLE=... ARGS="--dry-run"
output-dry:
	@if [ -z "$(ARTICLE)" ]; then echo "❌ ARTICLE (Notion Article ID) を指定: make output-dry ARTICLE=<id>"; exit 1; fi
	@$(call RUNPY,$(SRC)/output_assistant.py --article-id $(ARTICLE) --dry-run)

# === Patterns シード投入（Web→正規化→Notion Patternsへ） ===
# 例: make patterns
patterns:
	@$(call RUNPY,$(SRC)/seed_patterns.py $(ARGS))

# 保存しない確認モード
# 例: make patterns-dry
patterns-dry:
	@$(call RUNPY,$(SRC)/seed_patterns.py --dry-run)

# ==== Word lists seeding ====
seed-ngsl:
	@$(PYTHON) $(SRC)/generate_words.py --list ngsl

seed-nawl:
	@$(PYTHON) $(SRC)/generate_words.py --list nawl

seed-spoken:
	@$(PYTHON) $(SRC)/generate_words.py --list ngsl-spoken

seed-all: seed-ngsl seed-nawl seed-spoken

# プレビューのみ
preview-ngsl:
	@$(PYTHON) $(SRC)/generate_words.py --list ngsl --dry-run

# CSVへ保存（コミット用）
csv-ngsl:
	@$(PYTHON) $(SRC)/generate_words.py --list ngsl --csv data/ngsl.csv

words-reset:
	@$(call RUNPY,$(SRC)/pipeline.py --reset-words zero)

words-reset-all:
	@$(call RUNPY,$(SRC)/pipeline.py --reset-words archive)

words-export:
	@$(PYTHON) $(SRC)/words_cli.py --export

words-summary:
	@$(PYTHON) $(SRC)/words_cli.py --summary

apply-counts:
	@if [ -z "$(ARTICLE)" ]; then echo "❌ ARTICLE を指定してください: make apply-counts ARTICLE=<page_id>"; exit 1; fi
	@$(call RUNPY,$(SRC)/pipeline.py --apply-counts $(ARTICLE))

unapply-counts:
	@if [ -z "$(ARTICLE)" ]; then echo "❌ ARTICLE を指定してください: make unapply-counts ARTICLE=<page_id>"; exit 1; fi
	@$(call RUNPY,$(SRC)/pipeline.py --unapply-counts $(ARTICLE))

vocab-notes:
	@if [ -z "$(ARTICLE)" ]; then echo "❌ ARTICLE を指定してください: make vocab-notes ARTICLE=<page_id>"; exit 1; fi
	@$(call RUNPY,$(SRC)/vocab_notes.py --article-id $(ARTICLE) $(if $(strip $(or $(XLANG),$(XLUNG))),--extra-language "$(strip $(or $(XLANG),$(XLUNG)))") $(ARGS))

# === Notion初期設置アップ ===
setup-notion:
	@if [ -z "$(PARENT)" ]; then echo "❌ PARENT を指定してください: make setup-notion PARENT=<page_id>"; exit 1; fi
	@$(call RUNPY,$(SRC)/setup_notion.py --parent-id $(PARENT) --env-file $(ENVFILE))

# === 仮想環境セットアップ ===
# 例: make venv
venv:
	@test -d .venv || $(PYTHON) -m venv .venv
	@. .venv/bin/activate && pip install -U pip wheel \
		&& [ -f requirements.txt ] && pip install -r requirements.txt || true
	@echo "✅ venv ready. Use: source .venv/bin/activate"
