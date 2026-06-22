import os
import time
import json
import base64
import shutil
import sqlite3
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
BASE_FOLDER = Path(os.getenv("BASE_FOLDER", "./정리된자료"))
DB_PATH = os.getenv("DB_PATH", "./index.db")
POLL_INTERVAL = 5

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GEMINI_API = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
)

INBOX = Path("./inbox")
INBOX.mkdir(exist_ok=True)
BASE_FOLDER.mkdir(exist_ok=True)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """ss (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            category TEXT,
            summary TEXT,
            source_text TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def get_existing_categories():
    if not BASE_FOLDER.exists():
        return []
    return [p.name for p in BASE_FOLDER.iterdir() if p.is_dir()]


def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    resp = requests.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=40)
    resp.raise_for_status()
    return resp.json().get("result", [])


def download_telegram_file(file_id, dest_path):
    file_info = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}).json()
    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    r = requests.get(file_url)
    dest_path.write_bytes(r.content)
    return dest_path


def classify_with_gemini(text_content, filename, categories, image_bytes=None, mime_type=None):
    categories_str = ", ".join(categories) if categories else "(아직 없음)"
    prompt = f"""너는 파일 정리 비서야. 아래 자료를 보고 분류해줘.
이미지가 첨부되어 있다면, 이미지 내용을 직접 보고 판단해.

기존 폴더 목록: {categories_str}

파일명: {filename}
내용 일부(텍스트/캡션): {text_content[:2000]}

다음 JSON 형식으로만 답해. JSON 외 다른 텍스트는 절대 포함하지 마.
{{
  "category": "폴더명 (기존 폴더 중 적절한 게 있으면 그걸 쓰고, 없으면 새 이름 제안)",
  "summary": "한 줄 요약 (20자 이내)",
  "tags": ["태그1", "태그2"]
}}"""
    parts = [{"text": prompt}]
    if image_bytes:
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        parts.append({"inline_data": {"mime_type": mime_type or "image/jpeg", "data": encoded}})

    body = {"contents": [{"parts": parts}]}
    resp = requests.post(GEMINI_API, json=body, timeout=60)
    resp.raise_for_status()
    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def process_message(msg, conn):
    text_content = ""
    filename = None
    file_path = None
    image_bytes = None
    mime_type = None

    if "document" in msg:
        doc = msg["document"]
        filename = doc["file_name"]
        file_path = INBOX / filename
        download_telegram_file(doc["file_id"], file_path)
        text_content = filename
    elif "photo" in msg:
        photo = msg["photo"][-1]
        filename = f"photo_{photo['file_id']}.jpg"
        file_path = INBOX / filename
        download_telegram_file(photo["file_id"], file_path)
        text_content = msg.get("caption", "")
        image_bytes = file_path.read_bytes()
        mime_type = "image/jpeg"
    elif "text" in msg:
        filename = f"note_{msg['message_id']}.txt"
        file_path = INBOX / filename
        text_content = msg["text"]
        file_path.write_text(text_content, encoding="utf-8")
    else:
        return

    categories = get_existing_categories()
    try:
        result = classify_with_gemini(text_content, filename, categories, image_bytes, mime_type)
    except Exception as e:
        print(f"분류 실패: {e}")
        result = {"category": "미분류", "summary": filename, "tags": []}

    category = result.get("category", "미분류")
    summary = result.get("summary", "")
    dest_dir = BASE_FOLDER / category
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename
    shutil.move(str(file_path), str(dest_path))

    conn.execute(
        "INSERT INTO items (filename, category, summary, source_text, created_at) VALUES (?, ?, ?, ?, ?)",
        (filename, category, summary, text_content[:500], datetime.now().isoformat()),
    )
    conn.commit()
    print(f"[정리완료] {filename} -> {category}/ ({summary})")

    send_telegram_message(msg["chat"]["id"], f"정리했어요: {category}/{filename}\n{summary}")


def send_telegram_message(chat_id, text):
    requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text})


def main():
    if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
        print("TELEGRAM_BOT_TOKEN과 GEMINI_API_KEY를 .env 파일에 설정해주세요.")
        return

    conn = init_db()
    offset = None
    print("자료 정리 에이전트 시작! 텔레그램으로 파일을 보내보세요. (종료: Ctrl+C)")

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update:
                    process_message(update["message"], conn)
        except Exception as e:
            print(f"오류 발생: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()