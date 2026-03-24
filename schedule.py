import re
import time
import discord
from discord import app_commands, ui
from discord.ext import commands
from dateutil.tz import gettz
from datetime import datetime

import db
from embeds import (build_mix_message, build_match_embed, build_ongoing_line,
                    build_archive_message, DIVISIONS, TF2_CLASSES, CLASS_EMOJI)
from views import SignupView

DEFAULT_TZ    = "Asia/Singapore"  # GMT+8
DATETIME_HINT = (
    "Format: DD/MM/YY HH:MM AM/PM (GMT+8)\n"
    "e.g. `25/3/25 8:00 PM`  `5/3/25 9PM`  `5/3/25 9pm`  `5/3/25 9 pm`"
)

# Accepts: 25/3/25 8PM  25/3/25 8pm  25/3/25 8:30PM  25/3/25 8:30 pm  etc.
DT_RE = re.compile(
    r"^(\d{1,2})/(\d{1,2})/(\d{2})\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)$",
    re.IGNORECASE
)

# Regex to extract connect strings from a forwarded server-bot message
CONNECT_RE = re.compile(r"connect\s+([\d.]+:\d+);\s*password\s+\"([^\"]+)\"", re.IGNORECASE)
SDR_RE     = re.compile(r"connect\s+(169\.254[\d.]+:\d+);\s*password\s+\"([^\"]+)\"", re.IGNORECASE)
TV_RE      = re.compile(r"connect\s+([\d.]+:270\d\d)(?:\s|$)", re.IGNORECASE)


def parse_datetime(raw):
    m = DT_RE.match(raw.strip())
    if not m:
        return None
    day, mon, yr2, hour, minute, ampm = m.groups()
    day, mon, year = int(day), int(mon), 2000 + int(yr2)
    hour   = int(hour)
    minute = int(minute) if minute else 0
    ampm   = ampm.upper()
    if ampm == "PM" and hour != 12:
        hour += 12
    elif ampm == "AM" and hour == 12:
        hour = 0
    try:
        dt = datetime(year, mon, day, hour, minute, tzinfo=gettz(DEFAULT_TZ))
        return int(dt.timestamp())
    except Exception:
        return None


def parse_connect_message(text):
    """
    Parse a forwarded server-bot message.
    Returns dict with keys: connect, sdr, tv (all optional strings).
    """
    result = {}
    all_connects = CONNECT_RE.findall(text)
    sdr_connects = SDR_RE.findall(text)

    sdr_ips = {ip for ip, _ in sdr_connects}

    for ip, pw in all_connects:
        if ip in sdr_ips:
            result["sdr"] = f'connect {ip}; password "{pw}"'
        else:
            result["connect"] = f'connect {ip}; password "{pw}"'

    tv_matches = TV_RE.findall(text)
    # filter out already-found connect IPs
    known = {ip for ip, _ in all_connects}
    for ip in tv_matches:
        if ip not in known:
            result["tv"] = f"connect {ip}"
            break

    return result


def is_hoster(interaction):
    role_id = interaction.client.config.get("hoster_role_id")
    if not role_id:
        return True
    return any(str(r.id) == str(role_id) for r in interaction.user.roles)


async def post_to_ongoing(bot, match_id, channel_id):
    ongoing_channel = bot.get_channel(bot.ongoing_channel)
    if not ongoing_channel:
        return
    match   = await db.get_match(match_id)
    signups = await db.get_signups_for_match(match_id)
    line    = build_ongoing_line(match, channel_id=channel_id, signups=signups)
    msg     = await ongoing_channel.send(line)
    await db.set_ongoing_msg_id(match_id, msg.id)


async def refresh_ongoing_line(bot, match_id):
    """Update the ongoing-matches line whenever the mix roster changes."""
    match = await db.get_match(match_id)
    if not match or not match["ongoing_msg_id"]:
        return
    ongoing_channel = bot.get_channel(bot.ongoing_channel)
    if not ongoing_channel:
        return
    try:
        msg     = await ongoing_channel.fetch_message(match["ongoing_msg_id"])
        signups = await db.get_signups_for_match(match_id)
        line    = build_ongoing_line(match, channel_id=match["channel_id"], signups=signups)
        await msg.edit(content=line)
    except Exception:
        pass


