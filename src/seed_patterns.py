# -*- coding: utf-8 -*-
"""
Webの公開フレームワーク（CEFR系/機能別英語）から
B1中心の“英文型（Patterns）”を抽出→正規化→Notion(Patterns DB)に投入するスクリプト。

失敗時はフォールバックの内蔵シードで確実投入。
"""

import os, re, json, time, logging, itertools
import requests
from io import BytesIO
from pdfminer.high_level import extract_text
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN   = os.getenv("NOTION_TOKEN")
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")
PATTERNS_DB_ID = os.getenv("PATTERNS_DB_ID")

if not NOTION_TOKEN or not PATTERNS_DB_ID:
    raise SystemExit("❌ NOTION_TOKEN / PATTERNS_DB_ID (.env) を設定してください。")

HEAD = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

# ---- 参照URL（安定めの一次/準一次情報） ----
SOURCES = {
    "coe_descriptors": "https://www.coe.int/en/web/common-european-framework-reference-languages/cefr-descriptors",
    # CEFR “can-do” の一次出典（カテゴリを補強する用途：Opinion/Reasoning/Descriptions など）。
    "speak_and_see_frames_pdf": "https://speak-and-see.com/wp-content/uploads/2025/09/CEFR-A1%E2%80%93B1-Oral-Descriptors-Sentence-Frames.pdf",
    # A1–B1 相当の口頭ディスクリプタ + sentence frames（パターン抽出の主力）
    "bc_opinion": "https://www.englishclub.com/vocabulary/fl-giving-opinions.php",
    # 意見表明の機能別フレーズ（B1帯の代表句抽出）
}

# ---- 正規化ターゲットの“型”カテゴリ（あなたの設計に合わせる） ----
CANONICAL_PATTERNS = [
    # Name, CEFR, Tag, 正規化テンプレ
    ("Opinion-Because",   "B1", "opinion",       "I think [topic] is [adj] because [reason]."),
    ("Cause-Effect",      "B1", "cause-effect",  "When [cause] happens, [effect] will occur."),
    ("Problem-Solution",  "B1", "solution",      "The problem is [X], and the solution is [Y]."),
    ("Comparison",        "B1", "comparison",    "Compared to [A], [B] is more [adj]."),
    ("Contrast",          "A2–B1", "contrast",   "[A] is [adj], but [B] is [adj]."),
    ("Future-Intention",  "A2–B1", "future",     "I will [action] to [goal]."),
    ("Experience",        "B1", "experience",    "I have [past experience] with [topic]."),
    ("Hypothesis",        "B1", "conditional",   "If [condition], [result]."),
    ("Description",       "A2", "description",   "[Topic] has [feature] and [benefit]."),
]

# ---- フォールバック・シード（Web取得失敗時に確実投入） ----
FALLBACK = [
    {
        "Name": "Opinion-Because",
        "Pattern": "I think [topic] is [adj] because [reason].",
        "Example": "I think DeFi is useful because it increases financial access.",
        "Tags": ["opinion"],
        "CEFR": "B1",
        "Source": "fallback"
    },
    {
        "Name": "Cause-Effect",
        "Pattern": "When [cause] happens, [effect] will occur.",
        "Example": "When institutions join DeFi, liquidity will increase.",
        "Tags": ["cause-effect"],
        "CEFR": "B1",
        "Source": "fallback"
    },
    {
        "Name": "Problem-Solution",
        "Pattern": "The problem is [X], and the solution is [Y].",
        "Example": "The problem is key management, and the solution is secure custody.",
        "Tags": ["solution"],
        "CEFR": "B1",
        "Source": "fallback"
    },
    {
        "Name": "Comparison",
        "Pattern": "Compared to [A], [B] is more [adj].",
        "Example": "Compared to banks, DeFi is more transparent.",
        "Tags": ["comparison"],
        "CEFR": "B1",
        "Source": "fallback"
    },
    {
        "Name": "Contrast",
        "Pattern": "[A] is [adj], but [B] is [adj].",
        "Example": "DeFi is open, but banks are restricted.",
        "Tags": ["contrast"],
        "CEFR": "A2–B1",
        "Source": "fallback"
    },
    {
        "Name": "Future-Intention",
        "Pattern": "I will [action] to [goal].",
        "Example": "I will study Solidity to become a blockchain developer.",
        "Tags": ["future"],
        "CEFR": "A2–B1",
        "Source": "fallback"
    },
    {
        "Name": "Experience",
        "Pattern": "I have [past experience] with [topic].",
        "Example": "I have worked with Layer 2 rollups before.",
        "Tags": ["experience"],
        "CEFR": "B1",
        "Source": "fallback"
    },
    {
        "Name": "Hypothesis",
        "Pattern": "If [condition], [result].",
        "Example": "If fees get lower, user adoption will grow.",
        "Tags": ["conditional"],
        "CEFR": "B1",
        "Source": "fallback"
    },
    {
        "Name": "Description",
        "Pattern": "[Topic] has [feature] and [benefit].",
        "Example": "Blockchain has transparency and security.",
        "Tags": ["description"],
        "CEFR": "A2",
        "Source": "fallback"
    },
]

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def http_get(url, expect_pdf=False, timeout=30):
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    if expect_pdf:
        return r.content
    return r.text

