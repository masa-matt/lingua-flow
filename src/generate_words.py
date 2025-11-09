# src/generate_words.py
import argparse
import csv
import io
import re
import requests
from words_repo import load_words, save_words

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})


def fetch_text(url: str) -> str:
    r = SESSION.get(url, timeout=60)
    r.raise_for_status()
    try:
        r.encoding = r.apparent_encoding or "utf-8"
    except Exception:
        r.encoding = "utf-8"
    return r.text


# --- 公式URL（必要なら --source-url で上書き） ---
DEFAULT_SOURCES = {
    # 一般書き言葉の基礎 (Alphabetized description = 1語/行)
    "ngsl": "https://www.newgeneralservicelist.com/s/NGSL_12_alphabetized_description.txt",
    # 学術向け拡張
    "nawl": "https://www.newgeneralservicelist.com/s/NAWL_12_alphabetized_description.txt",
    # 話し言葉（短め）
    "ngsl-spoken": "https://www.newgeneralservicelist.com/s/NGSL-Spoken_12_alphabetized_description.txt",
}


def parse_lines_to_words(raw: str):
    words = []
    raw = raw.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = []
    for line in raw.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("#") or s.startswith("//"):
            continue
        if re.match(r"(?i)ngsl|nawl|spoken.*ver|version|copyright|corpus|cambridge", s):
            if re.search(r"\s", s) and not re.match(r"[A-Za-z-]+$", s):
                continue
        cleaned.append(s)

    sample = cleaned[:5]
    delim = "," if any("," in x for x in sample) else ("\t" if any("\t" in x for x in sample) else None)
    if delim:
        reader = csv.reader(io.StringIO("\n".join(cleaned)), delimiter=delim)
        for row in reader:
            if not row:
                continue
            w = row[0].strip().lower()
            if not w or not re.match(r"[a-z\-']+$", w):
                continue
            words.append(w)
        return words

    for s in cleaned:
        w = s.strip().lower()
        if not w or not re.match(r"[a-z\-']+$", w):
            continue
        words.append(w)
    return words


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", required=True, choices=["ngsl", "nawl", "ngsl-spoken"])
    ap.add_argument("--source-url", help="上書きURL（公式から変わる場合）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--csv", help="ローカルCSVに保存（Wordのみ1列）")
    args = ap.parse_args()

    label_map = {"ngsl": "NGSL", "nawl": "NAWL", "ngsl-spoken": "Spoken"}
    label = label_map[args.list]
    url = args.source_url or DEFAULT_SOURCES.get(args.list)
    if not url:
        raise SystemExit("取込URLが未設定です。--source-url を指定してください。")

    print(f"[fetch] {label} <- {url}")
    raw = fetch_text(url)
    words = parse_lines_to_words(raw)
    words = list(dict.fromkeys(words))
    print(f"[parse] {len(words)} words")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            writer = csv.writer(f)
            for t in words:
                writer.writerow([t])
        print(f"[write] saved -> {args.csv}")

    if args.dry_run:
        print("[dry-run] Words CSV を更新せず終了")
        return

    store = load_words()
    created = 0
    tagged = 0
    for word in words:
        entry = store.setdefault(word, {"lists": set(), "seen_tokens": 0, "seen_articles": 0, "last_seen": ""})
        if label not in entry["lists"]:
            entry["lists"].add(label)
            if entry["seen_tokens"] == 0 and entry["seen_articles"] == 0 and entry["last_seen"] == "" and len(entry["lists"]) == 1:
                created += 1
            else:
                tagged += 1

    save_words(store)
    print(f"[words.csv] new={created}, tagged={tagged}, total_entries={len(store)}")


if __name__ == "__main__":
    main()
