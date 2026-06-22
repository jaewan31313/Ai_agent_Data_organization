import sqlite3
import sys
import os
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./index.db")


def search(query):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        """
        SELECT filename, category, summary, created_at FROM items
        WHERE filename LIKE ? OR summary LIKE ? OR source_text LIKE ?
        ORDER BY created_at DESC
        """,
        (f"%{query}%", f"%{query}%", f"%{query}%"),
    )
    rows = cur.fetchall()
    if not rows:
        print("결과 없음")
        return
    for filename, category, summary, created_at in rows:
        print(f"[{category}] {filename} - {summary} ({created_at})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python search.py 검색어")
    else:
        search(" ".join(sys.argv[1:]))
