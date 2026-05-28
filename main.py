"""QQ Group Chat Product Model Monitoring System — main orchestration loop."""

import argparse
import json
import logging
import os
import random
import re
import signal
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from window_manager import find_window_by_title
from qq_automation import QQAutomation
from message_parser import filter_messages, parse_qq_messages, truncate_message
from tracker import IncrementalTracker
from matcher import ProductMatcher
from notifier import NotificationService

logger = logging.getLogger(__name__)

# Global shutdown event, set by signal handlers.
_shutdown_event = threading.Event()


def _handle_signal(signum: int, frame) -> None:
    logger.info("Received signal %d, shutting down gracefully...", signum)
    _shutdown_event.set()


def _strip_jsonc_comments(raw: str) -> str:
    """Remove // line comments and /* block comments */ from JSONC text."""
    raw = re.sub(r"//.*", "", raw)
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)
    return raw


def _load_config(path: str) -> dict:
    """Load and validate the JSONC configuration file."""
    if not os.path.exists(path):
        logger.fatal("Configuration file not found: %s", path)
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        raw = _strip_jsonc_comments(f.read())

    try:
        config = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.fatal("Invalid JSON in config file: %s", e)
        sys.exit(1)

    # Validate required fields.
    missing = []
    for field in ("groups", "target_contact", "product_csv_path", "log_dir"):
        if field not in config:
            missing.append(field)
    if missing:
        logger.fatal("Missing required config fields: %s", ", ".join(missing))
        sys.exit(1)

    if not isinstance(config["groups"], list) or len(config["groups"]) == 0:
        logger.fatal("Config 'groups' must be a non-empty list")
        sys.exit(1)

    return config


