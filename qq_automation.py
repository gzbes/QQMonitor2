"""QQ UI automation via pywinauto UIA backend: copy chat, send messages."""

import logging
import random
import re
import time

import psutil
import win32clipboard
from pywinauto import Application
from pywinauto.keyboard import send_keys

from window_manager import activate_window, find_window_by_title

logger = logging.getLogger(__name__)

# Known QQ NT installation paths (tried in order when QQ is not running).
_KNOWN_QQ_PATHS = [
    r"C:\Program Files\Tencent\QQNT\QQ.exe",
    r"C:\Program Files (x86)\Tencent\QQNT\QQ.exe",
    r"C:\Program Files\Tencent\QQ\QQ.exe",
]


class QQAutomation:
    """Connect to a running QQ NT process and automate chat operations."""

    def __init__(self, config: dict):
        selectors = config.get("ui_selectors", {})
        self._main_window_pattern = selectors.get("main_window_title_pattern", ".*QQ.*")
        self._msg_list_sel = selectors.get("message_list", {"auto_id": "message_list", "control_type": "List"})
        self._msg_area_fallback = selectors.get("message_area_fallback", {"class_name": "ChatWnd"})
        self._input_edit_sel = selectors.get("input_edit", {"auto_id": "input_edit", "control_type": "Edit"})
        self._search_box_sel = selectors.get("search_box", {"auto_id": "search_box", "control_type": "Edit"})
        self._contact_item_sel = selectors.get("contact_result_item", {"title": None, "control_type": "ListItem"})
        self._fallback_click = selectors.get("fallback_to_center_click", True)
        self._retry_attempts = config.get("retry_attempts", 3)
        self._retry_delay = config.get("retry_delay_sec", 0.5)

        logger.info("Connecting to QQ process (UIA backend, timeout=15s)...")
        self.app = self._connect_to_qq()
        logger.info("Connected to QQ process")

    # ------------------------------------------------------------------
    # Connection strategies
    # ------------------------------------------------------------------

    def _connect_to_qq(self) -> Application:
        """Connect to QQ via UIA using multiple fallback strategies.

        Strategy order:
        1. Exact title match "QQ" — most reliable (avoids ambiguity with
           other windows that contain "QQ" in their title like VS Code).
        2. Find QQ.exe PID via psutil, connect by process.
        3. Configured title_re pattern from config.jsonc (last resort).
        """
        # Strategy 1: exact title match
        try:
            app = Application(backend="uia").connect(title="QQ", timeout=10)
            logger.debug("Connected via exact title 'QQ'")
            return app
        except Exception as e:
            logger.debug("Exact title 'QQ' failed: %s", e)

        # Strategy 2: connect by process (find QQ.exe PID)
        qq_pid = self._find_qq_pid()
        if qq_pid is not None:
            try:
                app = Application(backend="uia").connect(process=qq_pid, timeout=10)
                logger.debug("Connected via process (PID=%d)", qq_pid)
                return app
            except Exception as e:
                logger.debug("Process connect failed (PID=%d): %s", qq_pid, e)

        # Strategy 3: configured title_re pattern
        try:
            app = Application(backend="uia").connect(
                title_re=self._main_window_pattern, timeout=10
            )
            logger.debug("Connected via title_re pattern: %s", self._main_window_pattern)
            return app
        except Exception as e:
            logger.debug("title_re pattern failed: %s", e)

        raise RuntimeError(
            "Failed to connect to QQ process via UIA. "
            "Ensure QQ is running and logged in."
        )

    @staticmethod
    def _find_qq_pid() -> int | None:
        """Return the PID of a QQ.exe process, preferring the main instance."""
        candidates = []
        for p in psutil.process_iter(["name", "pid", "exe"]):
            try:
                if p.info["name"] == "QQ.exe":
                    candidates.append(p.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        # Return the first candidate (typically the main process has the
        # lowest PID, and process_iter usually returns it first).
        return candidates[0] if candidates else None

    # ------------------------------------------------------------------
    # Clipboard / chat copying
    # ------------------------------------------------------------------

    def copy_chat_content(self, group_hwnd: int) -> str:
        """Extract chat messages from the group window.

        Primary strategy: read text directly from the UIA '消息列表' tree
        (bypasses clipboard, no focus/selection needed).
        Fallback: Ctrl+A / Ctrl+C clipboard copy.
        """
        activate_window(group_hwnd)
        dlg = self.app.window(handle=group_hwnd)

        text = self._extract_messages_via_uia(dlg)
        if text:
            return text

        logger.debug("UIA extraction returned empty, falling back to clipboard")
        self._focus_message_list(dlg)
        send_keys("{END}")
        time.sleep(random.uniform(0.1, 0.3))
        return self._retry_clipboard_copy()

    # ------------------------------------------------------------------
    # UIA message extraction
    # ------------------------------------------------------------------

    def _extract_messages_via_uia(self, dlg) -> str:
        """Extract chat messages directly from the UIA '消息列表' element.

        Returns text formatted identically to QQ's clipboard copy output so
        that message_parser.parse_qq_messages() can consume it unchanged.
        Returns empty string when the UIA tree doesn't match expectations.
        """
        try:
            msg_list = dlg.child_window(title="消息列表", control_type="Window")
            if not msg_list.exists():
                logger.debug("UIA: '消息列表' element not found")
                return ""

            children = msg_list.children()
            if not children:
                return ""
            ml_root = children[0]  # ml-root Group

            current_date = ""
            current_sender = ""
            current_content = ""
            last_time = ""
            messages: list[dict] = []

            for child in ml_root.children():
                name = child.window_text()

                # Collect Text grandchildren.
                texts = []
                try:
                    for sc in child.children():
                        if sc.element_info.control_type == "Text":
                            texts.append(sc.window_text())
                except Exception:
                    pass
                text = texts[0] if texts else ""

                # Date separator: "YYYY/MM/DD HH:MM"
                if re.match(r"\d{4}/\d{2}/\d{2}", text):
                    current_date = text[:10].replace("/", "-")
                    continue

                # Timestamp: 上午/下午/凌晨/昨天 HH:MM
                tm = re.match(
                    r"(上午|下午|凌晨|昨天)\s*(\d{1,2}):(\d{2})", text
                )
                if tm and current_sender:
                    period, hour_str, minute = tm.group(1), tm.group(2), tm.group(3)
                    hour = int(hour_str)
                    if period == "下午" and hour != 12:
                        hour += 12
                    elif period == "上午" and hour == 12:
                        hour = 0
                    last_time = f"{hour:02d}:{minute}:00"

                    if current_content:
                        messages.append({
                            "sender": current_sender,
                            "time": f"{current_date} {last_time}",
                            "content": current_content.strip(),
                        })
                        current_content = ""
                    continue

                # Sender: Group element whose own name is the sender name.
                if name and not texts:
                    if current_sender and current_content:
                        fallback = last_time if last_time else "12:00:00"
                        messages.append({
                            "sender": current_sender,
                            "time": f"{current_date} {fallback}",
                            "content": current_content.strip(),
                        })
                        current_content = ""
                    current_sender = name
                    continue

                # Content: empty-name Group with a single Text child.
                if not name and texts and current_sender:
                    current_content = text
                    continue

            # Flush the last pending message.
            if current_sender and current_content:
                fallback = last_time if last_time else "12:00:00"
                messages.append({
                    "sender": current_sender,
                    "time": f"{current_date} {fallback}",
                    "content": current_content.strip(),
                })

            if not messages:
                return ""

            # Format identically to QQ's Ctrl+A → Ctrl+C output.
            lines = []
            for msg in messages:
                lines.append(f"{msg['sender']} {msg['time']}")
                lines.append(msg["content"])
                lines.append("")

            result = "\n".join(lines)
            logger.debug(
                "UIA extracted %d messages (%d chars)", len(messages), len(result)
            )
            return result

        except Exception:
            logger.debug("UIA extraction failed", exc_info=True)
            return ""

    def _focus_message_list(self, dlg) -> None:
        """Three-tier focus: auto_id → class_name → center click."""
        # Tier 1: precise auto_id
        try:
            child = dlg.child_window(**self._msg_list_sel)
            if child.exists():
                child.click_input()
                time.sleep(random.uniform(0.2, 0.4))
                return
        except Exception:
            pass

        # Tier 2: class_name fallback
        try:
            child = dlg.child_window(**self._msg_area_fallback)
            if child.exists():
                child.click_input()
                time.sleep(random.uniform(0.2, 0.4))
                return
        except Exception:
            pass

        # Tier 3: click window centre
        if self._fallback_click:
            dlg.click_input()
            time.sleep(random.uniform(0.2, 0.4))

    def _retry_clipboard_copy(self) -> str:
        """Copy via Ctrl+A, Ctrl+C with retries and incremental backoff.

        Clears the clipboard before each attempt to detect stale reads.
        """
        for i in range(self._retry_attempts):
            self._clear_clipboard()
            time.sleep(random.uniform(0.1, 0.2))
            send_keys("^a")
            time.sleep(random.uniform(0.2, 0.4))
            send_keys("^c")
            time.sleep(random.uniform(0.2, 0.4))
            text = self._get_clipboard_text()
            if text:
                return text
            delay = self._retry_delay * (i + 1)  # 0.5 → 1.0 → 1.5
            logger.debug("Clipboard empty on attempt %d/%d, retrying in %.1fs", i + 1, self._retry_attempts, delay)
            time.sleep(delay)
        logger.warning("Clipboard copy failed after %d attempts", self._retry_attempts)
        return ""

    @staticmethod
    def _clear_clipboard() -> None:
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
        except Exception:
            pass
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    @staticmethod
    def _get_clipboard_text() -> str:
        try:
            win32clipboard.OpenClipboard()
            try:
                data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                return data
            except Exception:
                return ""
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Sending messages
    # ------------------------------------------------------------------

    def send_to_contact(self, contact_name: str, message: str) -> None:
        """Send a text message to a QQ contact via their chat window.

        If the contact window is not open, searches via the main window first.
        """
        hwnd = find_window_by_title(contact_name)
        if not hwnd:
            self._open_contact_via_main(contact_name)
            time.sleep(random.uniform(0.5, 1.0))
            hwnd = find_window_by_title(contact_name)
        if not hwnd:
            raise RuntimeError(f"无法打开联系人窗口: {contact_name}")

        activate_window(hwnd)
        dlg = self.app.window(handle=hwnd)

        input_box = dlg.child_window(**self._input_edit_sel)
        input_box.click_input()
        time.sleep(random.uniform(0.1, 0.2))
        input_box.type_keys(message, with_spaces=True)
        send_keys("{ENTER}")
        logger.info("Sent notification to %s (%d chars)", contact_name, len(message))

    def _open_contact_via_main(self, contact_name: str) -> None:
        """Open a contact chat via the QQ main window search box."""
        main_hwnd = find_window_by_title("QQ")
        if not main_hwnd:
            logger.warning("QQ main window not found, cannot search for contact")
            return

        activate_window(main_hwnd)
        dlg = self.app.window(handle=main_hwnd)

        search_box = dlg.child_window(**self._search_box_sel)
        search_box.click_input()
        time.sleep(random.uniform(0.1, 0.2))
        search_box.type_keys(contact_name)
        time.sleep(random.uniform(0.3, 0.6))

        # Try to find and open the contact result item.
        try:
            result_kwargs = dict(self._contact_item_sel)
            if result_kwargs.get("title") is None:
                result_kwargs["title"] = contact_name
            result_item = dlg.child_window(**result_kwargs)
            if result_item.exists():
                result_item.double_click_input()
                logger.info("Opened contact '%s' via search", contact_name)
                return
        except Exception:
            pass

        # Fallback: type Enter in the search box.
        send_keys("{ENTER}")
        logger.info("Attempted to open contact '%s' via Enter on search", contact_name)
