# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

QQ Group Chat Product Model Monitoring System — a Windows desktop app that monitors QQ group messages via UI automation (keyboard/mouse simulation, no protocol hacking) and notifies a contact when messages match product model numbers from a CSV.

Target platform: Windows Server 2022 + QQ NT 9.9.9+ + Python 3.14.5.

## Commands

```bash
# Install dependencies (once)
pip install pywinauto pywin32 pynput psutil

# Run the monitor
python main.py

# Run the watchdog (keeps QQ.exe and main.py alive)
python watchdog.py

# Start via batch script (production)
start_monitor.bat

# Stop (production)
stop_monitor.bat
```

There is no test suite or linter configured yet. The design doc prescribes manual verification.

## Architecture

```
main.py          — Config loading, infinite polling loop, orchestrates all modules
window_manager.py — Find windows by title substring, restore/activate/focus (win32gui)
qq_automation.py  — pywinauto UIA backend: copy chat text (Ctrl+A/Ctrl+C), send messages to contacts
message_parser.py — Parse clipboard text into structured messages (sender, time, content)
tracker.py        — Incremental dedup via MD5 fingerprints (sender+time+first100chars)
matcher.py        — Load CSV model list, case-insensitive substring matching
notifier.py       — Cooldown-per-(group,model), dispatch via QQAutomation
archiver.py       — Append new messages to daily CSV files (messages_YYYY-MM-DD.csv)
watchdog.py       — Monitor QQ.exe and main.py via psutil, restart if missing
```

**Data flow per polling cycle:** Activate group window → End (scroll to bottom) → Ctrl+A, Ctrl+C → Read clipboard → Parse messages → Diff fingerprints (incremental) → Archive new messages → Match models → Notify (with cooldown).

## Key Constraints

- **No QQ protocol reverse-engineering or memory injection.** All interaction is simulated UI operations.
- **No QQ credentials stored.** Operator manually logs in before starting the monitor.
- QQ windows can be minimized to taskbar but NOT to system tray.
- Poll interval defaults to 60s. End-to-end latency target ≤ 60s.
- CPU idle ≤ 5%, peak ≤ 20%. Memory ≤ 200MB.

## Configuration

`config.json` (JSON, loaded at startup, no hot-reload):

```json
{
    "groups": [{"name": "群名称", "number": "群号"}],
    "target_contact": "联系人名称",
    "poll_interval_seconds": 60,
    "product_csv_path": "D:\\monitor\\products.csv",
    "archive_dir": "D:\\monitor\\archives",
    "log_dir": "D:\\monitor\\logs",
    "cooldown_sec": 30,
    "match_case_sensitive": false
}
```

Product CSV format (UTF-8-BOM or GBK): header row `型号,库存量`, one model per row. Models are matched as case-insensitive substrings against message content.

## Design Document

The full requirements spec and implementation blueprint is in [Requirment.md](Requirment.md). It contains detailed module pseudocode, error handling strategy, deployment procedures, and remote desktop keep-alive configuration.
