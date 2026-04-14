from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.schedule import (
    chunk_text,
    compute_post_at,
    eligible_entries,
    format_message,
    parse_md,
)


TZ = ZoneInfo("America/Montevideo")


def test_parse_md():
    assert parse_md("05-03") == (5, 3)
    assert parse_md("11-07") == (11, 7)


def test_compute_post_at_is_stable_and_in_tz():
    ts = compute_post_at("05-03", 2026, 10, TZ)
    dt = datetime.fromtimestamp(ts, TZ)
    assert dt.year == 2026 and dt.month == 5 and dt.day == 3
    assert dt.hour == 10 and dt.minute == 0


def test_compute_post_at_differs_by_tz():
    ts_mvd = compute_post_at("05-03", 2026, 10, ZoneInfo("America/Montevideo"))
    ts_utc = compute_post_at("05-03", 2026, 10, ZoneInfo("UTC"))
    assert ts_mvd != ts_utc
    # Montevideo is UTC-3: 10:00 local = 13:00 UTC, so local-as-epoch is later
    # than UTC-as-epoch by 3h.
    assert ts_mvd - ts_utc == 3 * 3600


def test_chunk_text_short_returns_single_chunk():
    assert chunk_text("hello world", size=100) == ["hello world"]


def test_chunk_text_splits_on_paragraph_then_word():
    body = "a" * 50 + "\n\n" + "b" * 50 + " " + "c" * 50
    chunks = chunk_text(body, size=60)
    assert all(len(c) <= 60 for c in chunks)
    # Rejoining should recover every non-whitespace char.
    rejoined = "".join(c.replace(" ", "").replace("\n", "") for c in chunks)
    original = body.replace(" ", "").replace("\n", "")
    assert rejoined == original


def test_chunk_text_handles_very_long_unbroken_input():
    body = "x" * 10_000
    chunks = chunk_text(body, size=1_000)
    assert all(len(c) <= 1_000 for c in chunks)
    assert "".join(chunks) == body


def test_format_message_produces_blocks_and_fallback():
    entry = {"date": "05-03", "title": "Jonathan Harker's Journal", "body": "3 May. Bistritz."}
    dt = datetime(2026, 5, 3, 10, 0, tzinfo=TZ)
    text, blocks = format_message(entry, dt)
    assert "Dracula" in text and "May 3" in text
    assert blocks[0]["type"] == "section"
    assert blocks[-1]["type"] == "context"
    body_chunks = [b for b in blocks if b["type"] == "section"][1:]
    assert any("Bistritz" in b["text"]["text"] for b in body_chunks)


def test_format_message_chunks_long_body():
    entry = {"date": "05-03", "title": "x", "body": "word " * 2000}  # ~10k chars
    dt = datetime(2026, 5, 3, 10, 0, tzinfo=TZ)
    _, blocks = format_message(entry, dt)
    section_blocks = [b for b in blocks if b["type"] == "section"]
    assert len(section_blocks) >= 3  # header + >=2 body chunks
    for b in section_blocks:
        assert len(b["text"]["text"]) <= 3000


def _entries():
    return [
        {"date": "05-03", "title": "A", "body": "x"},
        {"date": "06-15", "title": "B", "body": "x"},
        {"date": "11-07", "title": "C", "body": "x"},
    ]


def test_eligible_entries_within_window():
    today = date(2026, 5, 1)
    window_end = date(2026, 8, 29)  # 120 days later
    now_ts = int(datetime(2026, 5, 1, 0, 0, tzinfo=TZ).timestamp())
    pairs = eligible_entries(_entries(), today, window_end, now_ts, 10, TZ)
    dates = [e["date"] for e, _ in pairs]
    assert dates == ["05-03", "06-15"]


def test_eligible_entries_excludes_today_if_post_hour_passed():
    today = date(2026, 5, 3)
    window_end = date(2026, 9, 1)
    # 11:00 local on 05-03 is after the 10:00 post hour
    now_ts = int(datetime(2026, 5, 3, 11, 0, tzinfo=TZ).timestamp())
    pairs = eligible_entries(_entries(), today, window_end, now_ts, 10, TZ)
    assert "05-03" not in [e["date"] for e, _ in pairs]


def test_eligible_entries_includes_today_if_post_hour_not_yet():
    today = date(2026, 5, 3)
    window_end = date(2026, 9, 1)
    now_ts = int(datetime(2026, 5, 3, 8, 0, tzinfo=TZ).timestamp())
    pairs = eligible_entries(_entries(), today, window_end, now_ts, 10, TZ)
    assert "05-03" in [e["date"] for e, _ in pairs]


def test_data_file_is_valid():
    """Smoke-check the committed data/dracula.json: right shape, sane ranges."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "data" / "dracula.json"
    if not path.exists():
        import pytest
        pytest.skip("data/dracula.json not built yet")

    data = json.loads(path.read_text())
    assert len(data) >= 90, f"expected >=90 entries, got {len(data)}"
    seen_dates = set()
    for e in data:
        assert set(e.keys()) == {"date", "title", "body"}
        m, d = parse_md(e["date"])
        assert 1 <= m <= 12 and 1 <= d <= 31
        assert e["date"] not in seen_dates, f"duplicate date {e['date']}"
        seen_dates.add(e["date"])
        assert e["title"].strip()
        assert len(e["body"]) >= 100, f"{e['date']} body suspiciously short"

    # Novel spans May 3 → November 7
    months = {int(e["date"].split("-")[0]) for e in data}
    assert months <= {5, 6, 7, 8, 9, 10, 11}
    assert 5 in months and 11 in months


def test_idempotency_via_existing_post_ats():
    """Simulates skipping dupes: any post_at in `existing` is skipped."""
    today = date(2026, 5, 1)
    window_end = date(2026, 8, 29)
    now_ts = int(datetime(2026, 5, 1, 0, 0, tzinfo=TZ).timestamp())
    pairs = eligible_entries(_entries(), today, window_end, now_ts, 10, TZ)
    existing = {pairs[0][1]}  # already scheduled 05-03
    to_schedule = [p for p in pairs if p[1] not in existing]
    assert [e["date"] for e, _ in to_schedule] == ["06-15"]
