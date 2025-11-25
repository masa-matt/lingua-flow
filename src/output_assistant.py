import os, json, re, time, argparse, datetime, collections, random
import requests
from google import genai
from dotenv import load_dotenv

from words_repo import load_words as load_words_csv, save_words as save_words_csv

# ====== .env ======
load_dotenv()
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
NOTION_TOKEN     = os.getenv("NOTION_TOKEN")
NOTION_VERSION   = os.getenv("NOTION_VERSION", "2022-06-28")
ARTICLES_DB_ID   = os.getenv("ARTICLES_DB_ID")
PATTERNS_DB_ID   = os.getenv("PATTERNS_DB_ID")
OUTPUTS_DB_ID    = os.getenv("OUTPUTS_DB_ID")
WORDS_DB_ID      = os.getenv("WORDS_DB_ID")

if not GEMINI_API_KEY:
    raise SystemExit("❌ GEMINI_API_KEY 未設定")
if not NOTION_TOKEN:
    raise SystemExit("❌ NOTION_TOKEN 未設定")
if not PATTERNS_DB_ID:
    raise SystemExit("❌ PATTERNS_DB_ID 未設定")
if not OUTPUTS_DB_ID:
    raise SystemExit("❌ OUTPUTS_DB_ID 未設定")

# ====== Gemini client ======
client = genai.Client(api_key=GEMINI_API_KEY)

# ====== HTTP helpers ======
def post(url, headers=None, json=None, data=None):
    r = requests.post(url, headers=headers or {}, json=json, data=data, timeout=60)
    r.raise_for_status()
    return r

def patch(url, headers=None, json=None):
    r = requests.patch(url, headers=headers or {}, json=json, timeout=60)
    r.raise_for_status()
    return r

def get(url, headers=None, params=None):
    r = requests.get(url, headers=headers or {}, params=params, timeout=30)
    r.raise_for_status()
    return r

# ====== utilities ======
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()

def tokenize(text: str):
    toks = re.findall(r"[A-Za-z']+", text.lower())
    return [t for t in toks if len(t) > 1]

COMMON_SKIP_WORDS = set("""
a an the i you he she it we they me him her us them my your his her its our their
and or but so because although if when while as of in on at to for from with by
this that these those is am are was were be been being do does did will would can
could should might must not no nor than then there here who which what where why how
""".split())

# ====== Notion ======
HEAD = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

def query_database(db_id, filter_obj=None):
    payload = {"page_size": 100}
    if filter_obj:
        payload["filter"] = filter_obj
    results = []
    start_cursor = None
    while True:
        if start_cursor:
            payload["start_cursor"] = start_cursor
        resp = post(f"https://api.notion.com/v1/databases/{db_id}/query", headers=HEAD, json=payload).json()
        results.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        start_cursor = resp.get("next_cursor")
    return results

def get_article_body(article_id):
    data = get(f"https://api.notion.com/v1/pages/{article_id}", headers=HEAD).json()
    title_prop = data["properties"]["Title"]["title"]
    body_prop  = data["properties"]["Body"]["rich_text"]
    title = title_prop[0]["plain_text"] if title_prop else "(untitled)"
    body  = body_prop[0]["plain_text"] if body_prop else ""
    return title, body

# ====== Keywords (from article × Words catalog) ======
def load_words721():
    """
    Returns: (words_set, local_records | None)
    - If WORDS_DB_ID is defined, fetch from Notion and return (set, None).
    - Otherwise, fall back to local words CSV (Words repo) to stay in sync with pipeline.py.
    """
    if WORDS_DB_ID:
        words = set()
        for r in query_database(WORDS_DB_ID):
            title = r["properties"]["Word"]["title"]
            if title:
                words.add(title[0]["plain_text"].strip().lower())
        return words, None

    records = load_words_csv()
    if not records:
        return set(), {}
    return set(records.keys()), records

def suggest_keywords_from_article(article_text, words721_set, limit=40):
    toks = tokenize(article_text)
    counter = collections.Counter(toks)
    candidate_pool = counter.most_common(max(limit * 4, 80))
    top = [w for w, _ in candidate_pool if w in words721_set and w not in COMMON_SKIP_WORDS]
    # 似た語の重複を緩く排除
    out, seen = [], set()
    for w in top:
        k = w.rstrip("s")
        if k in seen:
            continue
        seen.add(k)
        out.append(w)
        if len(out) >= limit:
            break
    return out