def _setup_logging(config: dict) -> None:
    """Configure dual-channel logging: RotatingFileHandler (INFO+) + StreamHandler (WARNING+)."""
    log_dir = Path(config["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "monitor.log"

    # Configure the root logger so all module loggers (matcher, tracker,
    # qq_automation, notifier) propagate their messages here.
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    # File handler: INFO and above, size-based rotation.
    max_bytes = config.get("log_max_bytes", 10 * 1024 * 1024)
    backup_count = config.get("log_backup_count", 5)
    fh = RotatingFileHandler(
        str(log_file), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    fh.setLevel(logging.INFO)
    fh.setFormatter(formatter)
    root.addHandler(fh)

    # Console handler: WARNING and above to stderr.
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.WARNING)
    ch.setFormatter(formatter)
    root.addHandler(ch)


def main():
    parser = argparse.ArgumentParser(description="QQ群聊产品型号监控系统")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="演习模式：通知写入验证文件而不实际发送QQ消息",
    )
    parser.add_argument(
        "--config",
        default="config.jsonc",
        help="配置文件路径（默认 config.jsonc）",
    )
    args = parser.parse_args()

    # --- config ---
    config = _load_config(args.config)
    _setup_logging(config)

    if args.dry_run:
        logger.info("========== 演习模式 (DRY-RUN) 已启用 ==========")

    logger.info("监控程序启动，配置: %s", args.config)

    # --- signal handlers (graceful shutdown) ---
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # --- init modules ---
    try:
        qq = QQAutomation(config, shutdown_event=_shutdown_event)
    except Exception:
        logger.fatal("QQ 连接失败，程序退出")
        sys.exit(1)

    csv_path = config["product_csv_path"]
    if not os.path.exists(csv_path):
        logger.fatal("产品CSV文件不存在: %s", csv_path)
        sys.exit(1)
    try:
        matcher = ProductMatcher(csv_path, case_sensitive=config.get("match_case_sensitive", False))
    except Exception:
        logger.fatal("产品CSV加载失败，程序退出", exc_info=True)
        sys.exit(1)

    tracker = IncrementalTracker()

    dry_run_log = None
    if args.dry_run:
        dry_run_log = str(Path(config["log_dir"]) / "dry_run_verification.log")

    notifier = NotificationService(
        qq,
        config["target_contact"],
        cooldown_sec=config.get("cooldown_sec", 30),
        dry_run=args.dry_run,
        dry_run_log_path=dry_run_log,
    )

    groups = config["groups"]
    poll_interval = config.get("poll_interval_seconds", 60)
    inter_group_delay = config.get("inter_group_delay_sec", 1.0)

    logger.info(
        "初始化完成 — 监控 %d 个群, %d 个型号, 轮询间隔 %ds, 冷却 %ds",
        len(groups),
        len(matcher.models),
        poll_interval,
        config.get("cooldown_sec", 30),
    )

    # --- main loop ---
    cycle_count = 0

    while not _shutdown_event.is_set():
        cycle_count += 1
        cycle_start = time.time()
        cycle_stats = {
            "messages_total": 0,
            "messages_new": 0,
            "matches": 0,
            "notifications": 0,
            "cooldown_skipped": 0,
            "window_miss": 0,
            "clipboard_empty": 0,
            "group_times": {},
        }

        for group in groups:
            if _shutdown_event.is_set():
                break
            gname = group.get("name", group.get("number", "?"))
            gnumber = group.get("number", "")
            t0 = time.time()
            try:
                # Window lookup: group number first (more precise), then name.
                hwnd = None
                if gnumber:
                    hwnd = find_window_by_title(gnumber)
                if not hwnd:
                    hwnd = find_window_by_title(gname)
                if not hwnd:
                    logger.warning("未找到群窗口: %s (%s)", gname, gnumber)
                    cycle_stats["window_miss"] += 1
                    continue

                raw_text = qq.copy_chat_content(hwnd)
                if not raw_text:
                    logger.debug("群 %s 剪贴板复制为空", gname)
                    cycle_stats["clipboard_empty"] += 1
                    continue

                all_msgs = parse_qq_messages(raw_text)
                filtered_msgs = filter_messages(all_msgs)
                new_msgs = tracker.get_new_messages(gname, filtered_msgs)

                cycle_stats["messages_total"] += len(filtered_msgs)
                cycle_stats["messages_new"] += len(new_msgs)

                if new_msgs:
                    logger.info("群 %s 发现 %d 条新消息（已过滤 %d 条）", gname, len(new_msgs), len(all_msgs) - len(filtered_msgs))

                for msg in new_msgs:
                    matched = matcher.match(msg["content"])
                    if matched:
                        logger.info(
                            "匹配成功 群:%s 型号:%s 发送者:%s 内容:%s",
                            gname,
                            matched,
                            msg["sender"],
                            truncate_message(msg["content"]),
                        )
                        sent = notifier.send(gname, msg, matched)
                        cycle_stats["matches"] += len(matched)
                        if sent:
                            cycle_stats["notifications"] += 1
                        else:
                            cycle_stats["cooldown_skipped"] += 1

            except Exception:
                logger.error("处理群 %s 时出错", gname, exc_info=True)

            elapsed = time.time() - t0
            cycle_stats["group_times"][gname] = elapsed

            # Inter-group jitter.
            jitter = inter_group_delay * random.uniform(0.5, 1.5)
            time.sleep(jitter)

        cycle_elapsed = time.time() - cycle_start

        # Periodic metrics summary (every 10 cycles).
        if cycle_count % 10 == 0:
            fp_counts = tracker.fingerprint_counts
            group_detail = " ".join(
                f"{gn}={cycle_stats['group_times'].get(gn, 0):.1f}s"
                for gn in [g.get("name", "?") for g in groups]
            )
            logger.info(
                "[指标] 第%d轮 | 总消息:%d 新增:%d 命中:%d 通知:%d 冷却跳过:%d "
                "窗口丢失:%d 剪贴板空:%d | 耗时:%.1fs | 各群: %s | 指纹:%s",
                cycle_count,
                cycle_stats["messages_total"],
                cycle_stats["messages_new"],
                cycle_stats["matches"],
                cycle_stats["notifications"],
                cycle_stats["cooldown_skipped"],
                cycle_stats["window_miss"],
                cycle_stats["clipboard_empty"],
                cycle_elapsed,
                group_detail,
                fp_counts,
            )

        # Poll interval with ±10% random jitter, using 1s sub-waits for
        # responsive shutdown.
        jitter = poll_interval * random.uniform(-0.1, 0.1)
        sleep_remaining = poll_interval + jitter
        while sleep_remaining > 0 and not _shutdown_event.is_set():
            chunk = min(1.0, sleep_remaining)
            time.sleep(chunk)
            sleep_remaining -= chunk

    logger.info("监控程序已退出")


if __name__ == "__main__":
    main()
