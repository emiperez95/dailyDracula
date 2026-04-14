# Dracula Daily for Slack

Posts daily excerpts of Bram Stoker's *Dracula* to a Slack channel, mirroring [Dracula Daily](https://draculadaily.com/). The novel is epistolary and spans May 3 → November 7 in-story; one message per in-novel date.

Serverless: you run `schedule.py` once a month from this machine. It uses Slack's `chat.scheduleMessage` API (120-day queue), so Slack itself holds the messages and delivers them on the right day — your laptop can be off between runs.

## Setup (one-time)

1. Create a Slack app at https://api.slack.com/apps → From scratch → `Dracula Daily`.
2. **OAuth & Permissions** → add bot scopes: `chat:write`, `chat:write.public`.
3. Install to workspace → copy the bot token (`xoxb-...`).
4. Pick the destination channel; copy its ID (right-click → Copy link; ID is the last path segment, `C...`).
5. Copy `.env.example` → `.env` and fill in `SLACK_BOT_TOKEN` and `SLACK_CHANNEL_ID`.
6. Install deps:
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

## Running it (monthly)

```bash
source .venv/bin/activate
set -a; source .env; set +a
python -m src.schedule
```

Idempotent — rerunning skips dates already in Slack's scheduled queue. Do this once per month through the May–November run.

## Data

`data/dracula.json` is the source of truth for what gets posted. It's produced once by `build_data.py` from the Project Gutenberg plaintext and committed to the repo.

Regenerate (rarely needed):
```bash
python -m src.build_data
```

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

## Config

| Env var | Default | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | — | `xoxb-...` from the installed Slack app |
| `SLACK_CHANNEL_ID` | — | Channel ID (not name), e.g. `C0123456789` |
| `POST_HOUR_LOCAL` | `10` | Hour of day to post |
| `POST_TZ` | `America/Montevideo` | Timezone for post time |
