# -*- coding: utf-8 -*-
"""
Kiro IDE 会话适配器

Kiro IDE 将会话数据分散存储在两个位置：
1. workspace-sessions/<base64url编码的工作区>/<uuid>.json — 会话索引和用户消息
2. <profile_hash>/<model_hash>/<exec_hash> — agent 执行记录（包含完整回复）

存储根目录:
- Windows: %APPDATA%/Kiro/User/globalStorage/kiro.kiroagent/
- macOS:   ~/Library/Application Support/Kiro/User/globalStorage/kiro.kiroagent/
- Linux:   ~/.config/Kiro/User/globalStorage/kiro.kiroagent/
"""
from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def get_kiro_ide_base_dir() -> str:
    """获取 Kiro IDE globalStorage 根目录（跨平台）"""
    if sys.platform == 'win32':
        app_data = os.environ.get('APPDATA', os.path.expanduser('~/AppData/Roaming'))
        return os.path.join(app_data, 'Kiro', 'User', 'globalStorage', 'kiro.kiroagent')
    elif sys.platform == 'darwin':
        return os.path.expanduser(
            '~/Library/Application Support/Kiro/User/globalStorage/kiro.kiroagent')
    else:
        return os.path.expanduser(
            '~/.config/Kiro/User/globalStorage/kiro.kiroagent')


def get_kiro_ide_session_dir() -> str:
    """获取 Kiro IDE workspace-sessions 目录"""
    return os.path.join(get_kiro_ide_base_dir(), 'workspace-sessions')


def _decode_workspace_dir_name(encoded: str) -> str:
    """将 base64url 编码的目录名解码为工作区路径"""
    try:
        b64 = encoded.replace('_', '/').replace('-', '+')
        pad = (4 - len(b64) % 4) % 4
        b64 += '=' * pad
        return base64.b64decode(b64).decode('utf-8')
    except Exception:
        return encoded


@dataclass
class KiroIDESessionInfo:
    """Kiro IDE 会话信息"""
    session_id: str
    title: str
    date_created: float
    workspace_directory: str
    file_path: str
    hidden: bool = False


