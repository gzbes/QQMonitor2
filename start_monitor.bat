@echo off
chcp 65001 >nul
cd /d "%~dp0"

title QQ群聊产品型号监控系统

echo ============================================
echo  QQ群聊产品型号监控系统
echo ============================================
echo.
echo 正在启动监控服务...
echo.
echo   监控日志: logs\monitor.log
echo   看门狗日志: watchdog.log
echo.
echo ============================================
echo  按 Ctrl+C 或关闭此窗口将停止所有监控服务
echo ============================================
echo.

python.exe watchdog.py

echo.
echo 监控服务已退出，正在清理残留进程...
call "%~dp0stop_monitor.bat" /q
echo.
echo 所有服务已停止，按任意键关闭此窗口...
pause >nul
