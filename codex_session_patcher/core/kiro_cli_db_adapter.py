# -*- coding: utf-8 -*-
"""
Kiro CLI SQLite 适配器

Kiro CLI 将会话存储在 SQLite 数据库中。

数据库路径:
- Windows: %LOCALAPPDATA%/kiro-cli/data.sqlite3
- macOS:   ~/.local/share/kiro-cli/data.sqlite3
- Linux:   ~/.local/share/kiro-cli/data.sqlite3

Schema (conversations_v2):
- key: 工作目录路径
- conversation_id: UUID
- value: JSON 字符串，包含 history 数组
- created_at: 毫秒时间戳
- updated_at: 毫秒时间戳
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def get_kiro_cli_db_path() -> str:
    """获取 Kiro CLI 数据库路径（跨平台）"""
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA', os.path.expanduser('~/AppData/Local'))
    else:
        base = os.path.expanduser('~/.local/share')
    return os.path.join(base, 'kiro-cli', 'data.sqlite3')


class KiroCliDBAdapter:
    """Kiro CLI SQLite 数据库适配器"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or get_kiro_cli_db_path()

    def _connect(self, readonly: bool = True) -> sqlite3.Connection:
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Kiro CLI 数据库不存在: {self.db_path}")
        if readonly:
            conn = sqlite3.connect(f'file:{self.db_path}?mode=ro', uri=True)
        else:
            conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def list_sessions(self) -> List[Dict[str, Any]]:
        """列出所有会话"""
        conn = self._connect(readonly=True)
        try:
            # 优先使用 v2 表
            cursor = conn.execute(
                "SELECT key, conversation_id, value, created_at, updated_at "
                "FROM conversations_v2 ORDER BY updated_at DESC"
            )
            sessions = []
            for row in cursor:
                updated_at = row['updated_at']
                if updated_at > 1e12:
                    updated_at = updated_at / 1000.0
                created_at = row['created_at']
                if created_at > 1e12:
                    created_at = created_at / 1000.0

                dt = datetime.fromtimestamp(updated_at)
                sessions.append({
                    'session_id': row['conversation_id'],
                    'directory': row['key'],
                    'mtime': updated_at,
                    'mtime_str': dt.strftime('%Y-%m-%d %H:%M:%S'),
                    'date': dt.strftime('%Y-%m-%d'),
                    '_raw_value': row['value'],
                })
            return sessions
        finally:
            conn.close()

    def load_session_messages(self, conversation_id: str) -> List[Dict[str, Any]]:
        """加载会话消息，转换为管道可处理的 dict 列表。

        Kiro CLI history 格式:
        {"conversation_id":"...","history":[
            {"user":{"content":{"Prompt":{"prompt":"..."}}}},
            {"assistant":{"message_id":"...","content":[{"Text":"..."},{"ToolUse":{...}}]}},
            ...
        ]}

        转换为:
        [
            {"type":"assistant","message":{"role":"assistant","content":"..."},"_kiro_cli_idx":1},
            ...
        ]
        """
        conn = self._connect(readonly=True)
        try:
            cursor = conn.execute(
                "SELECT value FROM conversations_v2 WHERE conversation_id = ?",
                (conversation_id,)
            )
            row = cursor.fetchone()
            if not row:
                return []

            data = json.loads(row['value'])
            history = data.get('history', [])

            lines = []
            for idx, entry in enumerate(history):
                if 'user' in entry:
                    user_data = entry['user']
                    content = user_data.get('content', {})
                    if isinstance(content, dict):
                        prompt = content.get('Prompt', {})
                        text = prompt.get('prompt', '')
                    else:
                        text = str(content)
                    lines.append({
                        'type': 'user',
                        'message': {'role': 'user', 'content': text},
                        '_kiro_cli_idx': idx,
                    })
                elif 'assistant' in entry:
                    asst_data = entry['assistant']
                    content_parts = asst_data.get('content', [])
                    texts = []
                    for part in content_parts:
                        if isinstance(part, dict):
                            if 'Text' in part:
                                texts.append(part['Text'])
                            elif isinstance(part.get('text'), str):
                                texts.append(part['text'])
                    text = '\n'.join(texts)
                    lines.append({
                        'type': 'assistant',
                        'message': {'role': 'assistant', 'content': text},
                        '_kiro_cli_idx': idx,
                        '_kiro_cli_msg_id': asst_data.get('message_id', ''),
                    })

            return lines
        finally:
            conn.close()

    def save_session_messages(self, conversation_id: str, messages: List[Dict[str, Any]]) -> int:
        """将修改后的消息写回数据库"""
        conn = self._connect(readonly=False)
        try:
            cursor = conn.execute(
                "SELECT value FROM conversations_v2 WHERE conversation_id = ?",
                (conversation_id,)
            )
            row = cursor.fetchone()
            if not row:
                return 0

            data = json.loads(row['value'])
            history = data.get('history', [])
            updated = 0

            for msg in messages:
                idx = msg.get('_kiro_cli_idx')
                if idx is None or idx >= len(history):
                    continue
                if msg.get('type') != 'assistant':
                    continue

                new_text = msg.get('message', {}).get('content', '')
                entry = history[idx]
                if 'assistant' not in entry:
                    continue

                asst = entry['assistant']
                content_parts = asst.get('content', [])
                for part in content_parts:
                    if isinstance(part, dict) and 'Text' in part:
                        if part['Text'] != new_text:
                            part['Text'] = new_text
                            updated += 1
                        break

            if updated > 0:
                conn.execute(
                    "UPDATE conversations_v2 SET value = ?, updated_at = ? WHERE conversation_id = ?",
                    (json.dumps(data, ensure_ascii=False), int(datetime.now().timestamp() * 1000), conversation_id)
                )
                conn.commit()

            return updated
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def backup_database(self) -> str:
        """备份数据库"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = f'{self.db_path}.{timestamp}.bak'
        shutil.copy2(self.db_path, backup_path)
        return backup_path