# ── Step 1: Game mode select ─────────────────────────────────────────────────

class GameModeSelect(ui.View):
    def __init__(self, bot):
        super().__init__(timeout=60)
        self.bot = bot
        select = ui.Select(
            placeholder="Select game mode…",
            options=[
                discord.SelectOption(label="Highlander", value="hl"),
                discord.SelectOption(label="6s", value="6s"),
                discord.SelectOption(label="Ultiduo", value="ultiduo"),
            ]
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction):
        mode = interaction.data["values"][0]
        if mode in ("6s", "ultiduo"):
            await interaction.response.edit_message(
                content="🚧 **This mode is currently under development.** Check back later!",
                view=None,
            )
            return
        # Highlander → match type select
        view = MatchTypeSelect(self.bot)
        await interaction.response.edit_message(
            content="**Step 2 of 3:** What type of match?",
            view=view,
        )


# ── Step 2: Match type select ─────────────────────────────────────────────────

class MatchTypeSelect(ui.View):
    def __init__(self, bot):
        super().__init__(timeout=60)
        self.bot = bot
        select = ui.Select(
            placeholder="Select match type…",
            options=[
                discord.SelectOption(label="Mix", value="mix", description="One team is decided, 9 sign-ups"),
                discord.SelectOption(label="Organised PUG", value="opug", description="Coming soon"),
                discord.SelectOption(label="Fresh PUG", value="fpug", description="Coming soon"),
            ]
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction):
        match_type = interaction.data["values"][0]
        if match_type in ("opug", "fpug"):
            await interaction.response.edit_message(
                content="🚧 **PUGs are currently under development.** Check back later!",
                view=None,
            )
            return
        # Mix → division select
        view = DivisionSelect(self.bot)
        await interaction.response.edit_message(
            content="**Step 3 of 3:** Select the division.",
            view=view,
        )


# ── Step 3: Division select ───────────────────────────────────────────────────

class DivisionSelect(ui.View):
    def __init__(self, bot):
        super().__init__(timeout=60)
        self.bot = bot
        options  = [discord.SelectOption(label=d, value=d) for d in DIVISIONS]
        select   = ui.Select(placeholder="Select division…", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction):
        division = interaction.data["values"][0]
        await interaction.response.send_modal(MixModal(self.bot, division))


# ── Mix modal ─────────────────────────────────────────────────────────────────

