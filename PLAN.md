# Dracula Daily for Slack — Implementation Plan

## Concept

A Slack bot that posts daily excerpts of Bram Stoker's *Dracula* (1897, public domain) to a single company channel, mirroring the popular [Dracula Daily](https://draculadaily.com/) newsletter. The novel is epistolary and spans **May 3 → November 7** in-story; each day's entry corresponds to that calendar date.

Goal: an async, company-wide book-club moment. People follow along, react, discuss in threads.

## Architecture (no long-running server)

```
┌──────────────────┐   monthly (manual   ┌──────────────────────┐
│ Local machine    │    or launchd)      │ schedule.py          │
│ (run from repo)  │ ──────────────────▶ │ - reads dracula.json │
└──────────────────┘                     │ - lists existing     │
                                         │   scheduled msgs     │
                                         │ - schedules new ones │
                                         │   in next 120 days   │
                                         └──────────┬───────────┘
                                                    │ chat.scheduleMessage
                                                    ▼
                                         ┌──────────────────────┐
                                         │ Slack (holds queue,  │
                                         │ posts on date)       │
                                         └──────────────────────┘
```

No server. Run `schedule.py` from this machine once a month; Slack itself holds the queue and delivers on the right dates. The laptop can be off between runs.

## Slack API constraints (verified)

