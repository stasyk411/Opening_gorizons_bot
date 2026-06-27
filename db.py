# ВНИМАНИЕ: Схема БД хранится в core/db_schema_private.py (не в репозитории).
# При локальном запуске импортируется автоматически.

import sqlite3
import json
from datetime import datetime
from typing import Dict, Optional

try:
    from core.db_schema_private import init_db, DB_PATH
except ImportError:
    def init_db():
        raise RuntimeError("db_schema_private.py не найден. Бот не может работать без схемы БД.")
    DB_PATH = "bot_data.db"


# ============================================
# REPOSITORY ДЛЯ РАБОТЫ С КОНТЕКСТОМ
# ============================================

class UserRepository:
    """Класс для работы с контекстом пользователей в SQLite."""
    
    @staticmethod
    def _get_connection():
        """Создаёт и возвращает соединение с БД."""
        return sqlite3.connect(DB_PATH)
    
    @staticmethod
    def get_context(user_id: int) -> Optional[Dict]:
        """
        Получает контекст пользователя по user_id.
        
        Args:
            user_id: ID пользователя в Telegram
            
        Returns:
            Словарь с контекстом или None, если пользователь не найден
        """
        conn = UserRepository._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT context_json FROM users WHERE user_id = ?",
            (user_id,)
        )
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
        """
        Сохраняет или обновляет контекст пользователя.
        
        Args:
            user_id: ID пользователя в Telegram
            context_dict: Словарь с контекстом (messages, modules_loaded, current_level)
        """
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
        """
        Удаляет контекст пользователя (сброс сессии).
        
        Args:
            user_id: ID пользователя в Telegram
        """
        conn = UserRepository._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "DELETE FROM users WHERE user_id = ?",
            (user_id,)
        )
        
        conn.commit()
        conn.close()
    
    @staticmethod
    def log_event(user_id: int, event_type: str, metadata: dict = None) -> None:
        """
        Логирует событие пользователя.
        
        Args:
            user_id: ID пользователя
            event_type: Тип события (session_start, module_loaded, reset, message_sent)
            metadata: Дополнительные данные (JSON-сериализуемый словарь)
        """
        conn = UserRepository._get_connection()
        cursor = conn.cursor()
        
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        
        cursor.execute(
            "INSERT INTO events (user_id, event_type, metadata) VALUES (?, ?, ?)",
            (user_id, event_type, metadata_json)
        )
        
        conn.commit()
        conn.close()


# ============================================
# ИНИЦИАЛИЗАЦИЯ ПРИ ИМПОРТЕ
# ============================================

init_db()