class MixModal(ui.Modal, title="Schedule a Mix"):
    team_name_input = ui.TextInput(
        label="Host team name",
        placeholder="e.g. GAY BLACK MEN",
        style=discord.TextStyle.short,
        required=True, max_length=80,
    )
    datetime_input = ui.TextInput(
        label="Date & Time (GMT+8, DD/MM/YY)",
        placeholder="e.g.  25/3/25 8:00 PM  or  5/3/25 9PM",
        style=discord.TextStyle.short,
        required=True,
    )
    map_input = ui.TextInput(
        label="Map (leave blank for TBC)",
        placeholder="e.g. cp_process_final",
        style=discord.TextStyle.short,
        required=False, max_length=60,
    )
    server_input = ui.TextInput(
        label="Server & Location",
        placeholder="e.g. Matcha Singapore  or  Serveme Europe",
        default="Matcha Singapore",
        style=discord.TextStyle.short,
        required=True, max_length=80,
    )
    notes_input = ui.TextInput(
        label="Notes (optional)",
        style=discord.TextStyle.paragraph,
        required=False, max_length=300,
    )

    def __init__(self, bot, division):
        super().__init__()
        self.bot      = bot
        self.division = division

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)

        unix      = parse_datetime(self.datetime_input.value.strip())
        team_name = self.team_name_input.value.strip()

        if unix is None:
            await interaction.followup.send(
                f"❌ Couldn't parse that date/time.\n{DATETIME_HINT}", ephemeral=True
            )
            return
        if unix < time.time():
            await interaction.followup.send(
                "❌ That date/time is in the past.", ephemeral=True
            )
            return

        map_name    = self.map_input.value.strip() or "tbc"
        server      = self.server_input.value.strip()
        notes       = self.notes_input.value.strip() or None
        pug_role_id = self.bot.config.get("pug_role_id")

        occupied     = await db.get_active_channel_ids()
        mix_channels = [int(cid) for cid in self.bot.config.get("mix_channels", [])]
        channel_id   = next((cid for cid in mix_channels if cid not in occupied), None)

        if channel_id is None:
            await interaction.followup.send(
                "❌ All mix channels are currently occupied.", ephemeral=True
            )
            return

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            await interaction.followup.send(
                "❌ Could not access the mix channel. Check config.json.", ephemeral=True
            )
            return

        match_id = await db.create_match(
            type_="mix", timestamp=unix,
            created_by=interaction.user.id,
            created_by_name=interaction.user.display_name,
            team_name=team_name, notes=notes,
            division=self.division, map_name=map_name,
            server=server, pug_role_id=pug_role_id,
        )

        # Store pending roster context — bot will post the match after host types roster
        interaction.client._pending_roster[interaction.user.id] = {
            "match_id":   match_id,
            "channel_id": channel.id,
            "bot":        self.bot,
            "expires":    time.time() + 300,
        }

        await interaction.followup.send(
            f"✅ Match created! Now **type your host team roster in {channel.mention}**\n"
            "List 9 players separated by commas in class order (Scout to Spy).\n"
            "@mentions and plain text both work. The match will be posted once you send it.\n"
            "Example: `@kaldoz, @aboood, @aswero21, @mackey, mugen, @surge, tbc, tbc, tbc`",
            ephemeral=True,
        )


# ── Edit modal ────────────────────────────────────────────────────────────────

class EditMixModal(ui.Modal, title="Edit Match Details"):
    team_name_input = ui.TextInput(
        label="Team name — blank to keep current",
        style=discord.TextStyle.short,
        required=False, max_length=80,
    )
    datetime_input = ui.TextInput(
        label="Date & Time (DD/MM/YY) — blank to keep",
        placeholder="e.g.  25/3/25 8:00 PM",
        style=discord.TextStyle.short,
        required=False,
    )
    map_input = ui.TextInput(
        label="Map — blank to keep current",
        style=discord.TextStyle.short,
        required=False, max_length=60,
    )
    server_input = ui.TextInput(
        label="Server & Location — blank to keep current",
        style=discord.TextStyle.short,
        required=False, max_length=80,
    )
    notes_input = ui.TextInput(
        label="Notes — blank to keep current",
        style=discord.TextStyle.paragraph,
        required=False, max_length=300,
    )

    def __init__(self, match_id, bot):
        super().__init__()
        self.match_id = match_id
        self.bot      = bot

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)

        updates = {}
        if self.team_name_input.value.strip():
            updates["team_name"] = self.team_name_input.value.strip()
        if self.datetime_input.value.strip():
            unix = parse_datetime(self.datetime_input.value.strip())
            if unix is None:
                await interaction.followup.send(
                    f"❌ Couldn't parse date/time.\n{DATETIME_HINT}", ephemeral=True
                )
                return
            if unix < time.time():
                await interaction.followup.send(
                    "❌ That date/time is in the past.", ephemeral=True
                )
                return
            updates["timestamp"] = unix
        if self.map_input.value.strip():
            updates["map_name"] = self.map_input.value.strip()
        if self.server_input.value.strip():
            updates["server"] = self.server_input.value.strip()
        if self.notes_input.value.strip():
            updates["notes"] = self.notes_input.value.strip()

        if not updates:
            await interaction.followup.send(
                "No changes — all fields were blank.", ephemeral=True
            )
            return

        await db.update_match_fields(self.match_id, **updates)

        match   = await db.get_match(self.match_id)
        signups = await db.get_signups_for_match(self.match_id)
        pug_role_id = self.bot.config.get("pug_role_id")

        try:
            channel = interaction.client.get_channel(match["channel_id"])
            msg     = await channel.fetch_message(match["message_id"])
            content = build_mix_message(match, signups, pug_role_id=pug_role_id)
            await msg.edit(content=content)
        except Exception as e:
            await interaction.followup.send(
                f"⚠️ Details saved but couldn't refresh message: {e}", ephemeral=True
            )
            return

        await interaction.followup.send("✅ Match details updated.", ephemeral=True)


