@echo off
chcp 65001 >nul

echo ============================================
echo  QQ群聊产品型号监控系统 — 停止中...
echo ============================================

echo 停止 watchdog.py ...
taskkill /f /fi "WINDOWTITLE eq QQMonitor-Watchdog" 2>nul
for /f "tokens=2" %%a in ('tasklist /fi "IMAGENAME eq python.exe" /fo list ^| findstr /i "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /i "watchdog.py" >nul
    if not errorlevel 1 taskkill /f /pid %%a 2>nul
)
for /f "tokens=2" %%a in ('tasklist /fi "IMAGENAME eq pythonw.exe" /fo list ^| findstr /i "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /i "watchdog.py" >nul
    if not errorlevel 1 taskkill /f /pid %%a 2>nul
)

echo 停止 main.py ...
for /f "tokens=2" %%a in ('tasklist /fi "IMAGENAME eq python.exe" /fo list ^| findstr /i "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /i "main.py" >nul
    if not errorlevel 1 taskkill /f /pid %%a 2>nul
)
for /f "tokens=2" %%a in ('tasklist /fi "IMAGENAME eq pythonw.exe" /fo list ^| findstr /i "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /i "main.py" >nul
    if not errorlevel 1 taskkill /f /pid %%a 2>nul
)

echo.
echo 所有监控进程已停止
pause
