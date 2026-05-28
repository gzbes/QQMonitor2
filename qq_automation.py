"""QQ UI automation via pywinauto UIA backend: copy chat, send messages."""

import logging
import random
import re
import threading
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

    def __init__(self, config: dict, shutdown_event: threading.Event | None = None):
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
        self._shutdown_event = shutdown_event

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

        if self._shutdown_event and self._shutdown_event.is_set():
            logger.info("Shutdown requested, skipping clipboard fallback")
            return ""

        logger.info("UIA extraction returned empty, falling back to clipboard for group window")
        self._focus_message_list(dlg)
        send_keys("{END}")
        time.sleep(random.uniform(0.2, 0.3))
        send_keys("{DOWN}")  # activate a visible message so Ctrl+A works in Electron webview
        time.sleep(random.uniform(0.1, 0.2))
        return self._retry_clipboard_copy(dlg)

    # ------------------------------------------------------------------
    # UIA message extraction
    # ------------------------------------------------------------------

    def _extract_messages_via_uia(self, dlg) -> str:
        """Extract chat messages directly from the UIA '消息列表' element.

        Supports two QQ NT UIA tree layouts:
        - Legacy: messages nested under msg_list → ml_root → children
        - Current: messages as direct Group children of msg_list (sender=Group
          with name & 0 sub-children, content=Group with name & >=1 sub-children)

        Returns text formatted identically to QQ's clipboard copy output.
        Returns empty string when the UIA tree doesn't match expectations.
        """
        try:
            msg_list = dlg.child_window(title="消息列表", control_type="Window")
            if not msg_list.exists():
                logger.info("UIA: '消息列表' element not found")
                return ""

            msg_children = msg_list.children()
            if not msg_children:
                return ""

            current_date = ""
            current_sender = ""
            current_content = ""
            last_time = ""
            messages: list[dict] = []

            def _flush():
                nonlocal current_content
                if current_sender and current_content:
                    fallback = last_time if last_time else "12:00:00"
                    messages.append({
                        "sender": current_sender,
                        "time": f"{current_date} {fallback}",
                        "content": current_content.strip(),
                    })
                    current_content = ""

            def _try_parse_legacy():
                """Try old layout: ml_root → children (Groups with Text grandchildren)."""
                nonlocal current_date, current_sender, current_content, last_time
                ml_root = msg_children[0]
                for child in ml_root.children():
                    name = child.window_text()
                    texts = []
                    try:
                        for sc in child.children():
                            if sc.element_info.control_type == "Text":
                                texts.append(sc.window_text())
                    except Exception:
                        pass
                    text = texts[0] if texts else ""

                    if re.match(r"\d{4}/\d{2}/\d{2}", text):
                        current_date = text[:10].replace("/", "-")
                        continue

                    tm = re.match(r"(上午|下午|凌晨|昨天)\s*(\d{1,2}):(\d{2})", text)
                    if tm and current_sender:
                        period, h, m = tm.group(1), int(tm.group(2)), tm.group(3)
                        if period == "下午" and h != 12:
                            h += 12
                        elif period == "上午" and h == 12:
                            h = 0
                        last_time = f"{h:02d}:{m}:00"
                        _flush()
                        continue

                    if name and not texts:
                        if re.match(r"^\d{1,2}:\d{2}$", name):
                            continue
                        if re.match(r"^(上午|下午|凌晨|昨天)\s*\d{1,2}:\d{2}$", name):
                            continue
                        _flush()
                        current_sender = name
                        continue

                    if not name and texts and current_sender:
                        current_content = text
                        continue

                _flush()
                return len(messages) > 0

            def _parse_current_layout():
                """Parse current QQ NT layout: msg_list children are sender/content Groups."""
                nonlocal current_date, current_sender, current_content, last_time

                for child in msg_children:
                    name = child.window_text()
                    try:
                        ctrl = child.element_info.control_type
                    except Exception:
                        ctrl = None
                    try:
                        sub_count = len(child.children())
                    except Exception:
                        sub_count = 0

                    if ctrl == "Text":
                        # Bare timestamp or date at msg_list level.
                        if re.match(r"\d{4}[/-]\d{2}[/-]\d{2}", name):
                            current_date = name[:10].replace("/", "-")
                            continue
                        tm = re.match(r"(上午|下午|凌晨|昨天)\s*(\d{1,2}):(\d{2})", name)
                        if tm:
                            period, h, m = tm.group(1), int(tm.group(2)), tm.group(3)
                            if period == "下午" and h != 12:
                                h += 12
                            elif period == "上午" and h == 12:
                                h = 0
                            last_time = f"{h:02d}:{m}:00"
                            _flush()
                            continue
                        if re.match(r"^\d{1,2}:\d{2}$", name):
                            continue
                        continue

                    if ctrl == "Group":
                        # Empty-name group with children: could be a timestamp
                        # container (ml-root) OR a message whose text is in
                        # sub-children instead of the group name.
                        if not name and sub_count > 0:
                            text_parts = []
                            for sub in child.children():
                                try:
                                    sub_ctrl = sub.element_info.control_type
                                except Exception:
                                    sub_ctrl = None
                                sub_name = sub.window_text()
                                if sub_ctrl == "Text":
                                    if re.match(r"\d{4}[/-]\d{2}[/-]\d{2}", sub_name):
                                        current_date = sub_name[:10].replace("/", "-")
                                        continue
                                    tm = re.match(r"(上午|下午|凌晨|昨天)\s*(\d{1,2}):(\d{2})", sub_name)
                                    if tm:
                                        period, h, m = tm.group(1), int(tm.group(2)), tm.group(3)
                                        if period == "下午" and h != 12:
                                            h += 12
                                        elif period == "上午" and h == 12:
                                            h = 0
                                        last_time = f"{h:02d}:{m}:00"
                                        _flush()
                                        continue
                                    if re.match(r"^\d{1,2}:\d{2}$", sub_name):
                                        continue
                                    text_parts.append(sub_name)
                            # If we found non-timestamp text and have a sender,
                            # this is a message whose content lives in sub-children.
                            # Consecutive empty-name groups from same sender are
                            # concatenated (multi-part messages).
                            if text_parts and current_sender:
                                part = "\n".join(text_parts)
                                if current_content:
                                    current_content += "\n" + part
                                else:
                                    current_content = part
                            continue

                        # Sender: Group with name, no sub-children.
                        if name and sub_count == 0:
                            _flush()
                            current_sender = name
                            continue

                        # Content: Group with name and sub-children.
                        if name and sub_count > 0:
                            current_content = name
                            continue

                _flush()
                return len(messages) > 0

            # Try current layout first, fall back to legacy.
            if not _parse_current_layout():
                if not _try_parse_legacy():
                    self._dump_uia_tree(msg_list, msg_children)
                    return ""

            # Format identically to QQ's Ctrl+A → Ctrl+C output.
            lines = []
            for msg in messages:
                lines.append(f"{msg['sender']} {msg['time']}")
                lines.append(msg["content"])
                lines.append("")

            result = "\n".join(lines)
            logger.info("UIA extracted %d messages (%d chars)", len(messages), len(result))
            return result

        except Exception:
            logger.info("UIA extraction failed", exc_info=True)
            return ""

    def _dump_uia_tree(self, msg_list, msg_children) -> None:
        """Log the UIA tree structure to aid debugging selector mismatches."""
        logger.info("UIA: parsed 0 messages. msg_list has %d direct children", len(msg_children))
        for idx, c in enumerate(msg_children[:10]):
            try:
                ctrl = c.element_info.control_type
                name = c.window_text()[:80] if c.window_text() else ""
                sub = len(c.children()) if c.children() else 0
                # Collect sub-children text for Groups with empty names.
                sub_texts = []
                if ctrl == "Group" and not name and sub > 0:
                    try:
                        for sc in c.children():
                            sc_name = sc.window_text()
                            if sc_name:
                                sub_texts.append(sc_name[:60])
                    except Exception:
                        pass
                extra = f" sub_texts={sub_texts}" if sub_texts else ""
                logger.info("UIA msg_list[%d]: ctrl=%s name=%r sub_children=%d%s",
                            idx, ctrl, name, sub, extra)
            except Exception:
                logger.info("UIA msg_list[%d]: <error>", idx)

    def _focus_message_list(self, dlg) -> None:
        """Three-tier focus: auto_id → class_name → center click."""
        # Tier 1: precise auto_id
        try:
            child = dlg.child_window(**self._msg_list_sel)
            if child.exists():
                child.click_input()
                time.sleep(random.uniform(0.2, 0.4))
                logger.info("Focus: auto_id='message_list' succeeded")
                return
        except Exception:
            pass
        logger.info("Focus: auto_id='message_list' not found, trying class_name fallback")

        # Tier 2: class_name fallback
        try:
            child = dlg.child_window(**self._msg_area_fallback)
            if child.exists():
                child.click_input()
                time.sleep(random.uniform(0.2, 0.4))
                logger.info("Focus: class_name='ChatWnd' fallback succeeded")
                return
        except Exception:
            pass
        logger.info("Focus: class_name='ChatWnd' not found, trying generic List/Group fallback")

        # Tier 2.5: any List control (common in QQ NT message areas)
        try:
            children = dlg.children(control_type="List")
            if children:
                children[0].click_input()
                time.sleep(random.uniform(0.2, 0.4))
                logger.info("Focus: generic List control fallback succeeded (%d found)", len(children))
                return
        except Exception:
            pass
        logger.info("Focus: no List control found, trying message area coordinate click")

        # Tier 3: click message area (upper 40% height, avoids input box at bottom)
        if self._fallback_click:
            rect = dlg.rectangle()
            msg_x = rect.width() // 2
            msg_y = int(rect.height() * 0.4)
            dlg.click_input(coords=(msg_x, msg_y))
            time.sleep(random.uniform(0.2, 0.4))
            logger.info("Focus: message-area click fallback used (coords %d,%d)", msg_x, msg_y)

    def _retry_clipboard_copy(self, dlg) -> str:
        """Copy via Ctrl+A, Ctrl+C with retries, multi-read, and incremental backoff.

        Uses system-level send_keys() (not type_keys) because QQ NT uses an
        Electron webview that only responds to system-level keyboard events.
        Tries reading clipboard up to 3 times per attempt.
        Checks shutdown_event between retries to avoid delaying exit.
        """
        for i in range(self._retry_attempts):
            if self._shutdown_event and self._shutdown_event.is_set():
                logger.info("Shutdown requested, aborting clipboard retry loop")
                return ""

            self._clear_clipboard()
            time.sleep(random.uniform(0.2, 0.4))
            send_keys("^a")
            time.sleep(random.uniform(0.2, 0.4))
            send_keys("^c")
            time.sleep(random.uniform(0.3, 0.5))

            # Multi-read: clipboard data may arrive with slight delay.
            for read_i in range(3):
                text = self._get_clipboard_text()
                if text:
                    logger.info("Clipboard copy succeeded on attempt %d/%d, read %d/3 (%d chars)",
                                i + 1, self._retry_attempts, read_i + 1, len(text))
                    return text
                time.sleep(random.uniform(0.1, 0.2))

            delay = self._retry_delay * (i + 1)
            logger.info("Clipboard empty on attempt %d/%d (3 reads each), retrying in %.1fs",
                        i + 1, self._retry_attempts, delay)
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
    def _set_clipboard_text(text: str) -> None:
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        except Exception:
            logger.debug("Failed to set clipboard text", exc_info=True)
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    def _paste_via_clipboard(self, text: str) -> None:
        """Set clipboard and paste with Ctrl+V — reliable for Chinese text."""
        self._set_clipboard_text(text)
        time.sleep(random.uniform(0.05, 0.1))
        send_keys("^v")
        time.sleep(random.uniform(0.2, 0.3))

    def _click_bottom(self, dlg) -> None:
        """Click near the bottom of the dialog where the input area lives."""
        rect = dlg.rectangle()
        rel_x = rect.width() // 2
        rel_y = int(rect.height() * 0.9)
        dlg.click_input(coords=(rel_x, rel_y))

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
        Uses clipboard paste (Ctrl+V) for reliable Chinese text input.
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

        input_box = self._find_input_box(dlg)
        if input_box is not None:
            input_box.click_input()
            time.sleep(random.uniform(0.1, 0.2))
        else:
            logger.info("Input box not found via UIA, clicking bottom area")
            self._click_bottom(dlg)
            time.sleep(random.uniform(0.1, 0.2))

        self._paste_via_clipboard(message)
        send_keys("{ENTER}")
        logger.info("Sent notification to %s (%d chars)", contact_name, len(message))

    def _find_input_box(self, dlg):
        """Find the chat input Edit control using tiered fallback selectors.

        Returns the control or None if no suitable element was found.
        """
        # Tier 1: configured input_edit selector
        try:
            box = dlg.child_window(**self._input_edit_sel)
            if box.exists():
                logger.debug("Input box found via configured selector: %s", self._input_edit_sel)
                return box
        except Exception:
            pass

        # Tier 2: any Edit control (QQ NT chat window typically has only one
        # large Edit for composing messages; pick the last one if multiple exist)
        try:
            edits = dlg.children(control_type="Edit")
            if edits:
                box = edits[-1]
                logger.debug("Input box found via fallback: control_type=Edit (index=%d/%d)",
                             len(edits) - 1, len(edits))
                return box
        except Exception:
            pass

        logger.debug("No input box found via any UIA selector")
        return None

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
