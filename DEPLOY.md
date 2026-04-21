# Deployment: Mac mini via cron

One recipe for hosting this. You need a machine that's on at your chosen post hour; below assumes a Mac with SSH reachable as `HOST` (substitute your own alias or `user@hostname`). Commands run as your regular user — no sudo.

## First-time install

```bash
# on the Mac mini
mkdir -p ~/Projects && cd ~/Projects
git clone https://github.com/emiperez95/dailyDracula.git
cd dailyDracula

# system Python 3.9 is fine — post_today.py is 3.9-compatible
/usr/bin/python3 -m venv .venv
.venv/bin/pip install slack-sdk
```

Copy the `.env` onto the server (from your laptop):

```bash
scp .env HOST:~/Projects/dailyDracula/.env
ssh HOST 'chmod 600 ~/Projects/dailyDracula/.env'
```

Create the log directory and install the cron entry. The full path to the wrapper is needed because cron's environment doesn't expand `~` reliably — `$HOME` works on macOS cron:

```bash
ssh HOST 'mkdir -p ~/Library/Logs/dailydracula'
ssh HOST '(crontab -l 2>/dev/null; echo "0 10 * * * \$HOME/Projects/dailyDracula/scripts/run_post.sh >> \$HOME/Library/Logs/dailydracula/post.log 2>&1") | crontab -'
```

Verify:

```bash
ssh HOST 'crontab -l | grep dailydracula'
ssh HOST 'bash ~/Projects/dailyDracula/scripts/run_post.sh --date 05-03'
ssh HOST 'tail ~/Library/Logs/dailydracula/post.log'
```

## Why cron, not launchd

A LaunchAgent plist (`scripts/com.dailydracula.post.plist`) is checked in but not used. On this mac, `launchctl bootstrap gui/$(id -u) …` returns `125: Domain does not support specified action`, and `launchctl load -w` silently no-ops. The machine's other LaunchAgents (unrelated) are also unloaded — the issue is machine-wide (likely a broken GUI launchd domain or a Full Disk Access restriction on Terminal), not this project. Cron works and is simpler over SSH.

If launchd ever starts working here, the plist is ready — just:

```
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.dailydracula.post.plist
```

…and remove the cron entry.

## Updating

The wrapper runs `git pull --ff-only` at the start of each run, so deploying a change is just `git push`. To force an immediate re-pull:

```bash
ssh HOST 'cd ~/Projects/dailyDracula && git pull --ff-only'
```

## Rotating the Slack token

```bash
# on laptop: update .env, then
scp .env HOST:~/Projects/dailyDracula/.env
ssh HOST 'chmod 600 ~/Projects/dailyDracula/.env'
```

No restart needed — the wrapper re-sources `.env` each run.

## Troubleshooting

| Symptom | Look at |
|---|---|
| No post at 10:00 | `~/Library/Logs/dailydracula/post.log` on server |
| Cron not firing | `crontab -l` on server; macOS may require Full Disk Access for `/usr/sbin/cron` if ~/ is protected |
| Slack returns `missing_scope` | verify bot has `chat:write` + `files:write`; reinstall the app |
| `git pull` fails in wrapper | wrapper logs it and continues with the committed local state |

## Gotcha: non-interactive SSH PATH

`ssh HOST '<cmd>'` loads only `~/.zshenv`. If you don't have one, `PATH` stays bare (`/usr/bin:/bin:/usr/sbin:/sbin`) and Homebrew binaries under `/opt/homebrew/bin` are unreachable. Use absolute paths when needed, or `ssh -t HOST` / `source ~/.zprofile` for an interactive shell. The cron job isn't affected because the wrapper uses absolute paths for `python` (`.venv/bin/python`) and only shells out to `git`, which is in `/usr/bin`.