class KiroIDEAdapter:
    """Kiro IDE 会话适配器 — 合并 workspace-sessions 和 execution 文件"""

    def __init__(self, base_dir: str = None):
        self.base_dir = base_dir or get_kiro_ide_base_dir()
        self.session_dir = os.path.join(self.base_dir, 'workspace-sessions')
        self._exec_dirs = None  # 延迟加载

    def _get_exec_dirs(self) -> List[str]:
        """找到所有包含 execution 文件的目录"""
        if self._exec_dirs is not None:
            return self._exec_dirs

        self._exec_dirs = []
        skip = {'dev_data', 'index', 'workspace-sessions', 'default',
                '.diffs', '.migrations', '.utils'}
        for d1 in os.listdir(self.base_dir):
            if d1 in skip or d1.startswith('.'):
                continue
            d1_path = os.path.join(self.base_dir, d1)
            if not os.path.isdir(d1_path):
                continue
            for d2 in os.listdir(d1_path):
                d2_path = os.path.join(d1_path, d2)
                if not os.path.isdir(d2_path):
                    continue
                # Check if this dir has execution files (not subdirectories with code)
                sample = os.listdir(d2_path)[:5]
                has_exec = False
                for s in sample:
                    sp = os.path.join(d2_path, s)
                    if os.path.isfile(sp) and not s.startswith('.'):
                        try:
                            with open(sp, 'r', encoding='utf-8') as f:
                                peek = f.read(100)
                            if '"executionId"' in peek:
                                has_exec = True
                                break
                        except Exception:
                            continue
                if has_exec:
                    self._exec_dirs.append(d2_path)
        return self._exec_dirs

    def _load_executions_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        """加载某个 chatSessionId 对应的所有 execution 文件"""
        # 使用索引加速
        index = self._build_session_index()
        file_paths = index.get(session_id, [])

        executions = []
        for fpath in file_paths:
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                executions.append(data)
            except Exception:
                continue
        executions.sort(key=lambda x: x.get('startTime', 0))
        return executions

    def _build_session_index(self) -> Dict[str, List[str]]:
        """构建 chatSessionId -> [file_paths] 的索引（带缓存）"""
        if hasattr(self, '_session_index') and self._session_index is not None:
            return self._session_index

        index: Dict[str, List[str]] = {}
        for exec_dir in self._get_exec_dirs():
            for fname in os.listdir(exec_dir):
                fpath = os.path.join(exec_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                try:
                    # 只读取文件开头来提取 chatSessionId，避免解析整个大文件
                    with open(fpath, 'r', encoding='utf-8') as f:
                        head = f.read(2000)
                    # 快速提取 chatSessionId
                    marker = '"chatSessionId"'
                    pos = head.find(marker)
                    if pos < 0:
                        continue
                    # 找到值
                    colon = head.find(':', pos + len(marker))
                    quote1 = head.find('"', colon + 1)
                    quote2 = head.find('"', quote1 + 1)
                    if quote1 >= 0 and quote2 > quote1:
                        sid = head[quote1 + 1:quote2]
                        index.setdefault(sid, []).append(fpath)
                except Exception:
                    continue

        self._session_index = index
        return index

    def list_sessions(self) -> List[KiroIDESessionInfo]:
        """列出所有工作区下的所有会话"""
        if not os.path.isdir(self.session_dir):
            return []

        sessions = []
        for ws_dir_name in os.listdir(self.session_dir):
            ws_path = os.path.join(self.session_dir, ws_dir_name)
            if not os.path.isdir(ws_path):
                continue
            index_path = os.path.join(ws_path, 'sessions.json')
            if not os.path.isfile(index_path):
                continue
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)
            except Exception:
                continue

            workspace_dir = _decode_workspace_dir_name(ws_dir_name)

            for entry in index_data:
                sid = entry.get('sessionId', '')
                if not sid:
                    continue
                file_path = os.path.join(ws_path, f'{sid}.json')
                if not os.path.isfile(file_path):
                    continue

                date_created = float(entry.get('dateCreated', 0))
                if date_created > 1e12:
                    date_created = date_created / 1000.0

                sessions.append(KiroIDESessionInfo(
                    session_id=sid,
                    title=entry.get('title', ''),
                    date_created=date_created,
                    workspace_directory=workspace_dir,
                    file_path=file_path,
                    hidden=entry.get('hidden', False),
                ))

        sessions.sort(key=lambda s: s.date_created, reverse=True)
        return sessions

    def load_session_messages(self, file_path_or_session_id: str) -> List[Dict[str, Any]]:
        """加载会话的完整对话，合并 execution 文件中的 agent 回复。

        返回格式:
        [
            {"type": "user", "message": {"role": "user", "content": "..."}},
            {"type": "assistant", "message": {"role": "assistant", "content": "..."}},
            ...
        ]
        """
        # 确定 session_id 和 file_path
        if os.path.isfile(file_path_or_session_id):
            file_path = file_path_or_session_id
            session_id = os.path.splitext(os.path.basename(file_path))[0]
        else:
            session_id = file_path_or_session_id
            file_path = None

        # 从 execution 文件构建完整对话
        executions = self._load_executions_for_session(session_id)

        if not executions:
            # 回退：从 workspace-sessions JSON 读取（只有 "On it."）
            if file_path and os.path.isfile(file_path):
                return self._load_from_session_json(file_path)
            return []

        # 从 execution 文件构建对话
        lines = []
        seen_user_msgs = set()  # 去重

        for exec_data in executions:
            # 提取用户消息（从 input.data.messages 的最后一条 user 消息）
            messages = exec_data.get('input', {}).get('data', {}).get('messages', [])
            # 找到最后一条 user 消息（这是触发本次 execution 的用户输入）
            last_user = None
            for msg in reversed(messages):
                if msg.get('role') == 'user':
                    content = msg.get('content', [])
                    if isinstance(content, list):
                        texts = [c.get('text', '') for c in content
                                 if isinstance(c, dict) and c.get('type') == 'text']
                        text = '\n'.join(texts)
                    elif isinstance(content, str):
                        text = content
                    else:
                        text = ''
                    if text and text not in seen_user_msgs:
                        last_user = text
                    break

            if last_user:
                seen_user_msgs.add(last_user)
                lines.append({
                    'type': 'user',
                    'message': {'role': 'user', 'content': last_user},
                    '_kiro_exec_id': exec_data.get('executionId', ''),
                })

            # 提取 agent 回复（从 actions 中找 actionType=say）
            agent_reply_parts = []
            for action in exec_data.get('actions', []):
                if action.get('actionType') == 'say':
                    output = action.get('output', {})
                    if isinstance(output, dict):
                        msg_text = output.get('message', '')
                        if msg_text:
                            agent_reply_parts.append(msg_text)

            if agent_reply_parts:
                reply = '\n'.join(agent_reply_parts)
                lines.append({
                    'type': 'assistant',
                    'message': {'role': 'assistant', 'content': reply},
                    '_kiro_exec_id': exec_data.get('executionId', ''),
                    '_kiro_session_id': session_id,
                    '_kiro_idx': len(lines),
                })

        return lines

    def _load_from_session_json(self, file_path: str) -> List[Dict[str, Any]]:
        """从 workspace-sessions JSON 文件加载（回退方案，只有 On it.）"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        history = data.get('history', [])
        lines = []
        for idx, entry in enumerate(history):
            msg = entry.get('message', {})
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')
            if isinstance(content, list):
                texts = [item.get('text', '') for item in content
                         if isinstance(item, dict) and item.get('type') == 'text']
                content = '\n'.join(texts)
            lines.append({
                'type': role,
                'message': {'role': role, 'content': str(content)},
                '_kiro_idx': idx,
                '_kiro_file': file_path,
            })
        return lines

    def save_session_messages(self, file_path_or_session_id: str,
                              messages: List[Dict[str, Any]]) -> None:
        """将修改后的 assistant 消息写回 execution 文件"""
        if os.path.isfile(file_path_or_session_id):
            session_id = os.path.splitext(os.path.basename(file_path_or_session_id))[0]
        else:
            session_id = file_path_or_session_id

        executions = self._load_executions_for_session(session_id)
        if not executions:
            return

        # 建立 executionId -> execution data 的映射
        exec_map = {}
        for exec_data in executions:
            eid = exec_data.get('executionId', '')
            if eid:
                exec_map[eid] = exec_data

        # 找到被修改的 assistant 消息，写回对应的 execution 文件
        for msg in messages:
            if msg.get('type') != 'assistant':
                continue
            eid = msg.get('_kiro_exec_id', '')
            if not eid or eid not in exec_map:
                continue

            new_content = msg.get('message', {}).get('content', '')
            exec_data = exec_map[eid]

            # 更新 actions 中 actionType=say 的 output.message
            modified = False
            for action in exec_data.get('actions', []):
                if action.get('actionType') == 'say':
                    output = action.get('output', {})
                    if isinstance(output, dict) and output.get('message', '') != new_content:
                        output['message'] = new_content
                        modified = True

            if modified:
                # 找到这个 execution 文件并写回
                self._save_execution(exec_data)

    def _save_execution(self, exec_data: Dict[str, Any]) -> None:
        """将修改后的 execution 数据写回文件"""
        eid = exec_data.get('executionId', '')
        sid = exec_data.get('chatSessionId', '')
        # 使用索引找到文件
        index = self._build_session_index()
        for fpath in index.get(sid, []):
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    head = f.read(200)
                if eid in head:
                    with open(fpath, 'w', encoding='utf-8') as f:
                        json.dump(exec_data, f, ensure_ascii=False)
                    return
            except Exception:
                continue

    def backup_session(self, file_path_or_session_id: str) -> str:
        """备份会话相关的所有 execution 文件"""
        if os.path.isfile(file_path_or_session_id):
            session_id = os.path.splitext(os.path.basename(file_path_or_session_id))[0]
            # 也备份 session JSON
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup = f'{file_path_or_session_id}.{timestamp}.bak'
            shutil.copy2(file_path_or_session_id, backup)
        else:
            session_id = file_path_or_session_id
            backup = None

        # 备份 execution 文件
        executions = self._load_executions_for_session(session_id)
        for exec_data in executions:
            eid = exec_data.get('executionId', '')
            for exec_dir in self._get_exec_dirs():
                for fname in os.listdir(exec_dir):
                    fpath = os.path.join(exec_dir, fname)
                    if not os.path.isfile(fpath):
                        continue
                    try:
                        with open(fpath, 'r', encoding='utf-8') as f:
                            d = json.load(f)
                        if d.get('executionId') == eid:
                            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                            bak = f'{fpath}.{timestamp}.bak'
                            shutil.copy2(fpath, bak)
                            if backup is None:
                                backup = bak
                    except Exception:
                        continue

        return backup or 'no-backup'
