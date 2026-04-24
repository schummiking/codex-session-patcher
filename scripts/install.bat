@echo off
REM Codex Session Patcher Windows 安装脚本

echo === Codex Session Patcher 安装脚本 ===

REM 检测 Python 版本
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未检测到 Python，请先安装 Python 3.8+
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYTHON_VERSION=%%v
echo 检测到 Python 版本: %PYTHON_VERSION%

REM 安装包
echo 正在安装 codex-session-patcher...
pip install -e .

if errorlevel 1 (
    echo 错误: 安装失败
    exit /b 1
)

echo.
echo === 安装完成 ===
echo.
echo 使用方法:
echo     codex-patcher              # 执行基本清洗
echo     codex-patcher --help       # 查看帮助
echo     codex-patcher --web        # 启动 Web UI
echo.
echo 如需 Web UI 功能，请运行:
echo     pip install -e ".[web]"
echo.
