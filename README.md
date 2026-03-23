# TF2 Match Scheduler Bot

A Discord bot for scheduling and managing TF2 Highlander mix matches with sign-ups, roster management, and automatic cleanup.

---

## Features

- `/host` — Schedule a Highlander mix (game mode → match type → division → modal)
- `/manage` — Open the manage panel for the active match in a channel
- `/edit-match` — Edit match details (time, map, server, etc.)
- `/edit-roster` — Edit a specific slot in the host team roster
- `/connect-string` — Parse and post server connect strings
- `/ping` — Ping the pug role asking for players
- Sign-up buttons with per-class accept/deny panel
- Sign-out button with last-hour warning
- Multi-class sign-ups per player
- Clash detection across concurrent mixes
- Auto thread creation per match
- Archive on conclusion/cancellation
- 1-hour match reminder pinging accepted players
- 8-hour host reminder in the hoster channel

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create your Discord bot

1. Go to https://discord.com/developers/applications → New Application
2. Bot → Add Bot
3. Enable **Message Content Intent** and **Server Members Intent**
4. Copy the token
5. OAuth2 → URL Generator — scopes: `bot`, `applications.commands`
6. Bot permissions: `Send Messages`, `Embed Links`, `Read Message History`, `Manage Messages`, `Create Public Threads`
7. Invite the bot to your server

### 3. Configure

Create `config.json` (not committed to git):

```json
{
    "token": "YOUR_BOT_TOKEN",
    "guild_id": "YOUR_SERVER_ID",
    "ongoing_matches_channel_id": "CHANNEL_ID",
    "archive_channel_id": "CHANNEL_ID",
    "hoster_channel_id": "CHANNEL_ID",
    "hoster_role_id": "ROLE_ID",
    "pug_role_id": "ROLE_ID",
    "mix_channels": [
        "MIX_SCRIMS_1_ID",
        "MIX_SCRIMS_2_ID",
        "MIX_SCRIMS_3_ID",
        "MIX_SCRIMS_4_ID",
        "MIX_SCRIMS_5_ID",
        "MIX_SCRIMS_6_ID",
        "MIX_SCRIMS_7_ID"
    ],
    "re_sort_enabled": false,
    "re_sort_interval_minutes": 30
}
```

To get IDs: enable **Developer Mode** in Discord (Settings → Advanced), then right-click any channel/role/server → Copy ID.

### 4. Run

```bash
python main.py
```

---

## File Structure

```
matchscheduler/
├── main.py         — Bot entry point, on_message handler
├── db.py           — SQLite helpers
├── embeds.py       — Message builders and constants
├── views.py        — All Discord UI (buttons, selects, modals)
├── schedule.py     — Slash commands (/host, /edit-match, /ping, etc.)
├── manage.py       — /manage command
├── scheduler.py    — Background jobs (reminders, cleanup)
├── config.json     — Your config (do not commit)
├── requirements.txt
└── matches.db      — Auto-created SQLite database
```

---

## Deployment (Oracle Cloud Free Tier)

```bash
# Clone on server
git clone https://github.com/YOURUSERNAME/matchscheduler.git
cd matchscheduler
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Create config.json manually (not in git)
nano config.json

# Run as a service
sudo systemctl enable discordbot
sudo systemctl start discordbot

# Update
git pull
sudo systemctl restart discordbot
```

---

## Commands

| Command | Description |
|---|---|
| `/host` | Schedule a new match |
| `/manage` | Manage the active match in this channel |
| `/edit-match` | Edit match details |
| `/edit-roster` | Edit a host team roster slot |
| `/connect-string` | Post server connect strings |
| `/ping` | Ping for more players |
