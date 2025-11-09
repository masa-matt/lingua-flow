# LinguaFlow

**LinguaFlow** is an AI-powered toolkit that helps second-language learners improve comprehension and fluency through balanced input and output practice.
It allows you to process any article, simplify it to your target CEFR level, check how much vocabulary you already understand, and keep track of your learning in Notion.

---

## ✨ Features

### 🧠 AI Simplified Reading
- Automatically rewrites any English article into your target CEFR level (A2 / B1 / B2 / C1).
- Keeps the meaning accurate while simplifying grammar and structure.
- Generates a short glossary of difficult or **specialized terms** for easier understanding.

### 📊 Vocabulary Coverage
- Measures how much of the text you can already understand using:
  - **NGSL (New General Service List)** — common written English
  - **NAWL (New Academic Word List)** — academic or formal English
  - **Spoken** — everyday conversational English
- Shows both your “written” and “spoken” vocabulary coverage.
- Lets you exclude **specialized terms** (for example, blockchain or medical jargon) to see your general comprehension rate more clearly.

### 🗂 Notion Integration
- Creates and links **Articles**, **Patterns**, and **Outputs** databases in Notion.
- Saves each article with its simplified version, glossary, and vocabulary coverage.
- Tracks word exposure — how often you read, hear, or use each word.

### 📚 Wordlist Management
- Imports official lists (NGSL / NAWL / Spoken) directly from the [New General Service List](https://www.newgeneralservicelist.com/).
- Merges all into one Notion database with tags like `Lists = NGSL | NAWL | Spoken`.
- Records how many times you’ve seen or used each word.

### 💬 Output Practice Assistant
- Interactive command-line tool to help you **write and speak using new words**.
- Suggests keywords from your articles, provides common sentence patterns, and uses Gemini to give feedback on your sentences.
- Stores all feedback in Notion for review.

---

## 🚀 Quick Start

### 1. Requirements
- Python 3.11+
- Gemini API key and Notion integration token
- `.env` file (see below)

### 2. Setup
```bash
make venv
source .venv/bin/activate
make seed-ngsl seed-nawl seed-spoken     # import wordlists into Notion
make setup-notion PARENT=<notion_page_id> # auto-create Notion databases
