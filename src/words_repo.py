import csv
import os
from typing import Dict, Set

WORDS_CSV_PATH = os.getenv("WORDS_CSV_PATH", os.path.join("data", "words.csv"))
FIELDNAMES = ["word", "lists", "seen_tokens", "seen_articles", "last_seen"]


def _ensure_parent_dir():
    parent = os.path.dirname(WORDS_CSV_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _parse_lists(value: str | None) -> Set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(";") if item.strip()}


def load_words() -> Dict[str, dict]:
    """
    CSV -> {word: {"lists": set, "seen_tokens": int, "seen_articles": int, "last_seen": str}}
    """
    if not os.path.exists(WORDS_CSV_PATH):
        return {}

    records: Dict[str, dict] = {}
    with open(WORDS_CSV_PATH, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            word = (row.get("word") or "").strip().lower()
            if not word:
                continue
            records[word] = {
                "lists": _parse_lists(row.get("lists")),
                "seen_tokens": int(row.get("seen_tokens") or 0),
                "seen_articles": int(row.get("seen_articles") or 0),
                "last_seen": (row.get("last_seen") or "").strip(),
            }
    return records


def save_words(records: Dict[str, dict]):
    _ensure_parent_dir()
    with open(WORDS_CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for word in sorted(records.keys()):
            entry = records[word]
            writer.writerow({
                "word": word,
                "lists": ";".join(sorted(entry.get("lists", set()))),
                "seen_tokens": entry.get("seen_tokens", 0),
                "seen_articles": entry.get("seen_articles", 0),
                "last_seen": entry.get("last_seen", "") or "",
            })


def reset_counts(mode: str = "zero"):
    records = load_words()
    before = len(records)
    if mode == "archive":
        records.clear()
    else:
        for entry in records.values():
            entry["seen_tokens"] = 0
            entry["seen_articles"] = 0
            entry["last_seen"] = ""
    save_words(records)
    return before