- `chat.scheduleMessage`: max **120 days** in future per message
- Per-channel pacing: max 30 scheduled messages per 5-min window
- Soft cap: ~2,000 scheduled messages per channel (we'll use ~180 across the run)
- Idempotency: use `chat.scheduledMessages.list` to skip dupes on re-runs

## Repo layout

```
dracula-daily-slack/
├── README.md                    # setup + how to run
├── PLAN.md                      # this file
├── pyproject.toml               # or requirements.txt — Python 3.11+
├── data/
│   └── dracula.json             # [{date, title, body}, ...] ~180 entries
├── src/
│   ├── schedule.py              # main scheduler (idempotent)
│   ├── build_data.py            # one-time: builds dracula.json from Gutenberg
│   └── slack_client.py          # thin wrapper around chat.scheduleMessage
└── tests/
    └── test_schedule.py         # unit tests for date filtering + idempotency
```

## Data file format (`data/dracula.json`)

```json
[
  {
    "date": "05-03",
    "title": "Jonathan Harker's Journal",
    "body": "3 May. Bistritz.—Left Munich at 8:35 P.M., on 1st May, ..."
  },
  {
    "date": "05-04",
    "title": "Jonathan Harker's Journal",
    "body": "..."
  }
]
```

- `date` is `MM-DD` (year-agnostic — the bot runs every year)
- `body` is the full excerpt, can contain multiple sub-entries from the same in-novel date
- Skip days with no content entirely (don't include empty entries)

## Slack message format

Markdown / Slack `mrkdwn`:

```
:closed_book: *Dracula — May 3* · _Jonathan Harker's Journal_

3 May. Bistritz.—Left Munich at 8:35 P.M., on 1st May, arriving at Vienna early next morning...

:speech_balloon: _Reply in thread to discuss._
```

For longer excerpts, use Slack Block Kit with a `section` block (supports up to 3000 chars per text field; split into multiple blocks if needed).

## Configuration (env vars)

| Var | Description |
|---|---|
| `SLACK_BOT_TOKEN` | `xoxb-...` from the installed Slack app |
| `SLACK_CHANNEL_ID` | e.g. `C0123456789` (channel ID, not name) |
| `POST_HOUR_LOCAL` | default `10` (10 AM) |
| `POST_TZ` | default `America/Montevideo` (GMT-3) |

Time math: the script computes `post_at` as a Unix timestamp by converting `{date} {POST_HOUR_LOCAL}:00` in `POST_TZ` to UTC, then to epoch.

## Scheduler logic (`schedule.py`)

```
1. Load data/dracula.json
2. today = current date in POST_TZ
3. window_end = today + 120 days
4. eligible = [e for e in entries if today <= entry_date_this_year(e) <= window_end]
5. existing = slack.chat_scheduledMessages_list(channel=SLACK_CHANNEL_ID)
   → set of post_at timestamps already scheduled
6. for entry in eligible:
       post_at = compute_post_at(entry.date, POST_HOUR_LOCAL, POST_TZ)
       if post_at in existing: skip
       slack.chat_scheduleMessage(channel=..., text=..., post_at=post_at)
       sleep(0.2)  # respect 30/5min rate limit
7. log summary: scheduled X new, skipped Y dupes
```

**Edge case: year rollover.** Entries span May → Nov, so within any given calendar year there's no rollover issue. The script just uses the current year for all dates.

**Edge case: re-runs after edits.** If you edit `dracula.json` after messages are scheduled, you'll need to delete the old scheduled messages via `chat.deleteScheduledMessage` and re-run. Add a `--force` flag for this later if needed; not needed for MVP.

## Running it

Local, manual, once a month:

```bash
# from repo root, with a .env file holding SLACK_BOT_TOKEN + SLACK_CHANNEL_ID
set -a; source .env; set +a
python -m src.schedule
```

`.env` is gitignored. Any month, re-run the command — idempotency prevents dupes.

**Optional: automate with launchd.** A `~/Library/LaunchAgents/com.dracula-daily.plist` with `StartCalendarInterval` on day 1 of each month can run it unattended when the Mac is awake. Skipped for MVP; triggering manually is fine for ~7 runs across the May–Nov cycle.

## Slack app setup (one-time, manual)

1. https://api.slack.com/apps → Create New App → From scratch
2. Name: `Dracula Daily`
3. **OAuth & Permissions** → add bot scopes:
   - `chat:write`
   - `chat:write.public` (so you don't need to invite to public channels)
4. Install to workspace → copy bot token (`xoxb-...`)
5. Create or pick the destination channel; right-click → Copy link → channel ID is the last path segment (`C...`)
6. Put both in a local `.env` file at the repo root: `SLACK_BOT_TOKEN=xoxb-...` and `SLACK_CHANNEL_ID=C...`

## Building `data/dracula.json` (one-time)

Source: [Project Gutenberg — Dracula](https://www.gutenberg.org/ebooks/345) (plain text)

`build_data.py` approach:
1. Download Gutenberg plain text
2. Parse by chapter, then by date headers (the novel uses formats like `"3 May."`, `"3 May, Bistritz."`, `"4 May, Castle Dracula."`)
3. Group consecutive paragraphs under the most recent date header
4. Where multiple chapters/correspondents have entries on the same date, concatenate them under one date with sub-headers
5. Output `data/dracula.json` sorted by date

Validation: cross-check entry count against Matt Kirkland's published schedule (~180 entries May 3 → Nov 7, with gaps).

This script is run **once**, output committed to the repo. No need to re-run unless we want to re-derive.

## MVP scope (don't gold-plate)

**In:**
- Schedule messages by running `schedule.py` locally, once a month
- Idempotent re-runs
- One channel
- Plain text excerpts with simple formatting
- "Reply in thread" footer

**Out (defer):**
- Auto-thread bot
- Reaction pre-seeding
- `/dracula today` slash command
- Per-user DM opt-in
- Multi-channel support
- Web dashboard

## Implementation order

1. Repo scaffold + `pyproject.toml` + README skeleton
2. Slack app set up manually, token saved to local `.env`
3. `slack_client.py` wrapper + smoke test (post a single message to a test channel)
4. `build_data.py` → produce `data/dracula.json` and commit
5. `schedule.py` with idempotency + tests
6. Run locally → verify ~120 days of messages appear in Slack scheduled queue
7. Wait for May 3, watch first message land

## Effort estimate

| Task | Time |
|---|---|
| Slack app setup | 15 min |
| Repo scaffold | 30 min |
| `slack_client.py` + smoke test | 30 min |
| `build_data.py` (Gutenberg parsing) | 1–2 hr |
| `schedule.py` + tests | 1 hr |
| End-to-end test | 30 min |
| **Total** | **~4 hr** |

## Open questions (none blocking — defaults locked)

All locked:
- Repo: personal GitHub, name TBD (suggested: `dracula-daily-slack`)
- Time: 10:00 America/Montevideo (= 13:00 UTC)
- Cadence: monthly cron
- Threads: simple footer, no auto-thread bot
- Source: Project Gutenberg, parse once
- Language: Python (simpler than Node for this)

## References

- [Dracula Daily — Wikipedia](https://en.wikipedia.org/wiki/Dracula_Daily)
- [Slack `chat.scheduleMessage` docs](https://docs.slack.dev/reference/methods/chat.scheduleMessage/)
- [Slack `chat.scheduledMessages.list` docs](https://docs.slack.dev/reference/methods/chat.scheduledMessages.list/)
- [Project Gutenberg — Dracula](https://www.gutenberg.org/ebooks/345)
