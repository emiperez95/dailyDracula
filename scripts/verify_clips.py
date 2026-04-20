"""Verify each clip starts with the expected date announcement.

For each anchor in data/anchors.json, transcribe the first N seconds of the
corresponding clip with Whisper 'tiny' and check that a date token matching
the entry's MM-DD appears. Reports PASS / FAIL / MAYBE per clip.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import whisper

REPO_ROOT = Path(__file__).resolve().parent.parent
ANCHORS = REPO_ROOT / "data" / "anchors.json"
CLIPS = REPO_ROOT / "data" / "audio_clips"
SAMPLE_S = 15

MONTHS = ["january", "february", "march", "april", "may", "june",
          "july", "august", "september", "october", "november", "december"]
WORDS_FOR_DAY = {
    1: ["first", "1st", "one"], 2: ["second", "2nd", "two"],
    3: ["third", "3rd", "three"], 4: ["fourth", "4th", "four"],
    5: ["fifth", "5th", "five"], 6: ["sixth", "6th", "six"],
    7: ["seventh", "7th", "seven"], 8: ["eighth", "8th", "eight"],
    9: ["ninth", "9th", "nine"], 10: ["tenth", "10th", "ten"],
    11: ["eleventh", "11th"], 12: ["twelfth", "12th"],
    13: ["thirteenth", "13th"], 14: ["fourteenth", "14th"],
    15: ["fifteenth", "15th"], 16: ["sixteenth", "16th"],
    17: ["seventeenth", "17th"], 18: ["eighteenth", "18th"],
    19: ["nineteenth", "19th"], 20: ["twentieth", "20th"],
    21: ["twenty-first", "twenty first", "21st"],
    22: ["twenty-second", "twenty second", "22nd"],
    23: ["twenty-third", "twenty third", "23rd"],
    24: ["twenty-fourth", "twenty fourth", "24th"],
    25: ["twenty-fifth", "twenty fifth", "25th"],
    26: ["twenty-sixth", "twenty sixth", "26th", "26 "],
    27: ["twenty-seventh", "twenty seventh", "27th"],
    28: ["twenty-eighth", "twenty eighth", "28th"],
    29: ["twenty-ninth", "twenty ninth", "29th"],
    30: ["thirtieth", "30th"], 31: ["thirty-first", "thirty first", "31st"],
}


def expected_tokens(mm_dd: str) -> list[str]:
    month = int(mm_dd[:2])
    day = int(mm_dd[3:])
    mn = MONTHS[month - 1]
    toks = []
    for d_form in WORDS_FOR_DAY.get(day, [str(day)]):
        toks.append(f"{d_form} of {mn}")
        toks.append(f"{d_form} {mn}")
        toks.append(f"{mn} the {d_form}")
    return [t.lower() for t in toks]


def sample_audio(clip: Path, seconds: int) -> Path:
    tmp = Path(tempfile.gettempdir()) / f"clip_head_{clip.stem}.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(clip), "-t", str(seconds), "-c", "copy", str(tmp),
    ], check=True)
    return tmp


def main() -> None:
    anchors = json.loads(ANCHORS.read_text())
    model = whisper.load_model("tiny")

    passes, fails, maybes = [], [], []

    for a in anchors:
        date = a["date"]
        clip = CLIPS / f"{date}.mp3"
        if not clip.exists():
            fails.append((date, "clip missing"))
            continue
        sample = sample_audio(clip, SAMPLE_S)
        result = model.transcribe(str(sample), language="en", verbose=False,
                                   no_speech_threshold=0.3)
        text = result["text"].strip().lower()
        text = re.sub(r"\s+", " ", text)
        hit = None
        for tok in expected_tokens(date):
            if tok in text:
                hit = tok
                break
        if hit:
            passes.append((date, hit))
            print(f"PASS {date}: '{hit}' found in: {text[:100]}")
        else:
            # Maybe partial: just the day number or just the month
            day = int(date[3:])
            month = MONTHS[int(date[:2]) - 1]
            if month in text and (str(day) in text or any(w in text for w in WORDS_FOR_DAY.get(day, []))):
                maybes.append((date, text[:100]))
                print(f"MAYBE {date}: day + month seen separately: {text[:100]}")
            else:
                fails.append((date, text[:100]))
                print(f"FAIL {date}: expected one of {expected_tokens(date)[:3]!r}")
                print(f"           got: {text[:120]!r}")

    print()
    print(f"=== {len(passes)} PASS, {len(maybes)} MAYBE, {len(fails)} FAIL (of {len(anchors)}) ===")
    if fails:
        print()
        print("Failures:")
        for d, why in fails:
            print(f"  {d}: {why}")


if __name__ == "__main__":
    main()
