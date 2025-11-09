import argparse
import json
import os
import textwrap

import requests
from dotenv import load_dotenv
from google import genai

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")

if not GEMINI_API_KEY:
    raise SystemExit("❌ GEMINI_API_KEY 未設定 (.env)")
if not NOTION_TOKEN:
    raise SystemExit("❌ NOTION_TOKEN 未設定 (.env)")

client = genai.Client(api_key=GEMINI_API_KEY)

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


def get_article(article_id: str) -> tuple[str, str]:
    url = f"https://api.notion.com/v1/pages/{article_id}"
    res = requests.get(url, headers=HEADERS, timeout=30)
    res.raise_for_status()
    data = res.json()
    props = data.get("properties", {})
    title_arr = props.get("Title", {}).get("title", [])
    title = "".join([t.get("plain_text", "") for t in title_arr]).strip()
    body_arr = props.get("Body", {}).get("rich_text", [])
    body = "".join([t.get("plain_text", "") for t in body_arr]).strip()
    return title or article_id, body


def explain_term(term: str, article_text: str, extra_language: str | None = None) -> dict:
    bilingual_note = ""
    if extra_language:
        bilingual_note = textwrap.dedent(
            f"""
            Additionally, include translations into {extra_language}:
            - "term_local": the term translated or the closest common {extra_language} equivalent (one or two words)
            - "meaning_local": one sentence definition translated into {extra_language}
            """
        ).strip()
    prompt = textwrap.dedent(
        f"""
        You are a vocabulary tutor for intermediate English learners.
        Article excerpt:
        {article_text[:2000]}

        Explain the term below in simple English:
        TERM: {term}

        Return ONLY valid JSON with keys:
        - "term": lowercase term string
        - "meaning": one sentence definition (CEFR B1)
        - "context": short phrase showing how it appears in context
        - "tip": brief memory tip or synonym
        {bilingual_note}
        """
    ).strip()
    try:
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        raw = (getattr(resp, "text", "") or "").strip()
        if not raw:
            raise RuntimeError("空応答")
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"Gemini応答パース失敗: {e}")

    if not isinstance(data, dict):
        raise RuntimeError("Gemini応答形式が不正")
    output = {
        "term": data.get("term", term).strip().lower(),
        "meaning": data.get("meaning", "").strip(),
        "context": data.get("context", "").strip(),
        "tip": data.get("tip", "").strip(),
        "meaning_local": data.get("meaning_local", "").strip(),
        "term_local": data.get("term_local", "").strip(),
    }
    return output


def format_notes(entries: list[dict], extra_language: str | None = None) -> str:
    lines = []
    for item in entries:
        term = item.get("term") or "term"
        meaning = item.get("meaning") or ""
        meaning_local = item.get("meaning_local") or ""
        term_local = item.get("term_local") or ""
        context = item.get("context") or ""
        tip = item.get("tip") or ""
        block = f"- **{term}**"
        if term_local:
            label = extra_language or "Other language"
            block += f" ({label}: {term_local})"
        block += f": {meaning}"
        if meaning_local:
            label = extra_language or "Other language"
            block += f"\n    - {label}: {meaning_local}"
        if context:
            block += f"\n    - Context: {context}"
        if tip:
            block += f"\n    - Tip: {tip}"
        lines.append(block)
    return "\n".join(lines)


def get_prior_vocab_notes(article_id: str) -> str:
    url = f"https://api.notion.com/v1/pages/{article_id}"
    res = requests.get(url, headers=HEADERS, timeout=30)
    res.raise_for_status()
    data = res.json()
    notes_arr = data.get("properties", {}).get("VocabNotes", {}).get("rich_text", [])
    prior = "".join([t.get("plain_text", "") for t in notes_arr]).strip()
    return prior


def update_article_notes(article_id: str, new_notes: str):
    prior = get_prior_vocab_notes(article_id)
    combined = prior.strip()
    if combined:
        if not combined.endswith("\n"):
            combined += "\n"
        combined += new_notes
    else:
        combined = new_notes
    payload = {
        "properties": {
            "VocabNotes": {
                "rich_text": [
                    {"type": "text", "text": {"content": combined[:1900]}}
                ]
            }
        }
    }
    url = f"https://api.notion.com/v1/pages/{article_id}"
    res = requests.patch(url, headers=HEADERS, data=json.dumps(payload))
    if res.status_code == 404:
        print("⚠️ VocabNotes プロパティが存在しないかもしれません。Notion側で作成してください。")
    res.raise_for_status()


def main():
    parser = argparse.ArgumentParser(description="Vocabulary inquiry assistant")
    parser.add_argument("--article-id", required=True, help="Notion Articles page ID")
    parser.add_argument("--auto-save", action="store_true", help="空入力でも直ちに保存せず、確認してから保存")
    parser.add_argument(
        "--extra-language",
        help="Optional language for translated meanings (e.g., Japanese)",
    )
    args = parser.parse_args()
    extra_language = (args.extra_language or "").strip()

    title, body = get_article(args.article_id)
    if not body:
        raise SystemExit("記事本文が空です。")
    print(f"📘 Article: {title}")
    print("分からない語句を入力してください。空エンターで終了。")

    entries = []
    while True:
        term = input("❓ Term (blank to finish): ").strip()
        if not term:
            break
        try:
            info = explain_term(term, body, extra_language=extra_language or None)
        except Exception as e:
            print(f"⚠️ 失敗: {e}")
            continue
        entries.append(info)
        print(f" {info['term']}: {info['meaning']}")
        if info.get("term_local"):
            label = extra_language or "Other language"
            print(f"    term ({label}): {info['term_local']}")
        if info.get("meaning_local"):
            label = extra_language or "Other language"
            print(f"    meaning ({label}): {info['meaning_local']}")
        if info.get("context"):
            print(f"    context: {info['context']}")
        if info.get("tip"):
            print(f"    tip: {info['tip']}")

    if not entries:
        print("ノートはありません。終了します。")
        return

    notes_text = format_notes(entries, extra_language=extra_language or None)
    print("\n=== Vocabulary Notes Preview ===")
    print(notes_text)
    if not args.auto_save:
        confirm = input("Notionに保存しますか？ [Y/n]: ").strip().lower()
        if confirm not in ("", "y", "yes"):
            print("保存をキャンセルしました。")
            return

    update_article_notes(args.article_id, notes_text)
    print("✅ Notion に保存しました。（VocabNotes property）")


if __name__ == "__main__":
    main()