# ── PUG modal ─────────────────────────────────────────────────────────────────

class PugModal(ui.Modal, title="Schedule a PUG (9v9)"):
    datetime_input = ui.TextInput(
        label="Date & Time (GMT+8, DD/MM/YY)",
        placeholder="e.g.  25/3/25 8:00 PM  or  5/3/25 9PM",
        style=discord.TextStyle.short,
        required=True,
    )
    notes_input = ui.TextInput(
        label="Notes (optional)",
        style=discord.TextStyle.paragraph,
        required=False, max_length=500,
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction):
        unix = parse_datetime(self.datetime_input.value.strip())
        if unix is None:
            await interaction.response.send_message(
                f"❌ Couldn't parse that date/time.\n{DATETIME_HINT}", ephemeral=True
            )
            return
        if unix < time.time():
            await interaction.response.send_message(
                "❌ That date/time is in the past.", ephemeral=True
            )
            return

        notes    = self.notes_input.value.strip() or None
        match_id = await db.create_match(
            type_="pug", timestamp=unix,
            created_by=interaction.user.id,
            created_by_name=interaction.user.display_name,
            notes=notes,
        )
        channel = self.bot.get_channel(self.bot.ongoing_channel)
        if channel is None:
            await interaction.response.send_message(
                "❌ Could not find the ongoing-matches channel.", ephemeral=True
            )
            return
        match = await db.get_match(match_id)
        embed = build_match_embed(match, [])
        view  = SignupView(match_id)
        msg   = await channel.send(embed=embed, view=view)
        await db.set_message_id(match_id, msg.id, channel.id)
        await interaction.response.send_message(
            f"✅ PUG posted to {channel.mention}!", ephemeral=True
        )


# ── Cog ───────────────────────────────────────────────────────────────────────

# ── Edit select ───────────────────────────────────────────────────────────────

class EditSelectView(ui.View):
    def __init__(self, match_id, bot):
        super().__init__(timeout=60)
        self.match_id = match_id
        self.bot      = bot
        select = ui.Select(
            placeholder="Select what to edit...",
            options=[
                discord.SelectOption(label="Match details", value="match", description="Time, map, server, notes"),
                discord.SelectOption(label="Host team roster", value="roster", description="Edit a class slot"),
            ]
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction):
        choice = interaction.data["values"][0]
        if choice == "match":
            await interaction.response.send_modal(EditMixModal(self.match_id, self.bot))
        else:
            view = EditRosterClassSelect(self.match_id, self.bot)
            await interaction.response.edit_message(
                content="Which class slot would you like to edit?", view=view
            )


# ── Edit roster class select ──────────────────────────────────────────────────

class EditRosterClassSelect(ui.View):
    def __init__(self, match_id, bot):
        super().__init__(timeout=60)
        self.match_id = match_id
        self.bot      = bot
        options = [
            discord.SelectOption(label=cls, value=cls, emoji=CLASS_EMOJI[cls])
            for cls in TF2_CLASSES
        ]
        select = ui.Select(placeholder="Select class to edit…", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction):
        cls = interaction.data["values"][0]
        # Store pending edit context
        interaction.client._pending_roster[interaction.user.id] = {
            "match_id":   self.match_id,
            "channel_id": interaction.channel_id,
            "edit_class": cls,
            "expires":    time.time() + 120,
        }
        await interaction.response.edit_message(
            content=f"Type the player for **{cls}** in this channel — @mention or plain text.",
            view=None,
        )


