import sqlite3
import json
from datetime import datetime
from typing import Dict, Optional
import os

# ============================================
# ИНИЦИАЛИЗАЦИЯ БД (ИСПРАВЛЕННАЯ)
# ============================================

DB_PATH = "bot_data.db"

def _ensure_analytics_tables(db_path: str):
    """Создаёт таблицы аналитики V1. Вызывается ВСЕГДА, даже при наличии приватной схемы."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS funnel_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            metadata TEXT,
            created_at TEXT NOT NULL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS llm_hypotheses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            hypothesis_type TEXT NOT NULL,
            value TEXT NOT NULL,
            confidence REAL,
            context_ep TEXT,
            created_at TEXT NOT NULL
        )
    """)
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_funnel_user ON funnel_events(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_funnel_type ON funnel_events(event_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hyp_user ON llm_hypotheses(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hyp_type ON llm_hypotheses(hypothesis_type)")
    
    conn.commit()
    conn.close()

# Инициализация основной схемы
try:
    from core.db_schema_private import init_db as private_init_db, DB_PATH as PRIVATE_DB_PATH
    DB_PATH = PRIVATE_DB_PATH
    private_init_db()
    print("✅ Используется приватная схема БД")
except ImportError:
    # Fallback: создаём базовые таблицы самостоятельно
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            context_json TEXT,
            updated_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            event_type TEXT,
            metadata TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    print("⚠️ Приватная схема не найдена. Используется встроенная схема V1.")

# КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: Создаём таблицы аналитики ВСЕГДА
_ensure_analytics_tables(DB_PATH)
print("✅ Таблицы аналитики V1 проверены/созданы")

# ============================================
# REPOSITORY
# ============================================

class UserRepository:
    """Класс для работы с контекстом и аналитикой пользователей."""
    
    @staticmethod
    def _get_connection():
        return sqlite3.connect(DB_PATH)
    
    # === КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ ===
    
    @staticmethod
    def get_context(user_id: int) -> Optional[Dict]:
        conn = UserRepository._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT context_json FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result and result[0]:
            try:
                return json.loads(result[0])
            except json.JSONDecodeError:
                return None
        return None
    
    @staticmethod
    def save_context(user_id: int, context_dict: Dict) -> None:
        conn = UserRepository._get_connection()
        cursor = conn.cursor()
        context_json = json.dumps(context_dict, ensure_ascii=False)
        updated_at = datetime.now().isoformat()
        
        cursor.execute("""
            INSERT INTO users (user_id, context_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                context_json = excluded.context_json,
                updated_at = excluded.updated_at
        """, (user_id, context_json, updated_at))
        conn.commit()
        conn.close()
    
    @staticmethod
    def reset_context(user_id: int) -> None:
        conn = UserRepository._get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    
    # === СТАРЫЙ ЛОГЕР (для обратной совместимости) ===
    
    @staticmethod
    def log_event(user_id: int, event_type: str, metadata: dict = None) -> None:
        conn = UserRepository._get_connection()
        cursor = conn.cursor()
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        cursor.execute(
            "INSERT INTO events (user_id, event_type, metadata) VALUES (?, ?, ?)",
            (user_id, event_type, metadata_json)
        )
        conn.commit()
        conn.close()
    
    # === НОВАЯ АНАЛИТИКА V1: ОБЪЕКТИВНАЯ ВОРОНКА ===
    
    @staticmethod
    def log_funnel_event(user_id: int, event_type: str, metadata: dict = None) -> None:
        """
        Логирует объективное событие воронки.
        Типы: session_start, ep_selected, first_message_sent, dialog_completed, user_returned
        """
        conn = UserRepository._get_connection()
        cursor = conn.cursor()
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        created_at = datetime.now().isoformat()
        
        cursor.execute(
            "INSERT INTO funnel_events (user_id, event_type, metadata, created_at) VALUES (?, ?, ?, ?)",
            (user_id, event_type, metadata_json, created_at)
        )
        conn.commit()
        conn.close()
    
    # === НОВАЯ АНАЛИТИКА V1: ГИПОТЕЗЫ LLM ===
    
    @staticmethod
    def log_llm_hypothesis(
        user_id: int, 
        hypothesis_type: str, 
        value: str, 
        confidence: float = None, 
        context_ep: str = None
    ) -> None:
        """
        Логирует вероятностную гипотезу LLM.
        Типы: scene_classification, emotion_detection, intent_guess
        """
        conn = UserRepository._get_connection()
        cursor = conn.cursor()
        created_at = datetime.now().isoformat()
        
        cursor.execute(
            """INSERT INTO llm_hypotheses 
               (user_id, hypothesis_type, value, confidence, context_ep, created_at) 
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, hypothesis_type, value, confidence, context_ep, created_at)
        )
        conn.commit()
        conn.close()