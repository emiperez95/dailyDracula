"""Post today's Dracula Daily entry with audio.

Runs once per day (via launchd). Looks up the current date in data/dracula.json,
formats the message body, and uploads the matching clip from data/audio_clips/
via files.upload_v2 with the body as `initial_comment` — this gives a native
Slack inline audio player.

Dates not present in dracula.json are skipped silently (Nov 8 → May 2).
Entries without a clip (06-18, 08-04, 09-19) post text-only via chat.postMessage.

Usage:
    python -m src.post_today                   # today (local tz)
    python -m src.post_today --date 05-03      # override for testing
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from zoneinfo import ZoneInfo

from slack_sdk import WebClient

log = logging.getLogger("post_today")

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = REPO_ROOT / "data" / "dracula.json"
CLIPS_DIR = REPO_ROOT / "data" / "audio_clips"

NOVEL_YEAR = 1893


def find_entry(entries, mm_dd: str):
    for e in entries:
        if e["date"] == mm_dd:
            return e
    return None


def format_body(entry: dict, post_dt: datetime) -> tuple[str, list[dict]]:
    """Return (fallback_text, blocks) for the post's heading + body.
    Intentionally excludes any audio block — audio is attached via file upload."""
    date_label = post_dt.strftime("%-d %B")
    location = entry.get("location")
    loc_suffix = f", {location}" if location else ""
    heading = f":scroll: _{date_label} {NOVEL_YEAR}{loc_suffix} · {entry['title']}_"
    fallback_text = f"Dracula — {date_label} {NOVEL_YEAR}: {entry['title']}"
    body = entry["body"].strip()

    # Slack block text limit is 3000; leave a margin.
    BLOCK_TEXT_LIMIT = 2800
    chunks: list[str] = []
    remaining = body
    while len(remaining) > BLOCK_TEXT_LIMIT:
        split = remaining.rfind("\n\n", 0, BLOCK_TEXT_LIMIT)
        if split <= 0:
            split = remaining.rfind(" ", 0, BLOCK_TEXT_LIMIT)
        if split <= 0:
            split = BLOCK_TEXT_LIMIT
        chunks.append(remaining[:split].rstrip())
        remaining = remaining[split:].lstrip()
    if remaining:
        chunks.append(remaining)

    blocks: list[dict] = [
        {"type": "context", "elements": [{"type": "mrkdwn", "text": heading}]},
        {"type": "divider"},
    ]
    for chunk in chunks:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    return fallback_text, blocks


def post_with_audio(client: WebClient, channel: str, entry: dict, post_dt: datetime, clip: Path) -> None:
    """Post body as the main message, then upload the audio as a plain follow-up."""
    fallback, blocks = format_body(entry, post_dt)
    client.chat_postMessage(channel=channel, text=fallback, blocks=blocks)
    client.files_upload_v2(
        file=str(clip),
        filename=f"dracula_{entry['date']}.mp3",
        title=f"Dracula Daily — {post_dt.strftime('%-d %B')}",
        channel=channel,
    )


def post_text_only(client: WebClient, channel: str, entry: dict, post_dt: datetime) -> None:
    fallback, blocks = format_body(entry, post_dt)
    client.chat_postMessage(channel=channel, text=fallback, blocks=blocks)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="MM-DD override (defaults to today in POST_TZ)")
    args = ap.parse_args()

    token = os.environ["SLACK_BOT_TOKEN"]
    channel = os.environ["SLACK_CHANNEL_ID"]
    tz = ZoneInfo(os.environ.get("POST_TZ", "America/Montevideo"))
    now = datetime.now(tz)
    mm_dd = args.date or now.strftime("%m-%d")
    post_dt = datetime(NOVEL_YEAR, int(mm_dd[:2]), int(mm_dd[3:]), tzinfo=tz)

    entries = json.loads(DATA_PATH.read_text())
    entry = find_entry(entries, mm_dd)
    if not entry:
        log.info("no entry for %s — skipping", mm_dd)
        return

    clip = CLIPS_DIR / f"{mm_dd}.mp3"
    client = WebClient(token=token)

    if clip.exists():
        log.info("posting %s with audio (%d bytes)", mm_dd, clip.stat().st_size)
        post_with_audio(client, channel, entry, post_dt, clip)
    else:
        log.info("posting %s text-only (no clip for this date)", mm_dd)
        post_text_only(client, channel, entry, post_dt)

    log.info("done")


if __name__ == "__main__":
    main()
