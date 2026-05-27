"""Incremental message deduplication via MD5 fingerprint sets, per group."""

import logging

from message_parser import message_fingerprint

logger = logging.getLogger(__name__)

# Safety cap: maximum fingerprints stored per group before trimming.
_MAX_FINGERPRINTS = 50_000


class IncrementalTracker:
    """Track seen message fingerprints per group to detect new messages only."""

    def __init__(self):
        self.fingerprints: dict[str, set[str]] = {}

    def get_new_messages(self, group_name: str, messages: list[dict]) -> list[dict]:
        """Return only messages whose fingerprints are not already tracked for this group.

        Updates the fingerprint set for the group.  Trims the oldest half when
        the set exceeds the safety cap.
        """
        existing = self.fingerprints.get(group_name, set())
        new_msgs = []
        for msg in messages:
            fp = message_fingerprint(msg)
            if fp not in existing:
                new_msgs.append(msg)
                existing.add(fp)

        # Safety trim: keep set size bounded for long-running sessions.
        if len(existing) > _MAX_FINGERPRINTS:
            logger.warning(
                "Fingerprint set for group %s exceeded %d, trimming oldest half",
                group_name,
                _MAX_FINGERPRINTS,
            )
            # Convert to list, drop the first half, rebuild the set.
            kept = list(existing)[len(existing) // 2 :]
            existing = set(kept)

        self.fingerprints[group_name] = existing
        return new_msgs

    @property
    def fingerprint_counts(self) -> dict[str, int]:
        """Return per-group fingerprint set sizes (for metrics)."""
        return {k: len(v) for k, v in self.fingerprints.items()}
