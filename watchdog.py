"""Watchdog process: monitor QQ.exe and main.py, restart on crash.

Runs independently of main.py.  Checks every 30 seconds.
On QQ restart, waits for auto-login; logs ERROR if manual login is needed.
"""

import logging
import os
import subprocess
import sys
import time

import psutil
import win32gui

# ---------------------------------------------------------------------------
# Configuration — adjust paths to match your deployment.
# ---------------------------------------------------------------------------
# Known QQ NT installation paths, tried in order when QQ is not running.
_KNOWN_QQ_PATHS = [
    r"C:\Program Files\Tencent\QQNT\QQ.exe",
    r"C:\Program Files (x86)\Tencent\QQNT\QQ.exe",
    r"C:\Program Files\Tencent\QQ\QQ.exe",
]
MONITOR_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_SCRIPT = os.path.join(MONITOR_DIR, "main.py")
CHECK_INTERVAL_SEC = 30
QQ_LOGIN_WAIT_SEC = 30

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [watchdog] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(
            os.path.join(MONITOR_DIR, "watchdog.log"), encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("watchdog")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_running(exe_name: str) -> bool:
    """Check if a process with the given executable name is running."""
    for p in psutil.process_iter(["name"]):
        try:
            if p.info["name"] == exe_name:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def _is_monitor_running() -> bool:
    """Check if main.py is running."""
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            info = p.info
            if info["name"] in ("python.exe", "pythonw.exe") and info["cmdline"]:
                if any("main.py" in arg for arg in info["cmdline"]):
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def _is_qq_logged_in() -> bool:
    """Check whether QQ is running AND showing a logged-in main window.

    A logged-in QQ window title contains "QQ" but not "登录" (login).
    """
    result = []

    def callback(hwnd: int, extra: list) -> bool:
        title = win32gui.GetWindowText(hwnd)
        if win32gui.IsWindowVisible(hwnd) and "QQ" in title and "登录" not in title:
            extra.append(hwnd)
        return True

    win32gui.EnumWindows(callback, result)
    return len(result) > 0


def _find_qq_exe() -> str | None:
    """Locate QQ.exe on this machine.

    First checks running QQ processes, then tries known install paths.
    """
    # Try to get the path from a running QQ process.
    for p in psutil.process_iter(["name", "exe"]):
        try:
            if p.info["name"] == "QQ.exe" and p.info["exe"]:
                return p.info["exe"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Fall back to known installation paths.
    for path in _KNOWN_QQ_PATHS:
        if os.path.exists(path):
            return path

    return None


def _start_qq() -> None:
    """Launch QQ.exe and wait for auto-login."""
    logger.warning("QQ.exe 未运行，正在启动...")
    qq_path = _find_qq_exe()
    if qq_path is None:
        logger.error(
            "找不到 QQ.exe。已搜索的路径: %s",
            ", ".join(_KNOWN_QQ_PATHS),
        )
        return
    try:
        subprocess.Popen(qq_path)
        logger.info("QQ.exe 已启动: %s", qq_path)
    except Exception:
        logger.error("启动 QQ.exe 失败: %s", qq_path, exc_info=True)
        return

    logger.info("等待 %ds 以便QQ自动登录...", QQ_LOGIN_WAIT_SEC)
    time.sleep(QQ_LOGIN_WAIT_SEC)

    if _is_qq_logged_in():
        logger.info("QQ 已成功登录")
    else:
        logger.error("QQ 已重启但未能自动登录，请运维人员手动登录QQ！")


def _start_monitor() -> None:
    """Launch main.py as a subprocess."""
    logger.warning("main.py 未运行，正在启动...")
    try:
        subprocess.Popen(
            [sys.executable, MAIN_SCRIPT],
            cwd=MONITOR_DIR,
        )
        logger.info("main.py 已启动")
    except Exception:
        logger.error("启动 main.py 失败", exc_info=True)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> None:
    logger.info("看门狗启动 — 监控 QQ.exe 和 main.py (间隔 %ds)", CHECK_INTERVAL_SEC)

    # Initial check: don't restart QQ if it was deliberately closed.
    # Only act after the first normal cycle.

    while True:
        try:
            if not _is_running("QQ.exe"):
                _start_qq()
            elif not _is_qq_logged_in():
                # QQ process exists but not logged in — might be mid-restart.
                logger.warning("QQ.exe 运行中但未检测到已登录窗口，可能处于登录界面")

            if not _is_monitor_running():
                _start_monitor()

        except Exception:
            logger.error("看门狗循环异常", exc_info=True)

        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