def parse_pdf_frames(pdf_bytes):
    """PDF（A1–B1 Oral Descriptors & Sentence Frames）から“型っぽい文”を抽出"""
    text = extract_text(BytesIO(pdf_bytes))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    frames = []
    # 代表的な型（B1帯）っぽい行を拾う規則
    PAT = re.compile(
        r"^(I think|I believe|In my opinion|When\b.*|If\b.*|Compared to\b.*|The problem is\b.*|I will\b.*|I have\b.*|[A-Za-z]+\s+is\s+.+,\s+but\s+[A-Za-z]+\s+is\s+.+)$",
        re.IGNORECASE,
    )
    for ln in lines:
        if len(ln.split()) < 4:
            continue
        if PAT.match(ln):
            frames.append(ln)
    return frames

def scrape_opinion_expressions(html):
    """意見表明（英語表現）ページから代表句を抽出（B1帯っぽい短文）"""
    # HTMLの <li> や改行区切りから“根幹フレーズ”を拾う
    items = re.findall(r">([^<>]{3,120})<", html)  # 雑だが安定
    cleaned = []
    for it in items:
        s = re.sub(r"\s+", " ", it).strip()
        # 過度な長文や記号だらけを除外
        if 3 <= len(s) <= 90 and s[0].isalpha():
            cleaned.append(s)
    # 代表的シード（I think / In my opinion / From my point of view ...）を優先抽出
    seeds = [x for x in cleaned if re.match(r"^(I think|In my opinion|Personally,|From my (point|perspective))", x, re.I)]
    return list(dict.fromkeys(seeds))[:20]  # 重複排除して上位

def normalize_to_canonical(frames):
    """
    抽出した“生フレーズ”を、あなたの正規化テンプレ（CANONICAL_PATTERNS）にマップ。
    """
    out = []
    for raw in frames:
        s = raw.strip()
        low = s.lower()

        if low.startswith(("i think", "i believe", "in my opinion", "personally", "from my")):
            out.append(("Opinion-Because", "I think [topic] is [adj] because [reason].",
                        "I think DeFi is useful because it increases financial access."))
            continue
        if low.startswith("when "):
            out.append(("Cause-Effect", "When [cause] happens, [effect] will occur.",
                        "When institutions join DeFi, liquidity will increase."))
            continue
        if low.startswith("if "):
            out.append(("Hypothesis", "If [condition], [result].",
                        "If fees get lower, user adoption will grow."))
            continue
        if low.startswith("compared to"):
            out.append(("Comparison", "Compared to [A], [B] is more [adj].",
                        "Compared to banks, DeFi is more transparent."))
            continue
        if low.startswith("the problem is"):
            out.append(("Problem-Solution", "The problem is [X], and the solution is [Y].",
                        "The problem is key management, and the solution is secure custody."))
            continue
        if low.startswith("i will"):
            out.append(("Future-Intention", "I will [action] to [goal].",
                        "I will study Solidity to become a blockchain developer."))
            continue
        if low.startswith("i have"):
            out.append(("Experience", "I have [past experience] with [topic].",
                        "I have worked with Layer 2 rollups before."))
            continue
        if re.search(r"\bbut\b", low) and re.search(r"\bis\b", low):
            out.append(("Contrast", "[A] is [adj], but [B] is [adj].",
                        "DeFi is open, but banks are restricted."))
            continue

    # Description は直接拾いづらいので後で補間
    return out

def dedup_preserve(seq):
    seen = set()
    out = []
    for x in seq:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return out

