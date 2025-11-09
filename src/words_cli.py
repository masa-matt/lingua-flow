import argparse
from collections import Counter
from words_repo import load_words, WORDS_CSV_PATH


def cmd_export():
    records = load_words()
    if not records:
        print(f"⚠️ words.csv が見つからない、もしくは空です: {WORDS_CSV_PATH}")
        return
    print("word,lists,seen_tokens,seen_articles,last_seen")
    for word in sorted(records.keys()):
        entry = records[word]
        print(
            f"{word},"
            f"{';'.join(sorted(entry.get('lists', set())))}," 
            f"{entry.get('seen_tokens',0)},"
            f"{entry.get('seen_articles',0)},"
            f"{entry.get('last_seen','')}"
        )


def cmd_summary():
    records = load_words()
    if not records:
        print(f"⚠️ words.csv が見つからない、もしくは空です: {WORDS_CSV_PATH}")
        return
    counter = Counter()
    for entry in records.values():
        for tag in entry.get("lists", []):
            counter[tag] += 1
    print(f"Total words: {len(records)}")
    for tag, count in counter.most_common():
        print(f"{tag}: {count}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true", help="リスト別語数サマリを表示")
    ap.add_argument("--export", action="store_true", help="全行をCSV形式で出力")
    args = ap.parse_args()

    if args.summary:
        cmd_summary()
    elif args.export:
        cmd_export()
    else:
        ap.error("どちらかのモードを指定してください（--summary / --export）")


if __name__ == "__main__":
    main()
