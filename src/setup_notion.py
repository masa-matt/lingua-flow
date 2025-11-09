import argparse
import json
import os
import requests
from dotenv import load_dotenv

DEFAULT_NOTION_VERSION = "2022-06-28"


def notion_headers(token, version):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": version,
        "Content-Type": "application/json",
    }


def create_database(name, parent_id, properties, headers):
    payload = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"type": "text", "text": {"content": name}}],
        "properties": properties,
    }
    r = requests.post("https://api.notion.com/v1/databases", headers=headers, data=json.dumps(payload))
    if not r.ok:
        raise SystemExit(f"❌ Failed to create {name}: {r.status_code} {r.text}")
    data = r.json()
    return data["id"]


def articles_properties():
    levels = [{"name": lvl} for lvl in ["A2", "B1", "B2", "C1"]]
    status = [
        {"name": "Ready", "color": "green"},
        {"name": "Draft", "color": "yellow"},
    ]
    return {
        "Title": {"title": {}},
        "URL": {"url": {}},
        "ImportedAt": {"date": {}},
        "TargetLevel": {"select": {"options": levels}},
        "TokensTotal": {"number": {}},
        "TokensTotalSpecializedFree": {"number": {}},
        "NGSL_Tokens": {"number": {}},
        "NAWL_Tokens": {"number": {}},
        "WrittenCore_Tokens": {"number": {}},
        "WrittenCore_TokensExSpecialized": {"number": {}},
        "TopNonCore": {"rich_text": {}},
        "Status": {"select": {"options": status}},
        "Tags": {"multi_select": {}},
        "Body": {"rich_text": {}},
        "Glossary": {"rich_text": {}},
        "SpecializedTermsManual": {"multi_select": {}},
        "SpecializedTermsAI": {"multi_select": {}},
        "Audio": {"files": {}},
        "CountsApplied": {"checkbox": {}},
        "CountsAppliedAt": {"date": {}},
    }


def patterns_properties():
    tags = [
        {"name": "opinion"},
        {"name": "cause-effect"},
        {"name": "solution"},
        {"name": "comparison"},
        {"name": "contrast"},
        {"name": "future"},
        {"name": "experience"},
        {"name": "conditional"},
        {"name": "description"},
        {"name": "other"},
    ]
    levels = [{"name": lvl} for lvl in ["A2", "A2–B1", "B1", "B2"]]
    return {
        "Name": {"title": {}},
        "Pattern": {"rich_text": {}},
        "Example": {"rich_text": {}},
        "Tags": {"multi_select": {"options": tags}},
        "CEFR": {"select": {"options": levels}},
        "UsedCount": {"number": {}},
        "LastUsed": {"date": {}},
    }


def outputs_properties(articles_db_id, patterns_db_id):
    status = [
        {"name": "Draft", "color": "yellow"},
        {"name": "Done", "color": "green"},
    ]
    return {
        "Title": {"title": {}},
        "Article": {
            "relation": {
                "database_id": articles_db_id,
                "type": "single_property",
                "single_property": {},
            }
        },
        "Pattern": {
            "relation": {
                "database_id": patterns_db_id,
                "type": "single_property",
                "single_property": {},
            }
        },
        "Keywords": {"multi_select": {}},
        "Draft": {"rich_text": {}},
        "Corrected": {"rich_text": {}},
        "Feedback": {"rich_text": {}},
        "Tokens721Used": {"number": {}},
        "Date": {"date": {}},
        "Status": {"select": {"options": status}},
    }


def update_env_file(path, updates):
    lines = []
    if os.path.exists(path):
        with open(path, "r") as f:
            lines = f.readlines()

    updated_keys = set()
    new_lines = []
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0]
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    for key, value in updates.items():
        if key not in updated_keys and not any(l.startswith(f"{key}=") for l in new_lines):
            new_lines.append(f"{key}={value}\n")

    with open(path, "w") as f:
        f.writelines(new_lines)


def main():
    ap = argparse.ArgumentParser(description="Create Articles/Patterns/Outputs databases and store IDs into .env")
    ap.add_argument("--parent-id", required=True, help="Notion parent page ID (database container)")
    ap.add_argument("--env-file", default=".env", help="Path to .env file to update")
    args = ap.parse_args()

    load_dotenv(args.env_file)
    token = os.getenv("NOTION_TOKEN")
    version = os.getenv("NOTION_VERSION", DEFAULT_NOTION_VERSION)
    if not token:
        raise SystemExit("❌ NOTION_TOKEN が .env に設定されていません。")

    headers = notion_headers(token, version)

    print("📄 Creating Articles database...")
    articles_id = create_database("Articles", args.parent_id, articles_properties(), headers)
    print("   ->", articles_id)

    print("📄 Creating Patterns database...")
    patterns_id = create_database("Patterns", args.parent_id, patterns_properties(), headers)
    print("   ->", patterns_id)

    print("📄 Creating Outputs database...")
    outputs_id = create_database("Outputs", args.parent_id, outputs_properties(articles_id, patterns_id), headers)
    print("   ->", outputs_id)

    update_env_file(args.env_file, {
        "ARTICLES_DB_ID": articles_id,
        "PATTERNS_DB_ID": patterns_id,
        "OUTPUTS_DB_ID": outputs_id,
    })
    print(f"✅ .env 更新済み ({args.env_file})")


if __name__ == "__main__":
    main()
