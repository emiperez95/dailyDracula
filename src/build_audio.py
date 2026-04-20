"""Build per-entry Dracula audio clips from the Keeble LibriVox v5 reading.

Four idempotent phases:
  A (download)   — fetch 27 chapter MP3s from archive.org
  B (transcribe) — Whisper each chapter with word timestamps
  C (anchors)    — find date-header timestamps, map to dracula.json dates
  D (split)      — ffmpeg-slice each chapter into per-entry MP3 clips

Usage:
  python -m src.build_audio                    # runs all phases
  python -m src.build_audio --phase download   # runs a single phase
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests

log = logging.getLogger("build_audio")

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "audio_raw"
WHISPER_DIR = DATA_DIR / "whisper"
CLIPS_DIR = DATA_DIR / "audio_clips"
ANCHORS_PATH = DATA_DIR / "anchors.json"
DRACULA_PATH = DATA_DIR / "dracula.json"

CHAPTERS = range(1, 28)  # 27 chapters

# Ch01's `_64kb` file in the v5 archive item is mis-uploaded v2 (dramatic
# reading) content. The non-suffixed file is the correct Keeble v5 reading.
# Chapters 02–27's `_64kb` files are the correct v5 reading.
def _chapter_url(n: int) -> str:
    base = "https://www.archive.org/download/dracula_version_5_2012_librivox"
    filename = "dracula_01_stoker.mp3" if n == 1 else f"dracula_{n:02d}_stoker_64kb.mp3"
    return f"{base}/{filename}"


def chapter_raw_path(n: int) -> Path:
    # Keep the _64kb filename for ch01 locally for consistency with phases B-D,
    # even though the source URL differs.
    return RAW_DIR / f"dracula_{n:02d}_stoker_64kb.mp3"


def chapter_whisper_path(n: int) -> Path:
    return WHISPER_DIR / f"ch{n:02d}.json"


# ---------------------------------------------------------------------------
# Phase A: Download
# ---------------------------------------------------------------------------

def download_chapter(n: int) -> None:
    dest = chapter_raw_path(n)
    if dest.exists() and dest.stat().st_size > 1_000_000:
        log.info("[A] ch%02d already downloaded (%d bytes), skipping", n, dest.stat().st_size)
        return
    url = _chapter_url(n)
    log.info("[A] downloading ch%02d from %s", n, url)
    tmp = dest.with_suffix(".mp3.part")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    f.write(chunk)
    tmp.rename(dest)
    log.info("[A] ch%02d done (%d bytes)", n, dest.stat().st_size)


def phase_download() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for n in CHAPTERS:
        download_chapter(n)


# ---------------------------------------------------------------------------
# Phase B: Whisper transcription
# ---------------------------------------------------------------------------

def transcribe_chapter(n: int, model) -> None:
    src = chapter_raw_path(n)
    dest = chapter_whisper_path(n)
    if dest.exists():
        try:
            existing = json.loads(dest.read_text())
            if existing.get("segments"):
                log.info("[B] ch%02d already transcribed (%d segments), skipping",
                         n, len(existing["segments"]))
                return
        except json.JSONDecodeError:
            pass  # fall through and retranscribe
    log.info("[B] transcribing ch%02d", n)
    result = model.transcribe(
        str(src),
        language="en",
        word_timestamps=True,
        verbose=False,
    )
    dest.write_text(json.dumps(result, ensure_ascii=False))
    log.info("[B] ch%02d done (%d segments)", n, len(result.get("segments", [])))


def phase_transcribe() -> None:
    WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    import whisper  # local import — heavy
    log.info("[B] loading whisper model 'small'")
    model = whisper.load_model("small")
    for n in CHAPTERS:
        transcribe_chapter(n, model)


# ---------------------------------------------------------------------------
# Phase C: Anchor extraction
# ---------------------------------------------------------------------------

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

WORD_TO_DAY = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
    "twentieth": 20, "twenty-first": 21, "twenty-second": 22, "twenty-third": 23,
    "twenty-fourth": 24, "twenty-fifth": 25, "twenty-sixth": 26,
    "twenty-seventh": 27, "twenty-eighth": 28, "twenty-ninth": 29,
    "thirtieth": 30, "thirty-first": 31,
}

MONTH_RE = "|".join(MONTHS.keys())

NUMERIC_DATE_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({MONTH_RE})\b",
    re.IGNORECASE,
)

WORD_KEYS = sorted(WORD_TO_DAY.keys(), key=len, reverse=True)  # match longer first
WORD_DATE_RE = re.compile(
    rf"\b({'|'.join(re.escape(w) for w in WORD_KEYS)})\s+(?:of\s+)?({MONTH_RE})\b",
    re.IGNORECASE,
)


PAUSE_THRESHOLD_S = 0.5  # a date preceded by >= this pause is treated as a header
SEGMENT_HEAD_WORDS = 6    # or: it appears within first N words of a segment
ANCHOR_PREROLL_S = 1.5   # back off this much before the word timestamp to
                          # account for Whisper's tendency to mark word starts
                          # slightly late — otherwise clips miss the header word


def _normalize_words(words: list[dict]) -> list[dict]:
    """Whisper sometimes splits hyphenated words like 'Twenty-fourth' into
    two tokens (' Twenty', '-fourth'). Merge them so downstream matching
    sees a single token."""
    out: list[dict] = []
    i = 0
    while i < len(words):
        w = words[i]
        if i + 1 < len(words):
            nxt = words[i + 1]
            nxt_raw = nxt["word"].lstrip()
            if nxt_raw.startswith("-"):
                merged = {
                    "start": w["start"],
                    "end": nxt["end"],
                    "word": w["word"] + nxt_raw,  # e.g. " Twenty" + "-fourth"
                }
                out.append(merged)
                i += 2
                continue
        out.append(w)
        i += 1
    return out


def _match_date_at(words: list[dict], i: int) -> tuple[int, int, int] | None:
    """If a date phrase ("15th of May", "First of May", "Twenty-eighth of May",
    "26th May") starts at word index `i`, return (month, day, length).
    Returns None otherwise."""
    if i >= len(words):
        return None

    def wtxt(idx: int) -> str:
        return words[idx]["word"].strip().lower().rstrip(".,;:!?")

    w0 = wtxt(i)

    # Numeric form: "15th", "15", "1st"
    m = re.match(r"^(\d{1,2})(st|nd|rd|th)?$", w0)
    if m:
        day = int(m.group(1))
        if not (1 <= day <= 31):
            return None
        # Next 1–2 words should be "<Month>" or "of <Month>"
        if i + 1 < len(words) and wtxt(i + 1) in MONTHS:
            return (MONTHS[wtxt(i + 1)], day, 2)
        if (i + 2 < len(words) and wtxt(i + 1) == "of"
                and wtxt(i + 2) in MONTHS):
            return (MONTHS[wtxt(i + 2)], day, 3)
        return None

    # Word form: "first", "twenty-eighth" (after normalization)
    if w0 in WORD_TO_DAY:
        day = WORD_TO_DAY[w0]
        if i + 1 < len(words) and wtxt(i + 1) in MONTHS:
            return (MONTHS[wtxt(i + 1)], day, 2)
        if (i + 2 < len(words) and wtxt(i + 1) == "of"
                and wtxt(i + 2) in MONTHS):
            return (MONTHS[wtxt(i + 2)], day, 3)
        return None

    return None


def extract_anchors_for_chapter(n: int, valid_md: set[str]) -> list[dict]:
    """Scan chapter word-by-word; accept a date match as a header if either:
      - it appears within the first SEGMENT_HEAD_WORDS words of a segment, OR
      - the word before it has a pause >= PAUSE_THRESHOLD_S.
    Keep only the first occurrence of each MM-DD per chapter."""
    path = chapter_whisper_path(n)
    data = json.loads(path.read_text())

    anchors: list[dict] = []
    seen_dates: set[str] = set()

    for seg in data.get("segments", []):
        words = seg.get("words") or []
        if not words:
            continue
        words = _normalize_words(words)
        # Flatten with segment-local word indices for the "first-N" test.
        for i, w in enumerate(words):
            match = _match_date_at(words, i)
            if not match:
                continue
            month, day, _ = match
            mm_dd = f"{month:02d}-{day:02d}"
            if mm_dd not in valid_md or mm_dd in seen_dates:
                continue
            # Header test: any of
            #   - within first SEGMENT_HEAD_WORDS of segment
            #   - preceded by a >= PAUSE_THRESHOLD_S silence
            #   - previous word ends with sentence-break punctuation
            is_head = i < SEGMENT_HEAD_WORDS
            prev = words[i - 1] if i > 0 else None
            pause = w["start"] - prev["end"] if prev else 0.0
            prev_text = prev["word"].strip() if prev else ""
            ends_with_break = bool(prev_text and prev_text[-1] in ".,:;!?—-")
            if not (is_head or pause >= PAUSE_THRESHOLD_S or ends_with_break):
                continue
            seen_dates.add(mm_dd)
            # Back off by ANCHOR_PREROLL_S to cover Whisper's late word marks,
            # but never before the previous word's end (avoid overlap with the
            # prior entry's content).
            raw_start = float(w["start"])
            earliest = prev["end"] + 0.05 if prev else 0.0
            anchor_start = max(earliest, raw_start - ANCHOR_PREROLL_S)
            anchors.append({
                "date": mm_dd,
                "chapter": n,
                "start_s": anchor_start,
                "pause_s": round(pause, 2),
                "segment_text": seg.get("text", "").strip()[:120],
            })
    return anchors


def audio_duration(path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(out.decode().strip())


def phase_anchors() -> None:
    """Find date anchors across all chapters and build per-date piece lists.

    Output schema (anchors.json):
      [{"date": "10-03", "duration_s": 5108.7,
        "pieces": [{"chapter": 21, "start_s": 10.5, "end_s": 2288.4}, ...]}]
    Pieces handle the case where a single entry spans multiple chapters
    (e.g., 10-03 spans ch21 → ch23 in the audiobook).
    """
    entries = json.loads(DRACULA_PATH.read_text())
    valid_md = {e["date"] for e in entries}

    # Chapter durations (cached).
    chapter_dur = {n: audio_duration(chapter_raw_path(n)) for n in CHAPTERS}

    # Collect per-chapter anchors and convert to a flat global timeline.
    ch_offset: dict[int, float] = {}
    cum = 0.0
    for n in CHAPTERS:
        ch_offset[n] = cum
        cum += chapter_dur[n]

    raw_anchors: list[dict] = []
    for n in CHAPTERS:
        wpath = chapter_whisper_path(n)
        if not wpath.exists():
            log.warning("[C] ch%02d transcript missing, skipping", n)
            continue
        ch_anchors = extract_anchors_for_chapter(n, valid_md)
        log.info("[C] ch%02d → %d anchors: %s",
                 n, len(ch_anchors), [a["date"] for a in ch_anchors])
        for a in ch_anchors:
            a["global_s"] = ch_offset[a["chapter"]] + a["start_s"]
            raw_anchors.append(a)

    # Sort by global time, dedupe keeping first narrative occurrence per date.
    raw_anchors.sort(key=lambda a: a["global_s"])
    seen: set[str] = set()
    unique: list[dict] = []
    for a in raw_anchors:
        if a["date"] in seen:
            continue
        seen.add(a["date"])
        unique.append(a)

    # For each unique anchor, compute the global end as the next anchor's
    # global start (or end of audiobook). Then slice into per-chapter pieces.
    total_dur = cum
    out: list[dict] = []
    for i, a in enumerate(unique):
        gs = a["global_s"]
        ge = unique[i + 1]["global_s"] if i + 1 < len(unique) else total_dur

        pieces = []
        for n in CHAPTERS:
            co = ch_offset[n]
            cend = co + chapter_dur[n]
            if cend <= gs or co >= ge:
                continue
            piece_start = max(0.0, gs - co)
            piece_end = min(chapter_dur[n], ge - co)
            if piece_end - piece_start < 0.1:
                continue
            pieces.append({
                "chapter": n,
                "start_s": round(piece_start, 3),
                "end_s": round(piece_end, 3),
            })

        out.append({
            "date": a["date"],
            "duration_s": round(ge - gs, 3),
            "pieces": pieces,
            "segment_text": a.get("segment_text", ""),
        })

    got = {a["date"] for a in out}
    missing = sorted(valid_md - got)
    log.info("[C] total anchors: %d / %d", len(got), len(valid_md))
    if missing:
        log.warning("[C] missing %d dates: %s", len(missing), missing)

    ANCHORS_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    log.info("[C] wrote %s", ANCHORS_PATH)


# ---------------------------------------------------------------------------
# Phase D: Split
# ---------------------------------------------------------------------------

def split_clip(anchor: dict) -> None:
    """Assemble one day's clip from its pieces.

    Single-piece: plain `-ss … -to … -c copy`.
    Multi-piece: slice each piece then concat via the concat demuxer.
    """
    dest = CLIPS_DIR / f"{anchor['date']}.mp3"
    pieces = anchor["pieces"]
    expected_dur = anchor["duration_s"]

    if dest.exists():
        try:
            existing_dur = audio_duration(dest)
            if abs(existing_dur - expected_dur) < 2.0:
                log.info("[D] %s already split (%.1fs), skipping",
                         anchor["date"], existing_dur)
                return
        except subprocess.CalledProcessError:
            pass

    label = " + ".join(
        f"ch{p['chapter']:02d}[{p['start_s']:.1f}→{p['end_s']:.1f}]" for p in pieces
    )
    log.info("[D] splitting %s (%.1fs): %s", anchor["date"], expected_dur, label)

    if len(pieces) == 1:
        p = pieces[0]
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(chapter_raw_path(p["chapter"])),
            "-ss", f"{p['start_s']:.3f}",
            "-to", f"{p['end_s']:.3f}",
            "-c", "copy",
            str(dest),
        ], check=True)
        return

    # Multi-piece: slice each to /tmp, then concat demuxer.
    tmpdir = CLIPS_DIR / f".tmp_{anchor['date']}"
    tmpdir.mkdir(exist_ok=True)
    try:
        piece_files = []
        for idx, p in enumerate(pieces):
            pf = tmpdir / f"p{idx:02d}.mp3"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(chapter_raw_path(p["chapter"])),
                "-ss", f"{p['start_s']:.3f}",
                "-to", f"{p['end_s']:.3f}",
                "-c", "copy",
                str(pf),
            ], check=True)
            piece_files.append(pf)
        # concat demuxer file list
        list_file = tmpdir / "list.txt"
        list_file.write_text(
            "\n".join(f"file '{pf.resolve()}'" for pf in piece_files) + "\n"
        )
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(dest),
        ], check=True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def phase_split() -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH")
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    anchors = json.loads(ANCHORS_PATH.read_text())
    for a in anchors:
        split_clip(a)
    log.info("[D] wrote %d clips to %s", len(anchors), CLIPS_DIR)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

PHASES = {
    "download": phase_download,
    "transcribe": phase_transcribe,
    "anchors": phase_anchors,
    "split": phase_split,
}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--phase", choices=[*PHASES.keys(), "all"], default="all",
    )
    args = ap.parse_args()
    if args.phase == "all":
        for name, fn in PHASES.items():
            log.info("=== Phase %s ===", name)
            fn()
    else:
        PHASES[args.phase]()


if __name__ == "__main__":
    main()
