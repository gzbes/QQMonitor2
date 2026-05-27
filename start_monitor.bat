@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  QQ群聊产品型号监控系统 — 启动中...
echo ============================================

REM Start the watchdog (which auto-starts main.py if needed)
start "QQMonitor-Watchdog" /min pythonw.exe watchdog.py

echo 看门狗已启动（最小化窗口）
echo 监控程序将在看门狗检测后自动启动
echo.
echo 使用 stop_monitor.bat 停止所有进程
