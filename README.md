# TF2 Match Scheduler Bot

A Discord bot for scheduling and managing TF2 Highlander mix matches. Handles the full lifecycle of a mix — from scheduling to sign-ups, roster management, connect strings, and archiving.

---

## Commands

| Command | Who can use | Description |
|---|---|---|
| `/host` | Hoster role | Schedule a new match |
| `/manage` | Hoster role | Open the manage panel for the active match in this channel |
| `/edit` | Hoster role | Edit match details or host team roster |
| `/connect-string` | Hoster role | Paste server info and post connect strings to the channel |
| `/ping` | Hoster role | Ping the pug role asking for more players |

---

## Features

- **3-step scheduling flow** — game mode → match type → division → modal
- **Host team roster input** — type comma-separated @mentions directly in the mix channel after scheduling
- **Multi-class sign-ups** — players can sign up for multiple classes simultaneously
- **Re-signup after denial** — denied players can sign up on a different class without signing out
- **Clash detection** — warns players and notifies hosters if a player signs up for overlapping mixes (±1.5h window)
- **LP priority system** — non-LP players automatically take priority over LP players on the main roster
- **Live ongoing-matches line** — updates in real time with roster count and missing class emojis
- **Review panel** — class dropdown → player buttons to accept. Pending players listed chronologically
- **Sub promotion** — when a rostered player signs out, the highest-priority sub is promoted automatically and pinged in the thread
- **Connect string parser** — paste the full server bot message, bot extracts and posts connect/SDR/SourceTV strings
- **Archive on conclude/cancel** — posts summary + thread log to archive channel, locks original thread, cleans up all bot messages
- **24h notices** — cancellation and conclusion notices auto-delete after 24 hours
- **Reminders** — 1h before match pings rostered players; 8h after start pings original hoster to conclude

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create your Discord bot

1. Go to https://discord.com/developers/applications → New Application
2. Bot → Add Bot → copy the token
3. Enable **Message Content Intent** and **Server Members Intent**
4. OAuth2 → URL Generator — scopes: `bot`, `applications.commands`
5. Bot permissions: `Send Messages`, `Embed Links`, `Read Message History`, `Manage Messages`, `Create Public Threads`
6. Invite the bot to your server

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
    "lp_role_id": "ROLE_ID",
    "mix_channels": [
        "MIX_CHANNEL_1_ID",
        "MIX_CHANNEL_2_ID"
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
├── main.py         — Bot entry, on_message handler (roster input, connect string)
├── db.py           — SQLite CRUD helpers
├── embeds.py       — Message builders and class constants
├── views.py        — All Discord UI (buttons, selects, modals, views)
├── schedule.py     — Slash commands and ongoing-matches helpers
├── manage.py       — /manage command
├── scheduler.py    — Background reminder and cleanup jobs
├── config.json     — Config (do not commit to git)
├── requirements.txt
└── matches.db      — Auto-created SQLite database
```

---

## Deployment

The bot is designed to run as a systemd service on a Linux VPS. After cloning and configuring:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

To update:
```bash
git pull
sudo systemctl restart discordbot
```