def build_seed_from_web():
    seeds = []

    # 1) frames（メイン）：speak-and-see の A1–B1 sentence frames PDF
    try:
        pdf = http_get(SOURCES["speak_and_see_frames_pdf"], expect_pdf=True)
        frames = parse_pdf_frames(pdf)
        norm = normalize_to_canonical(frames)
        seeds.extend(norm)
        logging.info(f"PDF frames extracted: {len(frames)} → normalized {len(norm)}")
    except Exception as e:
        logging.warning(f"frames PDF 取得 or 解析失敗: {e}")

    # 2) 意見表明の典型文：EnglishClub（opinion）
    try:
        html = http_get(SOURCES["bc_opinion"])
        exprs = scrape_opinion_expressions(html)
        norm = normalize_to_canonical(exprs)
        seeds.extend(norm)
        logging.info(f"Opinion expressions extracted: {len(exprs)} → normalized {len(norm)}")
    except Exception as e:
        logging.warning(f"opinion ページ取得失敗: {e}")

    # 3) CEFR descriptors（カテゴリ名の裏取りに使うだけ。直接文は抽出しない）
    try:
        _ = http_get(SOURCES["coe_descriptors"])
        # 到達確認のみ。カテゴリ名の信頼性担保に使う（実データは上の抽出で充足）。
        logging.info("CEFR descriptors (CoE) reachable.")
    except Exception as e:
        logging.warning(f"CEFR descriptors 到達失敗: {e}")

    # 4) 正規テンプレの網羅補間（Description等が欠けたら追加）
    names_already = {n for (n, _, _) in seeds}
    for name, cefr, tag, patt in CANONICAL_PATTERNS:
        if name not in names_already:
            # 代表例をフォールバックから見つける
            fb = next((x for x in FALLBACK if x["Name"] == name), None)
            ex = fb["Example"] if fb else "Example to be added."
            seeds.append((name, patt, ex))

    # 重複排除
    seeds = dedup_preserve(seeds)

    # 最終整形
    final = []
    for (name, patt, ex) in seeds:
        meta = next((x for x in CANONICAL_PATTERNS if x[0] == name), None)
        cefr = meta[1] if meta else "B1"
        tag  = meta[2] if meta else "other"
        final.append({
            "Name": name,
            "Pattern": patt,
            "Example": ex,
            "Tags": [tag],
            "CEFR": cefr,
            "Source": "web+normalize"
        })

    # Safety: 最低7件は保証（足りなければフォールバック追加）
    if len(final) < 7:
        need = 7 - len(final)
        final.extend(FALLBACK[:need])

    return final

def notion_create_pattern(item):
    payload = {
        "parent": {"database_id": PATTERNS_DB_ID},
        "properties": {
            "Name":    {"title":     [{"text": {"content": item["Name"]}}]},
            "Pattern": {"rich_text": [{"text": {"content": item["Pattern"]}}]},
            "Example": {"rich_text": [{"text": {"content": item["Example"]}}]},
            "Tags":    {"multi_select": [{"name": t} for t in item.get("Tags", [])]},
            "CEFR":    {"select": {"name": item.get("CEFR","B1")}},
            "UsedCount": {"number": 0},
            "LastUsed": {"date": None}
        }
    }
    r = requests.post("https://api.notion.com/v1/pages", headers=HEAD, data=json.dumps(payload))
    r.raise_for_status()
    return r.json().get("id")

def main():
    logging.info("=== Webから“英文型”シードを収集 → Notion:Patterns へ投入 ===")
    try:
        seeds = build_seed_from_web()
        logging.info(f"Normalized seed count: {len(seeds)}")
    except Exception as e:
        logging.error(f"Web由来シード生成に失敗、フォールバックを使用: {e}")
        seeds = FALLBACK

    # 既存のNameを取得して重複投入を防ぐ
    existing = set()
    try:
        payload = {"page_size": 100}
        start = None
        while True:
            if start: payload["start_cursor"] = start
            rr = requests.post(f"https://api.notion.com/v1/databases/{PATTERNS_DB_ID}/query",
                               headers=HEAD, data=json.dumps(payload))
            rr.raise_for_status()
            data = rr.json()
            for res in data.get("results", []):
                name_prop = res["properties"].get("Name", {}).get("title", [])
                if name_prop:
                    existing.add(name_prop[0]["plain_text"])
            if not data.get("has_more"):
                break
            start = data.get("next_cursor")
        logging.info(f"既存: {len(existing)} 件")
    except Exception as e:
        logging.warning(f"既存一覧の取得失敗（続行）: {e}")

    created = 0
    for item in seeds:
        if item["Name"] in existing:
            logging.info(f"skip (exists): {item['Name']}")
            continue
        try:
            pid = notion_create_pattern(item)
            created += 1
            logging.info(f"created: {item['Name']} ({pid})")
            time.sleep(0.15)
        except Exception as e:
            logging.warning(f"create failed: {item['Name']} -> {e}")

    logging.info(f"✅ 完了: 新規 {created} 件（既存 {len(existing)} 件はスキップ）")

if __name__ == "__main__":
    main()
