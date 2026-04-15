"""Build data/dracula.json from the Project Gutenberg plaintext.

Run once; output is committed to the repo.

    python -m src.build_data

The novel is epistolary: journal entries, letters, and telegrams, each
tagged with a date. We walk the text line-by-line, find date anchors,
and attribute the text between two anchors to the first one. Entries
sharing an in-novel date are concatenated (with a sub-header per source)
so a single day becomes a single Slack post.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

GUTENBERG_URL = "https://www.gutenberg.org/cache/epub/345/pg345.txt"
REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "data" / "dracula.json"

MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}
MONTH_RE = "|".join(MONTHS)

# A journal-style date anchor: `_3 May. Bistritz._--Left Munich...`
# The body text starts on the same line, after the `--`.
JOURNAL_RE = re.compile(
    rf'^_(?P<day>\d{{1,2}})\s+(?P<month>{MONTH_RE})(?P<extra>[^_\n]*)_\s*[-\u2014]{{1,2}}\s*(?P<body>.*)$'
)

# A letter/telegram date line in quotes: `"_9 May._` (content follows on next lines).
# The trailing quote may be absent; a trailing `,` or `.` is permitted.
QUOTED_DATE_RE = re.compile(
    rf'^[\"\u201c]?_(?P<day>\d{{1,2}})\s+(?P<month>{MONTH_RE})(?P<extra>[^_\n]*)_[.,]?[\"\u201d]?\s*$'
)

# Chapter boundary: `CHAPTER I`, `CHAPTER XXVII`, etc.
CHAPTER_RE = re.compile(r"^CHAPTER\s+[IVXLC]+\s*$")

# A source marker such as `_Letter, Lucy Westenra to Mina Murray_.` or
# `_Telegram, Arthur Holmwood to Seward._` or `_Dr. Seward's Diary._`.
# Matches a line that is entirely wrapped in italic markers and names a
# document kind, not a date.
SOURCE_RE = re.compile(
    r'^[\"\u201c]?_(?P<src>(?:Letter|Telegram|Mem\.?|[A-Z][a-zA-Z\.\' ]+?(?:Diary|Journal|Phonograph Diary|Memorandum|Note|Report))[^_\n]*)_\.?[\"\u201d]?\s*$'
)

# End-of-book marker to crop the Gutenberg footer.
START_MARK = "*** START OF THE PROJECT GUTENBERG EBOOK"
END_MARK = "*** END OF THE PROJECT GUTENBERG EBOOK"


def fetch_text() -> str:
    with urllib.request.urlopen(GUTENBERG_URL, timeout=30) as resp:
        return resp.read().decode("utf-8")


def normalize(text: str) -> str:
    """Strip the Gutenberg wrapper; leave smart quotes intact for display."""
    try:
        s = text.index(START_MARK)
        s = text.index("\n", s) + 1
        e = text.index(END_MARK)
        text = text[s:e]
    except ValueError:
        pass
    return text


def parse_location(extra: str) -> str | None:
    """From the `extra` suffix of a date anchor (e.g. `. Bistritz`, `, morning`,
    `.`), return the location if one is present, else None.

    Period-led extras are treated as locations; comma-led extras are time
    qualifiers (`, morning`, `, before morning`) and discarded.
    """
    s = extra.strip()
    if not s:
        return None
    if s.startswith(","):
        return None
    s = s.lstrip(".").strip()
    # Drop trailing period/comma and surrounding italic/space cruft.
    s = s.rstrip(".,").strip()
    if not s:
        return None
    # Guard against weird matches: require the first char to be uppercase.
    if not s[0].isupper():
        return None
    # Time-of-day qualifiers masquerading as locations.
    if s.lower() in {"night", "morning", "evening", "afternoon", "noon", "midnight", "later", "continued"}:
        return None
    return s


def clean_title(raw: str) -> str:
    """Normalize a source/chapter title for display.

    Strips italic markers, surrounding quotes, trailing punctuation, and the
    Gutenberg `--_continued_` suffix; applies title-case to all-caps strings
    while preserving apostrophes like `'s`.
    """
    s = raw.strip()
    # Drop Gutenberg's `--_continued_` (italic) suffix
    s = re.sub(r"\s*--\s*_?continued_?\.?$", "", s, flags=re.IGNORECASE)
    # Strip leading/trailing italic markers and surrounding quotes
    s = s.strip('"\u201c\u201d').strip()
    s = s.strip("_").strip()
    # Strip trailing punctuation
    s = s.rstrip(".,").strip()
    # Title-case if all-caps, preserving `'s` / `'S` for both straight and
    # curly apostrophes (Gutenberg uses `\u2019`). Also lowercase common
    # function words when they appear mid-phrase.
    if s and s == s.upper():
        s = s.title()
        s = s.replace("'S", "'s").replace("\u2019S", "\u2019s")
        small = {"From", "By", "To", "Of", "And", "The", "In", "On", "For", "A", "An"}
        words = s.split(" ")
        s = " ".join(
            w if (i == 0 or w not in small) else w.lower()
            for i, w in enumerate(words)
        )
    return s


def _prettify(text: str) -> str:
    """Turn Gutenberg's `--` into a real em-dash for display. Only applied to
    prose text, not to anchor lines (those are already consumed by the regexes).
    """
    # Collapse three-hyphen typos first, then two-hyphen → em-dash.
    text = text.replace("---", "\u2014").replace("--", "\u2014")
    return text


def _join_paragraphs(lines: list[str]) -> str:
    """Join a list of raw lines into paragraph-preserving body text."""
    paragraphs: list[list[str]] = [[]]
    for ln in lines:
        if ln.strip() == "":
            if paragraphs[-1]:
                paragraphs.append([])
        else:
            paragraphs[-1].append(ln.strip())
    parts = [" ".join(p) for p in paragraphs if p]
    return _prettify("\n\n".join(parts).strip())


def _strip_chapter_heading(lines: list[str]) -> list[str]:
    """If the buffered body opens with a chapter heading (`CHAPTER X` then a
    blank line and an all-caps subtitle), drop it. Those lines belong to
    the chapter boundary, not the entry."""
    out = list(lines)
    while out and out[0].strip() == "":
        out.pop(0)
    if out and CHAPTER_RE.match(out[0].strip()):
        out.pop(0)
        while out and out[0].strip() == "":
            out.pop(0)
        # subtitle line (often upper-case)
        if out and out[0].strip() and out[0].strip() == out[0].strip().upper():
            out.pop(0)
    return out


def parse(text: str) -> list[dict]:
    text = normalize(text)
    lines = text.split("\n")

    # Walk through lines, tracking anchors.
    # Each "anchor" is either a date (opens a new entry) or a source/chapter
    # (updates the title for the next date).
    #
    # We collect raw body lines into `buf` between anchors and attach them
    # to the most recent DATE anchor when the next DATE anchor appears
    # (or at end of file).

    entries_by_date: dict[tuple[int, int], list[tuple[str, str, str | None]]] = defaultdict(list)

    current_source: str | None = None      # from a letter/telegram header
    current_chapter_title: str | None = None

    pending: dict | None = None  # {"date": (m, d), "title": str, "buf": [str]}

    def flush_pending():
        nonlocal pending
        if pending is None:
            return
        body_lines = _strip_chapter_heading(pending["buf"])
        body = _join_paragraphs(body_lines)
        if body:
            entries_by_date[pending["date"]].append(
                (pending["title"], body, pending.get("location"))
            )
        pending = None

    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        # Chapter boundary: update current title, clear current_source.
        if CHAPTER_RE.match(stripped):
            # The next non-blank line is the chapter subtitle (e.g.,
            # "JONATHAN HARKER'S JOURNAL" or "DR. SEWARD'S DIARY").
            if pending is not None:
                pending["buf"].append(raw)
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                current_chapter_title = clean_title(lines[j].strip())
                current_source = None
                if pending is not None:
                    # Include these heading lines in buffer so _strip_chapter_heading can remove them later.
                    for k in range(i + 1, j + 1):
                        pending["buf"].append(lines[k])
                i = j + 1
                continue
            i += 1
            continue

        # Source marker (letter/telegram/etc).
        m_src = SOURCE_RE.match(stripped)
        if m_src and not JOURNAL_RE.match(stripped) and not QUOTED_DATE_RE.match(stripped):
            current_source = clean_title(m_src.group("src"))
            if pending is not None:
                pending["buf"].append(raw)
            i += 1
            continue

        # Journal-style inline date (body on the same line).
        m_j = JOURNAL_RE.match(stripped)
        if m_j:
            flush_pending()
            mm = MONTHS[m_j.group("month")]
            dd = int(m_j.group("day"))
            title = current_chapter_title or "Dracula"
            first_body = m_j.group("body").strip()
            pending = {
                "date": (mm, dd),
                "title": title,
                "location": parse_location(m_j.group("extra")),
                "buf": [first_body] if first_body else [],
            }
            current_source = None  # consumed
            i += 1
            continue

        # Quoted date line (letter/telegram internals).
        m_q = QUOTED_DATE_RE.match(stripped)
        if m_q:
            # Some false positives are possible (e.g., a `_17, Chatham Street_`
            # line won't match because "Chatham" isn't a month). Good.
            flush_pending()
            mm = MONTHS[m_q.group("month")]
            dd = int(m_q.group("day"))
            title = current_source or current_chapter_title or "Dracula"
            pending = {
                "date": (mm, dd),
                "title": title,
                "location": parse_location(m_q.group("extra")),
                "buf": [],
            }
            current_source = None
            i += 1
            continue

        # Otherwise: body line, accumulate.
        if pending is not None:
            pending["buf"].append(raw)
        i += 1

    flush_pending()

    # Merge per-date into final entries.
    out: list[dict] = []
    for (mm, dd) in sorted(entries_by_date.keys()):
        parts = entries_by_date[(mm, dd)]
        if len(parts) == 1:
            title, body, location = parts[0]
        else:
            # Multiple sources on the same day; concatenate with sub-headers.
            # Use the first source as the headline title; take the first
            # non-empty location as the day's location.
            title = parts[0][0]
            location = next((loc for (_, _, loc) in parts if loc), None)
            body_chunks = []
            for (src, txt, _loc) in parts:
                body_chunks.append(f"*{src}*\n\n{txt}")
            body = "\n\n".join(body_chunks)
        entry: dict = {"date": f"{mm:02d}-{dd:02d}", "title": title}
        if location:
            entry["location"] = location
        entry["body"] = body
        out.append(entry)
    return out


def main() -> None:
    print(f"Fetching {GUTENBERG_URL} ...")
    text = fetch_text()
    entries = parse(text)
    OUT_PATH.parent.mkdir(exist_ok=True, parents=True)
    OUT_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(entries)} entries → {OUT_PATH.relative_to(REPO_ROOT)}")

    # Summary: count by month
    by_month: dict[int, int] = defaultdict(int)
    for e in entries:
        by_month[int(e["date"].split("-")[0])] += 1
    for m in sorted(by_month):
        print(f"  month {m:02d}: {by_month[m]} entries")


if __name__ == "__main__":
    main()
