# -*- coding: utf-8 -*-
"""
CTF 提示词 CRUD 测试
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest


class TestCTFPromptTemplates:
    """验证模板内容基本正确"""

    def test_codex_template_exists(self):
        from codex_session_patcher.ctf_config.templates import SECURITY_MODE_PROMPT
        assert 'CTF' in SECURITY_MODE_PROMPT
        assert len(SECURITY_MODE_PROMPT) > 100

    def test_claude_template_exists(self):
        from codex_session_patcher.ctf_config.templates import CLAUDE_CODE_SECURITY_MODE_PROMPT
        assert 'managed-by: codex-session-patcher:ctf' in CLAUDE_CODE_SECURITY_MODE_PROMPT

    def test_opencode_template_exists(self):
        from codex_session_patcher.ctf_config.templates import OPENCODE_SECURITY_MODE_PROMPT
        assert 'managed-by: codex-session-patcher:ctf' in OPENCODE_SECURITY_MODE_PROMPT
        assert '# Security Testing Mode' in OPENCODE_SECURITY_MODE_PROMPT

    def test_opencode_config_is_valid_json(self):
        from codex_session_patcher.ctf_config.templates import OPENCODE_CTF_CONFIG
        data = json.loads(OPENCODE_CTF_CONFIG)
        assert 'instructions' in data
        assert 'AGENTS.md' in data['instructions']

    def test_opencode_readme_exists(self):
        from codex_session_patcher.ctf_config.templates import OPENCODE_CTF_README
        assert 'opencode' in OPENCODE_CTF_README.lower()
        assert 'codex-patcher' in OPENCODE_CTF_README


class TestCustomPromptParameter:
    """验证 install() 方法的 custom_prompt 参数"""

    def test_codex_installer_accepts_custom_prompt(self, tmp_path):
        from codex_session_patcher.ctf_config.installer import CTFConfigInstaller

        installer = CTFConfigInstaller()
        installer.codex_dir = str(tmp_path / ".codex")
        installer.config_path = os.path.join(installer.codex_dir, "config.toml")
        installer.prompts_dir = os.path.join(installer.codex_dir, "prompts")

        custom = "# My Custom Codex Prompt"
        success, _ = installer.install(custom_prompt=custom)
        assert success

        # install() 写入的文件由 _get_prompt_file() 决定，默认为 ctf_optimized.md
        prompt_file = installer._get_prompt_file()
        actual_path = os.path.join(installer.prompts_dir, prompt_file)
        with open(actual_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert content == custom

    def test_codex_installer_uses_default_without_custom(self, tmp_path):
        from codex_session_patcher.ctf_config.installer import CTFConfigInstaller

        installer = CTFConfigInstaller()
        installer.codex_dir = str(tmp_path / ".codex")
        installer.config_path = os.path.join(installer.codex_dir, "config.toml")
        installer.prompts_dir = os.path.join(installer.codex_dir, "prompts")

        success, _ = installer.install()
        assert success

        # install() 写入的文件由 _get_prompt_file() 决定，默认为 ctf_optimized.md
        prompt_file = installer._get_prompt_file()
        actual_path = os.path.join(installer.prompts_dir, prompt_file)
        with open(actual_path, 'r', encoding='utf-8') as f:
            content = f.read()
        # 默认内容应来自 BUILTIN_TEMPLATES 中标记为 default 的模板
        assert len(content) > 100

    def test_claude_installer_accepts_custom_prompt(self, tmp_path):
        from codex_session_patcher.ctf_config.installer import ClaudeCodeCTFInstaller

        installer = ClaudeCodeCTFInstaller()
        installer.workspace_dir = str(tmp_path / "claude-ctf")
        installer.claude_dir = os.path.join(installer.workspace_dir, ".claude")
        installer.prompt_path = os.path.join(installer.claude_dir, "CLAUDE.md")
        installer.readme_path = os.path.join(installer.workspace_dir, "README.md")

        custom = "# My Custom Claude Prompt"
        success, _ = installer.install(custom_prompt=custom)
        assert success

        with open(installer.prompt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert content == custom


class TestCTFStatus:
    """验证 CTFStatus 包含 OpenCode 字段"""

    def test_status_has_opencode_fields(self):
        from codex_session_patcher.ctf_config.status import CTFStatus
        status = CTFStatus()
        assert hasattr(status, 'opencode_installed')
        assert hasattr(status, 'opencode_workspace_exists')
        assert hasattr(status, 'opencode_prompt_exists')
        assert hasattr(status, 'opencode_workspace_path')
        assert hasattr(status, 'opencode_prompt_path')
        assert status.opencode_installed is False
