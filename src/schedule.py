"""Schedule upcoming Dracula Daily messages into Slack's queue.

Run monthly. Slack holds the queue and delivers each message on its date.
Idempotent: re-running skips any entry already scheduled for the same post_at.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .slack_client import SlackClient

log = logging.getLogger("dracula")

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = REPO_ROOT / "data" / "dracula.json"

SCHEDULE_WINDOW_DAYS = 120  # Slack's hard cap on chat.scheduleMessage
BLOCK_TEXT_LIMIT = 2800      # Slack section text cap is 3000; leave a margin
RATE_LIMIT_SLEEP_S = 0.25    # 30 scheduled messages / 5 min = 1 every 10s,
                             # but per-user burst is higher; sleep is a courtesy


def parse_md(md: str) -> tuple[int, int]:
    m, d = md.split("-")
    return int(m), int(d)


def compute_post_at(mm_dd: str, year: int, hour: int, tz: ZoneInfo) -> int:
    m, d = parse_md(mm_dd)
    return int(datetime(year, m, d, hour, 0, 0, tzinfo=tz).timestamp())


def chunk_text(s: str, size: int = BLOCK_TEXT_LIMIT) -> list[str]:
    """Split `s` into chunks <= `size`, preferring paragraph then word breaks."""
    if len(s) <= size:
        return [s]
    chunks: list[str] = []
    remaining = s
    while len(remaining) > size:
        split = remaining.rfind("\n\n", 0, size)
        if split <= 0:
            split = remaining.rfind(" ", 0, size)
        if split <= 0:
            split = size
        chunks.append(remaining[:split].rstrip())
        remaining = remaining[split:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


NOVEL_YEAR = 1893  # in-story year for all entries


def format_message(
    entry: dict,
    post_dt: datetime,
    audio_url: str | None = None,
) -> tuple[str, list[dict]]:
    # %-d is POSIX (macOS/Linux); on Windows we'd need %#d.
    date_label = post_dt.strftime("%-d %B")  # e.g. "3 May"
    body = entry["body"].strip()

    location = entry.get("location")
    loc_suffix = f", {location}" if location else ""
    heading = f":scroll: _{date_label} {NOVEL_YEAR}{loc_suffix} · {entry['title']}_"

    fallback_text = f"Dracula — {date_label} {NOVEL_YEAR}: {entry['title']}"
    blocks: list[dict] = [
        {"type": "context", "elements": [{"type": "mrkdwn", "text": heading}]},
        {"type": "divider"},
    ]
    for chunk in chunk_text(body):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    if audio_url:
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f":headphones: _<{audio_url}|Listen to today's reading>_",
            }],
        })
    return fallback_text, blocks


def eligible_entries(
    entries: list[dict],
    today: date,
    window_end: date,
    now_ts: int,
    hour: int,
    tz: ZoneInfo,
) -> list[tuple[dict, int]]:
    """Return (entry, post_at) pairs whose date falls within the scheduling window
    and whose post_at is still in the future."""
    out: list[tuple[dict, int]] = []
    for entry in entries:
        m, d = parse_md(entry["date"])
        entry_date = date(today.year, m, d)
        if not (today <= entry_date <= window_end):
            continue
        post_at = compute_post_at(entry["date"], today.year, hour, tz)
        if post_at <= now_ts:
            continue
        out.append((entry, post_at))
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    token = os.environ["SLACK_BOT_TOKEN"]
    channel = os.environ["SLACK_CHANNEL_ID"]
    hour = int(os.environ.get("POST_HOUR_LOCAL", "10"))
    tz = ZoneInfo(os.environ.get("POST_TZ", "America/Montevideo"))
    audio_base = os.environ.get("AUDIO_BASE_URL", "").rstrip("/") or None

    entries = json.loads(DATA_PATH.read_text())

    now = datetime.now(tz)
    today = now.date()
    window_end = today + timedelta(days=SCHEDULE_WINDOW_DAYS)

    client = SlackClient(token)
    existing = client.list_scheduled_post_ats(channel)
    log.info("found %d already-scheduled messages in channel", len(existing))

    pairs = eligible_entries(entries, today, window_end, int(now.timestamp()), hour, tz)
    log.info("%d entries in the next %d days", len(pairs), SCHEDULE_WINDOW_DAYS)

    scheduled = skipped = 0
    for entry, post_at in pairs:
        if post_at in existing:
            skipped += 1
            continue
        post_dt = datetime.fromtimestamp(post_at, tz)
        audio_url = f"{audio_base}/{entry['date']}.mp3" if audio_base else None
        text, blocks = format_message(entry, post_dt, audio_url=audio_url)
        client.schedule_message(channel=channel, text=text, blocks=blocks, post_at=post_at)
        log.info("scheduled %s for %s", entry["date"], post_dt.isoformat())
        scheduled += 1
        time.sleep(RATE_LIMIT_SLEEP_S)

    log.info("done. scheduled=%d skipped=%d", scheduled, skipped)


if __name__ == "__main__":
    main()
