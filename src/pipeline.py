import os, re, json, time, argparse, math, uuid, collections, datetime, csv
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai
from words_repo import load_words, save_words, reset_counts as reset_words_repo

# ====== .env 読み込み & 環境変数 ======
load_dotenv()  # プロジェクトルートの .env を自動ロード
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
NOTION_TOKEN     = os.getenv("NOTION_TOKEN")
ARTICLES_DB_ID   = os.getenv("ARTICLES_DB_ID")
WORDS_DB_ID      = os.getenv("WORDS_DB_ID")
NOTION_VERSION   = os.getenv("NOTION_VERSION", "2022-06-28")

# ====== ユーティリティ ======
#
# Gemini クライアント（SDK）
#
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY が .env にありません。")
gclient = genai.Client(api_key=GEMINI_API_KEY)

def get(url, headers=None, params=None):
    r = requests.get(url, headers=headers or {}, params=params, timeout=30)
    try:
        r.raise_for_status()
    except requests.HTTPError:
        print("[Notion API error]", r.text)
        raise
    return r

def post(url, headers=None, json=None, data=None):
    r = requests.post(url, headers=headers or {}, json=json, data=data, timeout=60)
    try:
        r.raise_for_status()
    except requests.HTTPError:
        print("[Notion API error]", r.text)
        raise
    return r

def patch(url, headers=None, json=None):
    r = requests.patch(url, headers=headers or {}, json=json, timeout=60)
    try:
        r.raise_for_status()
    except requests.HTTPError:
        print("[Notion API error]", r.text)
        raise
    return r

def slug(text, n=50):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return s[:n]

def now_iso():
    # timezone-aware UTC（Python 3.11+）
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()

AD_CLASS_PAT = re.compile(
    r"(advert|ads?|promo|sponsor|subscribe|newsletter|related|"
    r"share|social|cookie|banner|signup|footer|header|nav|sidebar|outbrain|taboola)",
    re.I,
)
AD_TEXT_PAT = re.compile(
    r"(black\s*friday|buy\s*now|subscribe|sign\s*up|sponsored|deal(s)?|"
    r"coupon|newsletter|shop|read\s*more|related\s*articles?)",
    re.I,
)
BODY_SELECTORS = [
    "article",
    "[role=main]",
    "main",
    "[class*=entry-content]",
    ".td-post-content",
    ".post-content",
    ".article-content",
    ".story-content",
    ".content-body",
    ".c-article__body",
    ".article-body",
]


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _link_density(tag) -> float:
    try:
        text_len = len(tag.get_text(" ", strip=True))
        if not text_len:
            return 1.0
        link_text = " ".join(a.get_text(" ", strip=True) for a in tag.find_all("a"))
        return len(link_text) / max(text_len, 1)
    except Exception:
        return 1.0


def _prune_noise(soup: BeautifulSoup):
    for tag in soup.find_all(["script", "style", "noscript", "svg", "form", "iframe", "picture"]):
        tag.decompose()
    for tag in list(soup.find_all(True)):
        classes = " ".join(tag.get("class", []))
        tag_id = tag.get("id", "")
        if AD_CLASS_PAT.search(classes) or AD_CLASS_PAT.search(tag_id) or AD_CLASS_PAT.search(tag.name):
            tag.decompose()
            continue
        if _link_density(tag) > 0.5 and len(tag.get_text(" ", strip=True)) < 1200:
            tag.decompose()


