import json
import os
import sqlite3
from datetime import datetime, timedelta

from loguru import logger


class ChatContextManager:
    """
    SQLite-backed chat context and small bookkeeping tables.

    Open-source note:
    - The database file is local-only (default: data/chat_history.db).
    - Do not commit the DB to git.
    """

    def __init__(self, max_history: int = 100, db_path: str = "data/chat_history.db"):
        self.max_history = max_history
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            chat_id TEXT
        )
        """
        )

        cursor.execute("PRAGMA table_info(messages)")
        columns = [column[1] for column in cursor.fetchall()]
        if "chat_id" not in columns:
            cursor.execute("ALTER TABLE messages ADD COLUMN chat_id TEXT")
            logger.info("added chat_id column to messages table")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_item ON messages (user_id, item_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON messages (chat_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages (timestamp)")

        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS chat_bargain_counts (
            chat_id TEXT PRIMARY KEY,
            count INTEGER DEFAULT 0,
            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
        )

        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS items (
            item_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            price REAL,
            description TEXT,
            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
        )

        # Optional: order bookkeeping (kept for compatibility; not required for core loop).
        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS active_orders (
            chat_id TEXT PRIMARY KEY,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
        )

        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS browser_unread_fingerprints (
            fingerprint TEXT PRIMARY KEY,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
        )

        conn.commit()
        conn.close()
        logger.info(f"db initialized: {self.db_path}")

    def save_item_info(self, item_id: str, item_data: dict) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            price = float(item_data.get("soldPrice", 0))
            description = item_data.get("desc", "")
            data_json = json.dumps(item_data, ensure_ascii=False)

            now = datetime.now().isoformat()
            cursor.execute(
                """
                INSERT INTO items (item_id, data, price, description, last_updated)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(item_id)
                DO UPDATE SET data = ?, price = ?, description = ?, last_updated = ?
                """,
                (item_id, data_json, price, description, now, data_json, price, description, now),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"save_item_info error: {e}")
            conn.rollback()
        finally:
            conn.close()

    def get_item_info(self, item_id: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT data FROM items WHERE item_id = ?", (item_id,))
            result = cursor.fetchone()
            return json.loads(result[0]) if result else None
        except Exception as e:
            logger.error(f"get_item_info error: {e}")
            return None
        finally:
            conn.close()

    def add_message_by_chat(self, chat_id: str, user_id: str, item_id: str, role: str, content: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO messages (user_id, item_id, role, content, timestamp, chat_id) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, item_id, role, content, datetime.now().isoformat(), chat_id),
            )

            cursor.execute(
                """
                SELECT id FROM messages
                WHERE chat_id = ?
                ORDER BY timestamp DESC
                LIMIT ?, 1
                """,
                (chat_id, self.max_history),
            )
            oldest_to_keep = cursor.fetchone()
            if oldest_to_keep:
                cursor.execute("DELETE FROM messages WHERE chat_id = ? AND id < ?", (chat_id, oldest_to_keep[0]))
            conn.commit()
        except Exception as e:
            logger.error(f"add_message_by_chat error: {e}")
            conn.rollback()
        finally:
            conn.close()

    def get_context_by_chat(self, chat_id: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT role, content FROM messages
                WHERE chat_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (chat_id, self.max_history),
            )
            messages = [{"role": role, "content": content} for role, content in cursor.fetchall()]
            bargain_count = self.get_bargain_count_by_chat(chat_id)
            if bargain_count > 0:
                messages.append({"role": "system", "content": f"bargain_count: {bargain_count}"})
        except Exception as e:
            logger.error(f"get_context_by_chat error: {e}")
            messages = []
        finally:
            conn.close()
        return messages

    def increment_bargain_count_by_chat(self, chat_id: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            now = datetime.now().isoformat()
            cursor.execute(
                """
                INSERT INTO chat_bargain_counts (chat_id, count, last_updated)
                VALUES (?, 1, ?)
                ON CONFLICT(chat_id)
                DO UPDATE SET count = count + 1, last_updated = ?
                """,
                (chat_id, now, now),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"increment_bargain_count_by_chat error: {e}")
            conn.rollback()
        finally:
            conn.close()

    def get_bargain_count_by_chat(self, chat_id: str) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT count FROM chat_bargain_counts WHERE chat_id = ?", (chat_id,))
            result = cursor.fetchone()
            return int(result[0]) if result else 0
        except Exception as e:
            logger.error(f"get_bargain_count_by_chat error: {e}")
            return 0
        finally:
            conn.close()

    def mark_chat_as_ordered(self, chat_id: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT OR REPLACE INTO active_orders (chat_id, timestamp) VALUES (?, ?)",
                (chat_id, datetime.now().isoformat()),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"mark_chat_as_ordered error: {e}")
            conn.rollback()
        finally:
            conn.close()

    def is_chat_recently_ordered(self, chat_id: str, duration_seconds: int = 86400) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT timestamp FROM active_orders WHERE chat_id = ?", (chat_id,))
            result = cursor.fetchone()
            if not result:
                return False
            order_time = datetime.fromisoformat(result[0])
            return datetime.now() - order_time < timedelta(seconds=duration_seconds)
        except Exception as e:
            logger.error(f"is_chat_recently_ordered error: {e}")
            return False
        finally:
            conn.close()

    def is_browser_unread_seen(self, fingerprint: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1 FROM browser_unread_fingerprints WHERE fingerprint = ?", (fingerprint,))
            return cursor.fetchone() is not None
        finally:
            conn.close()

    def mark_browser_unread_seen(self, fingerprint: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO browser_unread_fingerprints (fingerprint) VALUES (?)",
                (fingerprint,),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"mark_browser_unread_seen error: {e}")
            conn.rollback()
        finally:
            conn.close()