# ── Roster prompt view ────────────────────────────────────────────────────────

class RosterPromptView(ui.View):
    def __init__(self, match_id, bot):
        super().__init__(timeout=300)
        self.match_id = match_id
        self.bot      = bot

    @ui.button(label="Fill in host team roster", style=discord.ButtonStyle.primary)
    async def fill_roster(self, interaction, button):
        await interaction.response.send_modal(TeamRosterModal(self.match_id, self.bot))

    @ui.button(label="Skip for now", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction, button):
        await interaction.response.edit_message(
            content="Skipped. You can fill in the roster later with /edit-match.", view=None
        )


# ── Team roster modal ─────────────────────────────────────────────────────────

class TeamRosterModal(ui.Modal, title="Host Team Roster"):
    roster_input = ui.TextInput(
        label="Players in class order (one per line)",
        placeholder=(
            "Scout: @player1\n"
            "Soldier: @player2\n"
            "Pyro: @player3\n"
            "Demo: @player4\n"
            "Heavy: @player5\n"
            "Engi: @player6\n"
            "Medic: @player7\n"
            "Sniper: @player8\n"
            "Spy: mugen (not in server)"
        ),
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    def __init__(self, match_id, bot):
        super().__init__()
        self.match_id = match_id
        self.bot      = bot

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)

        raw = self.roster_input.value.strip()

        # Strip optional "ClassName: " prefixes so user can type them or not
        import re as _re
        lines = []
        for line in raw.splitlines():
            # Remove leading "Scout:", "Soldier:", etc. if present
            cleaned = _re.sub(r"^[^:]+:\s*", "", line).strip()
            lines.append(cleaned)

        # Store as newline-separated string
        roster_str = "\n".join(lines) if lines else None
        await db.update_match_fields(self.match_id, host_roster=roster_str)

        # Refresh the match message
        match   = await db.get_match(self.match_id)
        signups = await db.get_signups_for_match(self.match_id)
        pug_role_id = self.bot.config.get("pug_role_id")
        try:
            channel = interaction.client.get_channel(match["channel_id"])
            msg     = await channel.fetch_message(match["message_id"])
            await msg.edit(content=build_mix_message(match, signups, pug_role_id=pug_role_id))
        except Exception as e:
            await interaction.followup.send(f"⚠️ Saved but couldn't refresh message: {e}", ephemeral=True)
            return

        await interaction.followup.send("✅ Host team roster updated!", ephemeral=True)


# ── Connect string modal ──────────────────────────────────────────────────────

class ConnectModal(ui.Modal, title="Post Connect String"):
    raw_input = ui.TextInput(
        label="Paste the full server message here",
        placeholder="Paste everything — connect strings will be extracted automatically.",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=2000,
    )

    def __init__(self, match_id):
        super().__init__()
        self.match_id = match_id

    async def on_submit(self, interaction):
        import re as _re
        await interaction.response.defer(ephemeral=True)

        text = self.raw_input.value
        connect = None
        sdr     = None
        tv      = None

        candidates = []
        candidates += [b.strip() for b in _re.findall(r"`{1,3}([^`]+)`{1,3}", text)]
        candidates += [l.strip() for l in text.splitlines() if l.strip().lower().startswith("connect")]

        for c in candidates:
            if not c:
                continue
            if _re.match(r"connect 169\.254\.", c, _re.I):
                sdr = sdr or c
            elif _re.match(r"connect [\d.]+:270\d\d", c, _re.I):
                tv = tv or c
            elif _re.match(r"connect ", c, _re.I):
                connect = connect or c

        if not connect and not sdr:
            await interaction.followup.send(
                "❌ Couldn't find any connect strings. "
                "Make sure the text contains lines starting with `connect`.",
                ephemeral=True,
            )
            return

        match   = await db.get_match(self.match_id)
        channel = interaction.client.get_channel(match["channel_id"])

        accepted = await db.get_accepted_signups(self.match_id)
        seen, pings = set(), []
        for s in accepted:
            if s["class_name"] not in seen:
                seen.add(s["class_name"])
                pings.append(f"<@{s['user_id']}>")

        out_lines = []
        if connect:
            out_lines.append("**Connect String**")
            out_lines.append(f"```{connect}```")
        if sdr:
            out_lines.append("**SDR Connect String**")
            out_lines.append(f"```{sdr}```")
        if tv:
            out_lines.append("**SourceTV**")
            out_lines.append(f"```{tv}```")
        if pings:
            out_lines.append(" ".join(pings))

        await channel.send("\n".join(out_lines))
        await interaction.followup.send("✅ Connect string posted!", ephemeral=True)


class ScheduleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="host", description="Host a match.")
    async def host(self, interaction):
        if not is_hoster(interaction):
            await interaction.response.send_message(
                "❌ You need the hoster role to use this command.", ephemeral=True
            )
            return
        view = GameModeSelect(self.bot)
        await interaction.response.send_message(
            "**Step 1 of 3:** Select the game mode.",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="edit", description="Edit match details or host team roster.")
    async def edit(self, interaction):
        if not is_hoster(interaction):
            await interaction.response.send_message(
                "❌ You need the hoster role to use this command.", ephemeral=True
            )
            return
        match = await db.get_match_by_channel(interaction.channel_id)
        if not match:
            await interaction.response.send_message(
                "❌ No active match in this channel.", ephemeral=True
            )
            return
        view = EditSelectView(match["id"], self.bot)
        await interaction.response.send_message(
            "What would you like to edit?", view=view, ephemeral=True
        )



    @app_commands.command(name="connect-string", description="Parse and post server connect strings.")
    async def connect_string(self, interaction):
        if not is_hoster(interaction):
            await interaction.response.send_message(
                "❌ You need the hoster role to use this command.", ephemeral=True
            )
            return
        match = await db.get_match_by_channel(interaction.channel_id)
        if not match:
            await interaction.response.send_message(
                "❌ No active match in this channel.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ConnectModal(match["id"]))



    @app_commands.command(name="ping", description="Ping the pug role asking for players.")
    async def ping_players(self, interaction):
        if not is_hoster(interaction):
            await interaction.response.send_message(
                "❌ You need the hoster role to use this command.", ephemeral=True
            )
            return
        match = await db.get_match_by_channel(interaction.channel_id)
        if not match:
            await interaction.response.send_message(
                "❌ No active match in this channel.", ephemeral=True
            )
            return

        pug_role_id = self.bot.config.get("pug_role_id")
        pug_ping    = f"<@&{pug_role_id}>" if pug_role_id else "@here"

        # Find unfilled main roster slots
        from embeds import TF2_CLASSES, CLASS_EMOJI
        signups  = await db.get_signups_for_match(match["id"])
        accepted = {}
        for s in signups:
            if s["status"] == "accepted" and s["class_name"] not in accepted:
                accepted[s["class_name"]] = s["user_id"]

        unfilled = [cls for cls in TF2_CLASSES if cls not in accepted]
        filled   = len(TF2_CLASSES) - len(unfilled)

        if filled >= 5:
            class_emojis = ", ".join(CLASS_EMOJI[cls] for cls in unfilled)
            msg = f"{pug_ping} Just {class_emojis} left"
        else:
            team = match["team_name"] or "Mix"
            msg  = f"{pug_ping} Need players against {team}"

        await interaction.response.send_message("✅ Pinged!", ephemeral=True)
        channel = self.bot.get_channel(match["channel_id"])
        if channel:
            await channel.send(msg)


async def setup(bot):
    await bot.add_cog(ScheduleCog(bot))
