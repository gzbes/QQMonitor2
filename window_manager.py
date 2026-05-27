"""Window management: find by title substring, restore/activate/focus via win32gui."""

import random
import time

import win32con
import win32gui


def find_window_by_title(substring: str) -> int | None:
    """Return the handle of the first visible window whose title contains `substring`.

    Returns None if no matching window is found.
    """

    def callback(hwnd: int, results: list[int]) -> bool:
        if win32gui.IsWindowVisible(hwnd) and substring in win32gui.GetWindowText(hwnd):
            results.append(hwnd)
        return True

    results: list[int] = []
    win32gui.EnumWindows(callback, results)
    return results[0] if results else None


def activate_window(hwnd: int) -> None:
    """Restore (if minimized) and bring a window to the foreground.

    A random jitter (0.2s–0.5s) is applied after activation to avoid
    timing-dependent race conditions.
    """
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(random.uniform(0.2, 0.5))
