# TF2 Match Scheduler Bot

A Discord bot for scheduling TF2 PUG (9v9) and Mix matches with per-class sign-ups, host-only accept/deny panels, and automatic cleanup.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create your Discord bot

1. Go to https://discord.com/developers/applications → **New Application**
2. Go to **Bot** → **Add Bot**
3. Under **Privileged Gateway Intents**, enable **Message Content Intent**
4. Copy the **token**
5. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `Send Messages`, `Embed Links`, `Read Message History`, `Manage Messages`
6. Visit the generated URL to invite the bot to your server

### 3. Configure

Edit `config.json`:

```json
{
    "token": "YOUR_BOT_TOKEN_HERE",
    "ongoing_matches_channel_id": "RIGHT_CLICK_CHANNEL_COPY_ID",
    "match_duration_hours": 2,
    "re_sort_enabled": true,
    "re_sort_interval_minutes": 30
}
```

- `match_duration_hours` — how long after the match starts before the embed is auto-deleted
- `re_sort_enabled` — if true, re-posts embeds periodically to keep the channel sorted chronologically
- `re_sort_interval_minutes` — how often the re-sort job runs

To get a channel ID: in Discord, enable **Developer Mode** (Settings → Advanced), then right-click the channel → **Copy ID**.

### 4. Run

```bash
python main.py
```

---

## Usage

### Scheduling

| Command | What it does |
|---|---|
| `/schedule-pug` | Opens a modal form to schedule a 9v9 PUG |
| `/schedule-mix` | Opens a modal form to schedule a Mix (includes team name field) |

**Timestamp field**: go to https://sesh.fyi/timestamp/, pick your date and time, copy the Discord timestamp (e.g. `<t:1700000000:F>`), and paste it into the bot form. The embed will show both the full date/time and a "in X hours" relative time — both update automatically for every viewer.

Only the person who called the command sees the modal form.

### Managing sign-ups

When someone signs up by clicking a class button on the match embed, the **host receives a DM** with an accept/deny panel.

Hosts can also open the panel manually:

| Command | What it does |
|---|---|
| `/manage <match_id>` | Open the accept/deny panel for your match |
| `/my-matches` | List all your active matches and their IDs |

The match ID is shown in the embed footer.

### Sign-up flow

1. Users click a class button on the match embed
2. Bot records sign-up as **pending**
3. Host uses the DM panel or `/manage` to **accept** or **deny**
4. Accepted players are pinged in the embed's roster
5. For a **mix**, only 9 total sign-ups are accepted

---

## File structure

```
bot/
├── main.py           # Bot entry point
├── db.py             # SQLite helpers (aiosqlite)
├── embeds.py         # Embed builder, class constants
├── views.py          # SignupView (persistent buttons), ManageView (accept/deny)
├── scheduler.py      # APScheduler jobs
├── config.json       # Your config (DO NOT commit your token)
├── requirements.txt
├── matches.db        # Auto-created on first run
└── cogs/
    ├── schedule.py   # /schedule-pug, /schedule-mix
    └── manage.py     # /manage, /my-matches
```

---

## Notes

- **Slash command sync**: On first run, `await bot.tree.sync()` is called globally. This can take up to an hour to propagate. For faster testing, sync to a specific guild: `await bot.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))` in `schedule.py` and `manage.py`.
- **Persistent views**: Sign-up buttons survive bot restarts. On startup, `ManageCog.cog_load` re-registers all active match views.
- **Host DMs**: The bot DMs the host when a new sign-up comes in. If the host has DMs disabled, this silently fails — they can still use `/manage`.
