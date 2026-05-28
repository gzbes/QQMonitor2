"""Notification dispatch: cooldown per (group, model), multi-model merge, dry-run support."""

import logging
import time
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)


class NotificationService:
    """Cooldown-gated notification dispatch via QQAutomation.

    When the same (group, model, sender) triple appears within `cooldown_sec`,
    it is skipped.  Each model matched in a message is sent as a separate
    notification so the recipient can act on them individually.
    """

    def __init__(
        self,
        qq_auto,
        target_contact: str,
        cooldown_sec: float = 30,
        dry_run: bool = False,
        dry_run_log_path: str | None = None,
    ):
        self._qq = qq_auto
        self._target = target_contact
        self._cooldown = cooldown_sec
        self._dry_run = dry_run
        self._dry_run_path = Path(dry_run_log_path) if dry_run_log_path else None
        self._last_sent: dict[tuple[str, str, str], float] = defaultdict(float)

        if self._dry_run:
            logger.info("[DRY-RUN] NotificationService initialized — no QQ messages will be sent")
            if self._dry_run_path:
                self._dry_run_path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, group_name: str, message_obj: dict, matched_models: list[str]) -> int:
        """Send one notification per non-cooldown model in `matched_models`.

        Returns the number of notifications actually sent.
        """
        if not matched_models:
            return 0

        sender = message_obj.get("sender", "")
        now = time.time()
        sent_count = 0

        for model in matched_models:
            key = (group_name, model, sender)
            if now - self._last_sent.get(key, 0) < self._cooldown:
                continue

            self._last_sent[key] = now
            notification = (
                f"{group_name}群里出现{model}信息："
                f"{message_obj['sender']}-{message_obj['time']}:\n"
                f"原文：{message_obj['content'][:100]}"
            )

            if self._dry_run:
                self._write_dry_run(notification, group_name, [model], message_obj)
            else:
                self._qq.send_to_contact(self._target, notification)

            sent_count += 1

        return sent_count

    def _write_dry_run(self, notification: str, group_name: str, models: list[str], msg: dict) -> None:
        """Log the notification to the dry-run verification file and INFO log."""
        entry = (
            f"[DRY-RUN] {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"| 群:{group_name} | 型号:{'、'.join(models)} "
            f"| 发送者:{msg['sender']} | 原文前100字符:{msg['content'][:100]}"
        )
        logger.info(entry)
        if self._dry_run_path:
            with open(self._dry_run_path, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
