# Dracula Daily for Slack

Posts daily excerpts of Bram Stoker's *Dracula* to a Slack channel, mirroring [Dracula Daily](https://draculadaily.com/). The novel is epistolary and spans May 3 → November 7 in-story; one message per in-novel date (96 total).

Each post is the entry's text followed by a Slack-hosted MP3 of Peter John Keeble's LibriVox solo reading — the native Slack audio player is rendered inline because the file is uploaded via `files.upload_v2`.

## How it works

Two independent flows:

1. **Audio pipeline (one-time build)** — `src/build_audio.py`
   Downloads the 27 LibriVox v5 chapters, transcribes them with Whisper, finds each date header by word-timestamp matching, and uses ffmpeg to cut 93 per-entry MP3s. 3 dates (`06-18`, `08-04`, `09-19`) have no spoken header in the source — those post text-only.
   Output: `data/audio_clips/MM-DD.mp3` (committed), `data/anchors.json` (committed).

2. **Daily post (recurring)** — `src/post_today.py`
   Reads today's entry from `data/dracula.json`, posts the body via `chat.postMessage`, then uploads the matching clip as a follow-up message via `files.upload_v2`. Triggered by cron on a home server.

The old `src/schedule.py` (monthly pre-scheduling of text-only messages via `chat.scheduleMessage`) is kept for reference but no longer the recommended path — Slack's scheduled messages can't carry files, so native inline audio requires live-upload each day.

## Repo layout

```
src/
  build_audio.py     # one-time: download → Whisper → anchors → ffmpeg split
  build_data.py      # one-time: Gutenberg text → data/dracula.json
  post_today.py      # daily: post body + upload audio
  schedule.py        # legacy text-only monthly scheduler
  slack_client.py    # thin wrapper around slack_sdk
data/
  dracula.json       # source of truth, 96 entries
  anchors.json       # date → chapter-pieces map (audio build artifact)
  audio_clips/       # 93 committed MP3s (~452 MB)
  audio_raw/         # gitignored: 27 raw chapter MP3s from Archive.org
  whisper/           # gitignored: per-chapter Whisper transcripts
scripts/
  run_post.sh        # cron wrapper: source .env, git pull, run post_today
  com.dailydracula.post.plist  # launchd plist (not used on current server — see DEPLOY.md)
  verify_clips.py    # sanity-check clip boundaries against expected date headers
tests/
  test_schedule.py
```

## Setup

### Slack app

Create at https://api.slack.com/apps → From scratch. **OAuth & Permissions** → Bot Token Scopes:

| Scope | Why |
|---|---|
| `chat:write` | post and delete messages |
| `files:write` | upload the audio clip |
| `files:read` | list files (for cleanup utilities) |
| `channels:history` | read message history (for cleanup utilities) |

Install to workspace → copy the `xoxb-…` token. Invite the bot to your target channel.

### Local env

```bash
cp .env.example .env        # fill in SLACK_BOT_TOKEN and SLACK_CHANNEL_ID
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Run once to verify:

```bash
set -a; source .env; set +a
.venv/bin/python -m src.post_today --date 05-03
```

### Deployment (home server, daily cron)

The production setup is a Mac mini running a user cron job. See **[DEPLOY.md](DEPLOY.md)** for the full recipe (SSH paths, `.env`, cron line, log location, and why launchd didn't work on that particular mac).

## Rebuilding the audio

Only needed if you change the source reading, re-transcribe, or tweak the anchor extraction.

```bash
.venv/bin/python -m src.build_audio              # runs all phases
.venv/bin/python -m src.build_audio --phase anchors   # or a single phase
```

Phases are idempotent (download/transcribe skip completed work; split re-cuts only if durations diverge). Requires: `openai-whisper`, `ffmpeg`, `requests`, and enough CPU time for Whisper (~90 min wall-clock on a modern laptop using the `small` model; ch25 was re-done with `medium` for transcript quality).

## Regenerating dracula.json

```bash
.venv/bin/python -m src.build_data
```

Reads the Project Gutenberg plaintext and emits the 96 date-keyed entries.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

## Config

| Env var | Default | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | — | `xoxb-…` from the installed Slack app |
| `SLACK_CHANNEL_ID` | — | Channel ID (not name), e.g. `C0123456789` |
| `POST_HOUR_LOCAL` | `10` | Hour of day to post (used by cron, not by `post_today.py` directly) |
| `POST_TZ` | `America/Montevideo` | Timezone used to pick today's date |
| `AUDIO_BASE_URL` | _(unset)_ | Only used by the legacy `schedule.py`; leave empty for the daily-post flow |

## Notes

- Clips are committed to git (~452 MB). `10-03.mp3` is 46 MB — GitHub's per-file soft limit is 100 MB, repo soft limit is 1 GB. Fine for now; switch to Release assets if the repo grows further.
- Ch01's `_64kb` file in the LibriVox v5 archive item is mis-uploaded v2 dramatic audio. `build_audio.py` uses the non-suffixed `dracula_01_stoker.mp3` for ch01 and `_64kb` for the rest.
- Audio boundaries are verified by `scripts/verify_clips.py`: transcribes the first 15s of each clip and checks for the expected date token. Many "FAILs" from that script are just Whisper-tiny mishearing — they're correct when cross-checked against `data/dracula.json` body starts.