def _jsonld_article(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    for ld in soup.find_all("script", type="application/ld+json"):
        if not ld.string:
            continue
        try:
            data = json.loads(ld.string)
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            typ = obj.get("@type")
            if not typ:
                continue
            types = (
                {t.lower() for t in typ} if isinstance(typ, list) else {str(typ).lower()}
            )
            if not {"article", "newsarticle", "blogposting"} & types:
                continue
            body = obj.get("articleBody") or obj.get("description") or ""
            title = obj.get("headline") or obj.get("name") or ""
            if body and len(body) > 200:
                return title.strip() if title else None, body.strip()
    return None, None


def _pick_best_block(blocks: list) -> str | None:
    best_text = None
    best_score = -1.0
    for el in blocks:
        txt = el.get_text(" ", strip=True)
        if len(txt) < 200 or AD_TEXT_PAT.search(txt):
            continue
        density = _link_density(el)
        score = len(txt) * (1.0 - min(density, 1.0))
        if score > best_score:
            best_score = score
            best_text = txt
    return best_text


def _best_from_selectors(soup: BeautifulSoup) -> str | None:
    blocks = []
    for sel in BODY_SELECTORS:
        blocks.extend(soup.select(sel))
    return _pick_best_block(blocks)


def _best_parent_block(soup: BeautifulSoup) -> str | None:
    parents: dict = {}
    for p in soup.find_all("p"):
        parent = p.parent
        parents[parent] = parents.get(parent, 0) + 1
    candidates = sorted(parents.items(), key=lambda kv: kv[1], reverse=True)[:8]
    best_parent = None
    best_score = -1.0
    for parent, _ in candidates:
        txt = parent.get_text(" ", strip=True)
        if len(txt) < 200 or AD_TEXT_PAT.search(txt):
            continue
        score = len(txt) * (1.0 - min(_link_density(parent), 1.0))
        if score > best_score:
            best_score = score
            best_parent = parent
    if best_parent:
        return best_parent.get_text(" ", strip=True)
    return None


def _clean_lines(text: str) -> str:
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", text)]
    lines = [ln for ln in lines if ln and not AD_TEXT_PAT.search(ln)]
    return " ".join(lines)


def _amp_candidate_url(soup: BeautifulSoup, base_url: str) -> str | None:
    amp_link = soup.find("link", rel=lambda v: v and "amphtml" in v.lower())
    if amp_link and amp_link.get("href"):
        return urljoin(base_url, amp_link["href"])
    if not base_url.endswith("/amp"):
        return base_url.rstrip("/") + "/amp"
    return None


def _soup_title(soup: BeautifulSoup, fallback: str, url: str) -> str:
    if soup and soup.find("h1"):
        return soup.find("h1").get_text(" ", strip=True)
    if soup and soup.title and soup.title.string:
        return soup.title.string.strip()
    return fallback or url


# ====== 1) 記事本文抽出 ======
def extract_article(url: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0"}
    html = get(url, headers=headers).text
    soup = BeautifulSoup(html, "lxml")
    fallback_title = (soup.title.string or "").strip() if soup.title and soup.title.string else url

    title_ld, body_ld = _jsonld_article(soup)
    if body_ld and len(body_ld) > 300:
        return {"title": title_ld or fallback_title, "body": _normalize_text(body_ld)}

    amp_url = _amp_candidate_url(soup, url)
    if amp_url:
        try:
            amp_html = get(amp_url, headers=headers).text
            soup_amp = BeautifulSoup(amp_html, "lxml")
            amp_title = (soup_amp.title.string or "").strip() if soup_amp.title and soup_amp.title.string else fallback_title
            title_ld_amp, body_ld_amp = _jsonld_article(soup_amp)
            if body_ld_amp and len(body_ld_amp) > 300:
                return {
                    "title": title_ld_amp or amp_title or fallback_title,
                    "body": _normalize_text(body_ld_amp),
                }
            _prune_noise(soup_amp)
            amp_text = _best_from_selectors(soup_amp) or _best_parent_block(soup_amp)
            if amp_text:
                cleaned = _normalize_text(_clean_lines(amp_text))
                if len(cleaned) > 200:
                    return {"title": _soup_title(soup_amp, fallback_title, url), "body": cleaned}
        except Exception:
            pass

    _prune_noise(soup)
    candidate_text = _best_from_selectors(soup) or _best_parent_block(soup)
    if candidate_text:
        cleaned = _normalize_text(_clean_lines(candidate_text))
        if len(cleaned) > 200:
            return {"title": _soup_title(soup, fallback_title, url), "body": cleaned}

    body = " ".join(p.get_text(" ", strip=True) for p in soup.find_all("p"))
    body = _normalize_text(body)
    return {"title": fallback_title, "body": body}

# ====== 2) GeminiでB1等へリライト＆語注 ======
def _strip_md_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        # ```json ... ``` / ``` ... ```
        parts = s.split("```")
        if len(parts) >= 3:
            return parts[1].strip()
    return s

def rewrite_with_gemini(raw_text: str, level: str = "B1") -> dict:
    """
    google-genai SDK に“文字列プロンプト”で投げる（あなたの動作例と同じ方式）。
    返り値は JSON(dict) を期待。失敗時はフォールバックで {} を抽出。
    """
    prompt = (
        "You are an expert editor for graded readers.\n"
        "Rewrite the article for CEFR " + level + " English.\n"
        "Constraints:\n"
        "- Keep facts accurate but make it simpler.\n"
        "- Short sentences, active voice, common vocabulary.\n"
        "- Keep the body around 1,500-1,800 characters so it fits in Notion.\n"
        "- Include a brief glossary of key terms (English only).\n"
        "Return ONLY valid JSON with keys:\n"
        '  \"body\": simplified article (~300-600 words),\n'
        '  \"glossary\": array of { \"term\": \"...\", \"definition\": \"...\" }.\n\n'
        "[ARTICLE]\n" + raw_text
    )
    try:
        resp = gclient.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        raw = (getattr(resp, "text", "") or "").strip()
        if not raw:
            # モデル相性のフォールバック
            alt_model = "gemini-2.0-flash"
            alt = gclient.models.generate_content(model=alt_model, contents=prompt)
            raw = (getattr(alt, "text", "") or "").strip()
            if not raw:
                raise RuntimeError(f"Gemini応答が空です（model={GEMINI_MODEL} / fallback={alt_model}）")
    except Exception as e:
        raise RuntimeError(f"Gemini呼び出し失敗: {e}")

    raw = _strip_md_fence(raw)
    # JSONとして解釈
    try:
        out = json.loads(raw)
    except Exception:
        m = re.search(r"\{[\s\S]*\}\s*$", raw)
        if not m:
            raise RuntimeError(f"GeminiからのJSONパース失敗: raw[:300]={raw[:300]}")
        out = json.loads(m.group(0))
    # 正規化
    if "body" not in out:
        out = {"body": raw, "glossary": []}
    if not isinstance(out.get("glossary"), list):
        out["glossary"] = []
    return out

def extract_specialized_terms(text: str, limit: int = 20) -> list[str]:
    """
    Geminiに記事本文を渡し、専門用語らしき語をJSONで返してもらう。
    """
    prompt = (
        "You are a terminology miner.\n"
        "Read the article below and list domain-specific or specialized terms that general learners might not know.\n"
        "Return ONLY valid JSON with the schema: {\"terms\": [\"word\", ...]}.\n"
        f"Limit to at most {limit} single words or short phrases.\n"
        "Lowercase the terms, remove duplicates, and include only alphabetic tokens.\n\n"
        "[ARTICLE]\n"
        f"{text}"
    )
    try:
        resp = gclient.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        raw = (getattr(resp, "text", "") or "").strip()
        raw = _strip_md_fence(raw)
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", raw)
            if not match:
                raise
            data = json.loads(match.group(0))
        terms = data.get("terms") if isinstance(data, dict) else None
        if not isinstance(terms, list):
            return []
        cleaned = []
        seen = set()
        for t in terms:
            if not isinstance(t, str):
                continue
            word = re.sub(r"[^a-z0-9\- ]", "", t.lower()).strip()
            if not word or word in seen:
                continue
            seen.add(word)
            cleaned.append(word)
        return cleaned[:limit]
    except Exception as e:
        print(f"[warn] Gemini specialized-term extraction failed: {e}")
        return []

# ====== 3) Wordsカタログのロード ======
def fetch_words_catalog() -> tuple[dict, dict]:
    """
    Returns:
      - entries_by_word: {word: {"lists": set[str], "seen_tokens": int, "seen_articles": int, "last_seen": str}}
      - words_by_list:   {list_name: set(word)}
    """
    entries = load_words()
    words_by_list: dict[str, set] = collections.defaultdict(set)
    for word, entry in entries.items():
        for tag in entry.get("lists", []):
            words_by_list[tag].add(word)
    return entries, words_by_list

def seed_words_csv(csv_path: str):
    import csv
    records = load_words()
    added = 0
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            word = row[0].strip().lower()
            if not word:
                continue
            records.setdefault(word, {"lists": set(), "seen_tokens": 0, "seen_articles": 0, "last_seen": ""})
            added += 1
    save_words(records)
    print(f"[seed] merged {added} entries into local words CSV (total={len(records)})")

# ====== 4) トークン化＆カバレッジ計算 ======
STOP = set("""
a an the i you he she it we they me him her us them my your his her its our their
and or but so because although if when while as of in on at to for from with by
this that these those is am are was were be been being do does did will would can
could should might must not no nor than then there here who which what where why how
""".split())

def tokenize(text: str):
    toks = re.findall(r"[A-Za-z']+", text.lower())
    return [t for t in toks if t not in STOP]

def load_manual_specialized_terms(path: str = "data/specialized_terms.txt") -> set[str]:
    terms = set()
    try:
        with open(path) as f:
            for line in f:
                word = line.strip().lower()
                if word:
                    terms.add(word)
    except FileNotFoundError:
        pass
    return terms

WORDS_WRITTEN_TAGS = ("NGSL", "NAWL")
SPOKEN_LIST_TAG = "Spoken"
WORDS_ANALYSIS_ORDER = ("NGSL", "NAWL", "Spoken")

def coverage_metrics(
    text: str,
    words_by_list: dict[str, set],
    specialized_exclude: set | None = None,
    spoken_tag: str = SPOKEN_LIST_TAG,
    written_tags: tuple[str, ...] = WORDS_WRITTEN_TAGS,
):
    tokens_all = tokenize(text)
    counter_all = collections.Counter(tokens_all)
    tokens_filtered = [t for t in tokens_all if not specialized_exclude or t not in specialized_exclude]
    counter_filtered = collections.Counter(tokens_filtered)
    total_filtered = len(tokens_filtered)

    per_list: dict[str, dict] = {}
    for name, words in words_by_list.items():
        if not words:
            continue
        tokens_in_all = sum(counter_all[w] for w in words)
        tokens_in_filtered = sum(counter_filtered[w] for w in words)
        pct = (tokens_in_filtered / total_filtered * 100) if total_filtered else 0.0
        per_list[name] = {
            "tokens": tokens_in_filtered,
            "tokens_all": tokens_in_all,
            "pct": pct,
        }

    written_words = set().union(*(words_by_list.get(tag, set()) for tag in written_tags)) if written_tags else set()
    spoken_words = words_by_list.get(spoken_tag, set())
    written_tokens_filtered = sum(counter_filtered[w] for w in written_words)
    spoken_tokens_filtered = sum(counter_filtered[w] for w in spoken_words)
    union_all = set().union(*words_by_list.values()) if words_by_list else set()
    noncore = [(w, c) for w, c in counter_filtered.most_common() if w not in union_all][:20]

    tokens_ngsl_all = per_list.get("NGSL", {}).get("tokens_all", 0)
    tokens_nawl_all = per_list.get("NAWL", {}).get("tokens_all", 0)
    tokens_spoken_all = per_list.get("Spoken", {}).get("tokens_all", 0)

    return {
        "tokens_total": len(tokens_all),
        "tokens_total_filtered": total_filtered,
        "per_list": per_list,
        "written_tokens": written_tokens_filtered,
        "written_pct": (written_tokens_filtered / total_filtered * 100) if total_filtered else 0.0,
        "spoken_tokens": spoken_tokens_filtered,
        "spoken_pct": (spoken_tokens_filtered / total_filtered * 100) if total_filtered else 0.0,
        "top_noncore": noncore,
        # 互換用キー
        "tokens_ngsl": tokens_ngsl_all,
        "tokens_nawl": tokens_nawl_all,
        "tokens_spoken": tokens_spoken_all,
        "tokens_core": tokens_ngsl_all + tokens_nawl_all,
        "tokens_core_ex": written_tokens_filtered,
    }

def build_coverage_summary(metrics: dict, order: tuple[str, ...] = WORDS_ANALYSIS_ORDER) -> list[str]:
    lines = []
    for tag in order:
        data = metrics.get("per_list", {}).get(tag)
        if not data:
            continue
        lines.append(f"{tag}: {data['tokens']} tokens ({data['pct']:.1f}% specialized-free)")
    lines.append(
        f"Written (NGSL+NAWL): {metrics['written_tokens']} tokens ({metrics['written_pct']:.1f}% specialized-free)"
    )
    lines.append(
        f"Spoken ({SPOKEN_LIST_TAG}): {metrics['spoken_tokens']} tokens ({metrics['spoken_pct']:.1f}% specialized-free)"
    )
    return lines

# ====== 5) Words CSVのカウント更新 ======
def update_word_counts(encounters: dict, word_entries: dict | None = None):
    """
    encounters: dict[word] = token_count_in_this_article
    - Words DBに存在する語だけ更新（存在しない語は無視）
    - SeenTokens += count
    - SeenArticles += 1（その語が今回1度でも出たら）
    """
    if word_entries is None:
        word_entries, _ = fetch_words_catalog()

    # 更新
    for w, c in encounters.items():
        entry = word_entries.get(w)
        if not entry:
            continue
        seen_tokens = entry.get("seen_tokens", 0)
        seen_articles = entry.get("seen_articles", 0)
        entry["seen_tokens"] = seen_tokens + c
        entry["seen_articles"] = seen_articles + 1
        entry["last_seen"] = now_iso()
    save_words(word_entries)

def reset_words_counters(mode: str):
    """Words CSV: counters をゼロ化 or 全削除"""
    total = reset_words_repo(mode)
    if mode == "archive":
        print(f"[reset] removed all entries (previously {total}).")
    else:
        print(f"[reset] counters zeroed for {total} entries.")

# ====== 6) Articles DBに作成 ======
def create_article_in_notion(payload):
    assert NOTION_TOKEN and ARTICLES_DB_ID, "NOTION_TOKEN / ARTICLES_DB_ID missing (.env を確認)"
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    res = post(url, headers=headers, json=payload).json()
    return res

def build_articles_payload(title, url, level, body, glossary, metrics, audio_url=None, tags=None):
    # Notionのプロパティ名はあなたのDBに合わせてある（前のcurlの定義）
    gloss_text = json.dumps(glossary, ensure_ascii=False, indent=2)
    coverage_text = metrics.get("coverage_summary_text", "")
    noncore_text = metrics.get("top_noncore_text") or ", ".join([f"{w}({c})" for w,c in metrics["top_noncore"]])
    props = {
      "parent": {"database_id": ARTICLES_DB_ID},
      "properties": {
        "Title": {"title":[{"type":"text","text":{"content":title}}]},
        "URL": {"url": url},
        "ImportedAt": {"date":{"start": now_iso()}},
        "TargetLevel": {"select":{"name": level}},
        "TokensTotal": {"number": metrics["tokens_total"]},
        "TokensTotalSpecializedFree": {"number": metrics.get("tokens_total_filtered")},
        "NGSL_Tokens": {"number": metrics["tokens_ngsl"]},
        "NAWL_Tokens": {"number": metrics["tokens_nawl"]},
        "WrittenCore_Tokens": {"number": metrics["tokens_core"]},
        "WrittenCore_TokensExSpecialized": {"number": metrics["tokens_core_ex"]},
        "CoverageSummary": {"rich_text":[{"type":"text","text":{"content": coverage_text[:1900]}}]},
        "TopNonCore": {"rich_text":[{"type":"text","text":{"content":noncore_text[:1900]}}]},
        "Status": {"select":{"name":"Ready"}}
      }
    }
    if tags:
        props["properties"]["Tags"] = {"multi_select":[{"name":t} for t in tags]}
    # 本文・用語集
    props["properties"]["Body"] = {"rich_text":[{"type":"text","text":{"content": body[:1900]}}]}
    props["properties"]["Glossary"] = {"rich_text":[{"type":"text","text":{"content": gloss_text[:1900]}}]}
    manual_terms = metrics.get("specialized_terms_manual") or []
    ai_terms = metrics.get("specialized_terms_ai") or []
    if manual_terms:
        props["properties"]["SpecializedTermsManual"] = {
            "multi_select": [{"name": t[:90]} for t in manual_terms[:20]]
        }
    if ai_terms:
        props["properties"]["SpecializedTermsAI"] = {
            "multi_select": [{"name": t[:90]} for t in ai_terms[:20]]
        }
    # 音声URL（外部）
    if audio_url:
        props["properties"]["Audio"] = {
          "files":[{"name":"audio.mp3","type":"external","external":{"url":audio_url}}]
        }
    return props

def get_article_body(page_id: str) -> tuple[str, str]:
    """
    Articlesページの Body を取得して返す。
    戻り値: (title, body)
    """
    assert NOTION_TOKEN, "NOTION_TOKEN missing"
    url = f"https://api.notion.com/v1/pages/{page_id}"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    data = get(url, headers=headers).json()
    props = data.get("properties", {})
    # Title
    title_arr = props.get("Title", {}).get("title", [])
    title = "".join([t.get("plain_text","") for t in title_arr]).strip()
    # Body
    body_arr = props.get("Body", {}).get("rich_text", [])
    body = "".join([t.get("plain_text","") for t in body_arr]).strip()
    return title or page_id, body

def apply_counts_for_article(page_id: str):
    """
    Articles ページIDを指定して Words のカウントを加算する。
    既に加算済みかどうかの判定は、DB側に 'CountsApplied' チェックボックスがある前提（無ければスキップせず適用）。
    """
    title, body = get_article_body(page_id)
    if not body:
        raise RuntimeError(f"Body が空です（page_id={page_id}）")

    # 既に適用済みならスキップ（プロパティが存在する場合のみ）
    url = f"https://api.notion.com/v1/pages/{page_id}"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    page = get(url, headers=headers).json()
    props = page.get("properties", {})
    applied = props.get("CountsApplied", {}).get("checkbox")
    if applied is True:
        print(f"[apply-counts] 既に適用済みのためスキップ: {page_id}")
        return

    # カウントしてWordsへ加算
    cnt = collections.Counter(tokenize(body))
    entries, _ = fetch_words_catalog()
    if not entries:
        raise RuntimeError("Words DB が空です。先に --seed で投入してください。")
    encounters = {w:c for w,c in cnt.items() if w in entries}
    if encounters:
        update_word_counts(encounters, entries)

    # 記事側に印をつける（プロパティが無ければ無視されるのでOK）
    payload = {
      "properties": {
        "CountsApplied": {"checkbox": True},
        "CountsAppliedAt": {"date": {"start": now_iso()}},
      }
    }
    try:
        patch(url, headers=headers, json=payload)
    except requests.HTTPError:
        # DBに該当プロパティが無い場合は何もしない（警告だけ）
        print("[apply-counts] CountsAppliedプロパティが見つからないため印付けをスキップしました")

def mark_counts_applied(page_id: str):
    """Articles ページに CountsApplied 印を付ける（あれば）。"""
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {
      "properties": {
        "CountsApplied": {"checkbox": True},
        "CountsAppliedAt": {"date": {"start": now_iso()}},
      }
    }
    try:
        patch(url, headers=headers, json=payload)
    except requests.HTTPError:
        # DB側にプロパティが無い場合は静かにスキップ
        pass

def unapply_counts_for_article(page_id: str):
    """
    Articles ページでカウント済みの Words を減算する。
    """
    title, body = get_article_body(page_id)
    if not body:
        raise RuntimeError(f"Body が空です（page_id={page_id}）")

    entries, _ = fetch_words_catalog()
    if not entries:
        raise RuntimeError("Words CSV が空です。")

    cnt = collections.Counter(tokenize(body))
    encounters = {w:c for w,c in cnt.items() if w in entries}
    if not encounters:
        print("[unapply-counts] 対応する語が見つからなかったため変更なし")
        return

    for w, c in encounters.items():
        entry = entries[w]
        entry["seen_tokens"] = max(entry.get("seen_tokens", 0) - c, 0)
        entry["seen_articles"] = max(entry.get("seen_articles", 0) - 1, 0)
        if entry["seen_articles"] == 0:
            entry["last_seen"] = ""
    save_words(entries)
    unmark_counts_applied(page_id)
    print(f"[unapply-counts] reverted {len(encounters)} words for article {page_id}")

def unmark_counts_applied(page_id: str):
    """Articles ページの CountsApplied 印を外す。"""
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {
      "properties": {
        "CountsApplied": {"checkbox": False},
        "CountsAppliedAt": {"date": None},
      }
    }
    try:
        patch(url, headers=headers, json=payload)
    except requests.HTTPError:
        pass

# ====== 7) gTTSでローカル音声生成（任意） ======
def synth_to_mp3(text: str, title: str, lang="en"):
    """
    テキストを音声化し、outputディレクトリに保存。
    ファイル名はスラッグ化したタイトル＋UUID短縮で一意に。
    """
    from gtts import gTTS
    os.makedirs("output", exist_ok=True)
    base = slug(title, n=60)
    uniq = uuid.uuid4().hex[:6]
    out_path = os.path.join("output", f"{base}-{uniq}.mp3")
    try:
        tts = gTTS(text=text, lang=lang)
        tts.save(out_path)
        return out_path
    except Exception as e:
        print(f"[warn] gTTS失敗: {e}")
        return None

# ====== メイン ======
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=False, help="記事URL → リライト → Articlesに保存")
    ap.add_argument("--level", default="B1", choices=["A2","B1","B2","C1"])
    ap.add_argument("--seed", help="words.csv でDBを初期投入")
    ap.add_argument("--dry-run-input", action="store_true",
                    help="[1-4のみ] リライト結果をログ出力して終了（Notion保存・カウント更新はしない）")
    ap.add_argument("--skip-word-count", action="store_true",
                    help="[6] Wordsカウント更新をスキップ（1-5は通常実行）")
    ap.add_argument("--apply-counts", metavar="ARTICLE_PAGE_ID",
                    help="既存のArticlesページIDを指定して、Wordsカウントを適用（明示実行）")
    ap.add_argument("--unapply-counts", metavar="ARTICLE_PAGE_ID",
                    help="既存のArticlesページIDについて、Wordsカウントを減算")
    ap.add_argument("--reset-words", choices=["zero","archive"],
                    help="Words をリセット。zero: カウンタ0化 / archive: 全ページをアーカイブ")
    args = ap.parse_args()

    # 明示カウント適用（単独モード）
    if args.apply_counts:
        apply_counts_for_article(args.apply_counts)
        print(f"Applied counts for article: {args.apply_counts}")
        return

    if args.unapply_counts:
        unapply_counts_for_article(args.unapply_counts)
        print(f"Unapplied counts for article: {args.unapply_counts}")
        return

    if args.reset_words:
        reset_words_counters(args.reset_words)
        return

    if args.seed:
        seed_words_csv(args.seed)
        print("Words seeded from CSV.")
        return

    assert args.url, "--url が必要です"

    print("[1] 記事抽出中...")
    art = extract_article(args.url)
    print("  Title:", art["title"][:80])

    print("[2] Geminiでリライト中...")
    simp = rewrite_with_gemini(art["body"], level=args.level)
    body = simp["body"]
    glossary = simp["glossary"]

    print("[3] Wordsカタログ取得中...")
    word_entries, words_by_list = fetch_words_catalog()
    if not words_by_list.get("NGSL"):
        raise RuntimeError("NGSL が未投入です。 `make seed-ngsl` を実行してください。")
    words_registry = set(word_entries.keys())

    print("[4] カバレッジ計算...")
    manual_specialized_terms = load_manual_specialized_terms()
    detected_specialized_terms = extract_specialized_terms(body)
    if detected_specialized_terms:
        print("  Detected specialized terms:", ", ".join(detected_specialized_terms[:20]))
    specialized_ex = manual_specialized_terms | set(detected_specialized_terms)
    metrics = coverage_metrics(body, words_by_list, specialized_exclude=specialized_ex)
    metrics["specialized_terms_manual"] = sorted(manual_specialized_terms)
    metrics["specialized_terms_ai"] = detected_specialized_terms
    coverage_lines = build_coverage_summary(metrics)
    metrics["coverage_lines"] = coverage_lines
    metrics["coverage_summary_text"] = "\n".join(coverage_lines)
    top_noncore_text = ", ".join([f"{w}({c})" for w, c in metrics["top_noncore"]])
    metrics["top_noncore_text"] = top_noncore_text
    analysis_text = "Coverage (specialized-free):\n" + metrics["coverage_summary_text"]
    if top_noncore_text:
        analysis_text += f"\nTop non-core: {top_noncore_text}"
    if detected_specialized_terms:
        analysis_text += f"\nSpecialized terms (AI): {', '.join(detected_specialized_terms[:20])}"
    metrics["analysis_text"] = analysis_text[:1900]
    print("  tokens_total (raw):", metrics["tokens_total"])
    print("  tokens_total (specialized-excluded):", metrics["tokens_total_filtered"])
    for line in coverage_lines:
        print(" ", line)
    if metrics["top_noncore"]:
        print("  Top non-core:", ", ".join([f"{w}({c})" for w, c in metrics["top_noncore"][:5]]))

    # --- ここで dry-run-input を処理：1-4で終了 & リライト本文をログ出力 ---
    if args.dry_run_input:
        print(f"\n===== [{args.level} Rewrite Preview] =====")
        # 長すぎる場合は頭を出しておく。必要ならここを調整。
        preview = body if len(body) <= 2000 else body[:2000] + "\n... (truncated)"
        print(preview)
        print(f"===== [/{args.level} Rewrite Preview] =====\n")
        return

    print("[5] Notion(Articles)登録...")
    # 任意：音声作成（ローカル保存のみ）
    mp3_path = synth_to_mp3(body, title=art["title"])
    # audio_url は外部ホスティングURLがある場合に入れてね
    payload = build_articles_payload(
        title=art["title"], url=args.url, level=args.level,
        body=body, glossary=glossary, metrics=metrics,
        audio_url=None, tags=["Web3","AI"] if "ai" in art["title"].lower() else ["Web3"]
    )
    res = create_article_in_notion(payload)
    print("  Created page:", res.get("id"))

    if args.skip_word_count:
        print("[6] Wordsカウント更新... (skip 指定のためスキップ)")
    else:
        print("[6] Wordsカウント更新...")
        # 今回出現した Words DB の語だけ抽出
        cnt = collections.Counter(tokenize(body))
        encounters = {w: c for w, c in cnt.items() if w in words_registry}
        if encounters:
            update_word_counts(encounters, word_entries)
            # 二重適用防止のため記事側に印を付ける
            if res.get("id"):
                mark_counts_applied(res["id"])

    print("Done. 音声ファイル:", mp3_path or "なし（省略）")

if __name__ == "__main__":
    main()