def choose_keywords_interactive(suggestions: list[str], chunk_size: int = 8) -> list[str]:
    """
    Allow the user to iterate through suggested keywords, skipping batches if needed.
    Returns the user's chosen keywords (lowercase). Empty list if user opts to type none.
    """
    if not suggestions:
        raw = input("No suggestions available. Type your own keywords (comma-separated, blank to skip):\n> ").strip()
        return [w.strip().lower() for w in raw.split(",") if w.strip()]

    page = 0
    total_pages = max((len(suggestions) + chunk_size - 1) // chunk_size, 1)
    while True:
        start = page * chunk_size
        end = start + chunk_size
        chunk = suggestions[start:end]
        if not chunk:
            # 末尾まで到達したら最初からループ
            page = 0
            continue
        print(f"🧩 Suggested keywords [{page + 1}/{total_pages}]: {', '.join(chunk)}")
        print("    (Type comma-separated keywords, 'skip' for another set, or leave blank to auto-pick from above)")
        raw = input("> ").strip()
        if not raw:
            auto_pick = chunk[: max(2, min(3, len(chunk)))]
            print("  (auto) Using:", ", ".join(auto_pick))
            return auto_pick
        cmd = raw.lower()
        if cmd in {"skip", "next"}:
            page = (page + 1) % total_pages
            continue
        chosen = [w.strip().lower() for w in raw.split(",") if w.strip()]
        if chosen:
            return chosen
        print("⚠️ 有効な入力ではありません。もう一度試してください。")

# ====== Patterns ======
def list_patterns():
    patterns = query_database(PATTERNS_DB_ID)
    pattern_list = []
    for p in patterns:
        name = p["properties"]["Name"]["title"][0]["plain_text"]
        patt = p["properties"]["Pattern"]["rich_text"][0]["plain_text"]
        ex   = p["properties"]["Example"]["rich_text"][0]["plain_text"] if p["properties"]["Example"]["rich_text"] else ""
        pattern_list.append({"id": p["id"], "name": name, "pattern": patt, "example": ex})
    return pattern_list

def choose_pattern_interactive(pattern_list):
    print("🧱 Available patterns:")
    for i, p in enumerate(pattern_list, 1):
        ex = f" → ex: {p['example']}" if p["example"] else ""
        print(f"{i}. {p['name']}: {p['pattern']}{ex}")
    while True:
        sel = input("Select pattern number (Enter='auto'): ").strip().lower()
        if not sel or sel in {"auto", "rand", "random"}:
            choice = random.choice(pattern_list)
            idx = pattern_list.index(choice) + 1
            print(f"  (auto) Using #{idx}: {choice['name']}")
            return choice
        if sel.isdigit() and 1 <= int(sel) <= len(pattern_list):
            return pattern_list[int(sel) - 1]
        print("Please input a valid number, or press Enter for auto.")

# ====== Gemini: correction only (user writes) ======
def correct_sentence(user_sentence, pattern_text, keywords, article_title):
    prompt = f"""
You are an English writing coach.
The learner wrote one sentence using this pattern:

Pattern: "{pattern_text}"
Topic: "{article_title}"
Keywords to prefer: {', '.join(keywords)}

Task:
1) Correct the sentence for grammar and naturalness (aim CEFR B2 clarity but keep it simple).
2) Keep the meaning and the chosen pattern if possible.
3) Return JSON with keys: draft, corrected, feedback. 'draft' must echo the original input.

Learner sentence:
{user_sentence}
"""
    res = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    txt = (getattr(res, "text", "") or "").strip()
    m = re.search(r"\{[\s\S]*\}", txt)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    # fallback
    return {"draft": user_sentence, "corrected": txt or user_sentence, "feedback": ""}

# ====== Notion: create Outputs row ======
def create_output_page(article_id, pattern_id, keywords, draft, corrected, feedback, tokens_used):
    payload = {
        "parent": {"database_id": OUTPUTS_DB_ID},
        "properties": {
            "Title": {"title": [{"text": {"content": f"Output for article {article_id[:8]}"}}]},
            "Article": {"relation": [{"id": article_id}]},
            "Pattern": {"relation": [{"id": pattern_id}]},
            "Keywords": {"multi_select": [{"name": k} for k in keywords]},
            "Draft": {"rich_text": [{"text": {"content": draft[:1900]}}]},
            "Corrected": {"rich_text": [{"text": {"content": corrected[:1900]}}]},
            "Feedback": {"rich_text": [{"text": {"content": feedback[:1900]}}]},
            "TokensUsed": {"number": tokens_used},
            "Date": {"date": {"start": now_iso()}},
            "Status": {"select": {"name": "Done"}}
        }
    }
    r = post("https://api.notion.com/v1/pages", headers=HEAD, json=payload).json()
    return r.get("id")

# ====== Word usage update ======
def update_usedinoutput(encounters: dict, local_records: dict | None = None) -> bool:
    """
    Returns True if any counts were updated.
    - If WORDS_DB_ID is configured, update the Notion property UsedInOutput.
    - Otherwise update the local words CSV (same counters used by pipeline.py).
    """
    if not encounters:
        return False

    if WORDS_DB_ID:
        mapping = {}
        for res in query_database(WORDS_DB_ID):
            title = res["properties"]["Word"]["title"]
            if title:
                w = title[0]["plain_text"].strip().lower()
                mapping[w] = (res["id"], res["properties"])
        updated = False
        for w, c in encounters.items():
            if w not in mapping:
                continue
            page_id, props = mapping[w]
            used_out = props.get("UsedInOutput", {}).get("number") or 0
            payload = {"properties": {"UsedInOutput": {"number": used_out + c}}}
            patch(f"https://api.notion.com/v1/pages/{page_id}", headers=HEAD, json=payload)
            time.sleep(0.1)
            updated = True
        return updated

    records = local_records if local_records is not None else load_words_csv()
    if not records:
        return False

    updated = False
    for w, c in encounters.items():
        entry = records.get(w)
        if not entry:
            continue
        entry["seen_tokens"] = entry.get("seen_tokens", 0) + c
        entry["seen_articles"] = entry.get("seen_articles", 0) + 1
        entry["last_seen"] = now_iso()
        updated = True
    if updated:
        save_words_csv(records)
    return updated

# ====== main ======
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--article-id", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="更新系 (Notion保存 / カウント更新) を行わない（参照・添削のみ）")
    args = ap.parse_args()

    # 1) 記事取得
    print("[1] Fetching article...")
    title, body = get_article_body(args.article_id)
    print("📘", title[:80])

    # 2) Wordsカタログロード
    print("[2] Loading Words / keywords catalog...")
    words721, words_records = load_words721()
    if not words721:
        print("⚠️ Words catalog が空のようです。`make seed-ngsl` などで投入、または WORDS_DB_ID を設定してください。")

    # 3) キーワード提案 → 選択
    print("[3] Suggesting keywords...")
    suggested = suggest_keywords_from_article(body, words721) if words721 else []
    chosen = choose_keywords_interactive(suggested)

    # 4) パターン選択
    print("[4] Selecting pattern...")
    plist = list_patterns()
    pattern = choose_pattern_interactive(plist)

    # 5) ユーザーが作文 → Geminiが添削
    print("[5] Your turn: write one sentence using the chosen pattern & keywords.")
    print(f"   Pattern: {pattern['pattern']}")
    if chosen:
        print(f"   Keywords: {', '.join(chosen)}")
    user_sentence = input("✍️ Your sentence:\n> ").strip()
    if not user_sentence:
        raise SystemExit("❌ 入力が空です。やり直してください。")

    print("🤖 Gemini reviewing...")
    result = correct_sentence(user_sentence, pattern["pattern"], chosen, title)
    draft = result.get("draft", user_sentence)
    corrected = result.get("corrected", user_sentence)
    feedback = result.get("feedback", "")
    print("\n—— Result ——")
    print("✍️ Draft:     ", draft)
    print("✅ Corrected: ", corrected)
    print("💬 Feedback:  ", feedback)

    # 6) Notion保存（dry-run対応）
    print("\n[6] Creating Notion output page...")
    if args.dry_run:
        print("[dry-run] Notionへの保存をスキップします。")
        output_id = "DRY-RUN"
    else:
        output_id = create_output_page(args.article_id, pattern["id"], chosen, draft, corrected, feedback, len(chosen))
        print("🧾 Created Output page:", output_id)

    # 7) Words catalog count update（dry-run対応）
    print("[7] Updating word usage counts...")
    if args.dry_run:
        print("[dry-run] Wordsカウント更新をスキップします。")
    else:
        cnt = collections.Counter(tokenize(corrected))
        # Wordsカタログに含まれる単語だけを加算
        encounters = {w: c for w, c in cnt.items() if w in words721}
        if encounters:
            updated = update_usedinoutput(encounters, local_records=words_records)
            if updated:
                source = "Notion Words DB" if WORDS_DB_ID else "Words CSV"
                print(f"✅ {source} counts updated.")
            else:
                print("ℹ️ 対象語が Words カタログに見つかりませんでした。")
        else:
            print("ℹ️ キーワード候補の語は含まれていませんでした。")

    print("\n✅ Done.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️ Canceled by user.")
