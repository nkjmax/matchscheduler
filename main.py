import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discord
from discord.ext import commands

from db import init_db
from scheduler import start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("bot")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
with open(CONFIG_PATH) as f:
    config = json.load(f)

TOKEN           = config["token"]
ONGOING_CHANNEL = int(config["ongoing_matches_channel_id"])
GUILD_ID        = int(config["guild_id"])
GUILD_OBJ       = discord.Object(id=GUILD_ID)


async def main():
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members         = True

    bot = commands.Bot(
        command_prefix="!",
        intents=intents,
        help_command=None,
    )

    bot.config          = config
    bot.ongoing_channel = ONGOING_CHANNEL
    bot._pending_connect = {}
    bot._pending_roster  = {}

    @bot.event
    async def on_ready():
        log.info(f"Logged in as {bot.user} ({bot.user.id})")
        start_scheduler(bot)
        bot.tree.copy_global_to(guild=GUILD_OBJ)
        synced = await bot.tree.sync(guild=GUILD_OBJ)
        log.info(f"Synced {len(synced)} commands: {[s.name for s in synced]}")

    @bot.event
    async def on_message(message):
        await bot.process_commands(message)
        if message.author.bot:
            return
        # Roster input handler — fires in the mix channel (not DMs)
        if not isinstance(message.channel, discord.DMChannel):
            import time as _time
            pending_r = bot._pending_roster.get(message.author.id)
            if pending_r and _time.time() < pending_r["expires"]:
                if message.channel.id == pending_r["channel_id"]:
                    del bot._pending_roster[message.author.id]

                    import db as _db
                    from embeds import build_mix_message
                    from views import SignupView

                    edit_class = pending_r.get("edit_class")

                    # Delete the host's message to keep the channel clean
                    try:
                        await message.delete()
                    except Exception:
                        pass

                    if edit_class:
                        # Single class edit — update just that slot in the stored roster
                        match = await _db.get_match(pending_r["match_id"])
                        from embeds import TF2_CLASSES
                        existing = match["host_roster"] or ""
                        entries  = existing.split("\n") if existing else []
                        while len(entries) < 9:
                            entries.append("")
                        idx = TF2_CLASSES.index(edit_class)
                        entries[idx] = message.content.strip()
                        await _db.update_match_fields(pending_r["match_id"], host_roster="\n".join(entries))
                    else:
                        # Full roster — split by commas
                        entries = [e.strip() for e in message.content.split(",") if e.strip()]
                        roster_str = "\n".join(entries)
                        await _db.update_match_fields(pending_r["match_id"], host_roster=roster_str)

                    # Refresh or post the match message
                    match   = await _db.get_match(pending_r["match_id"])
                    signups = await _db.get_signups_for_match(pending_r["match_id"])
                    pug_role_id = bot.config.get("pug_role_id")
                    channel = bot.get_channel(pending_r["channel_id"])

                    if channel:
                        if edit_class and match["message_id"]:
                            # Just edit the existing message
                            from embeds import build_mix_message
                            try:
                                msg = await channel.fetch_message(match["message_id"])
                                await msg.edit(content=build_mix_message(match, signups, pug_role_id=pug_role_id))
                            except Exception:
                                pass
                        else:
                            # Initial post — clear old conclusion notice and post fresh
                            prev = await _db.get_conclude_msg_for_channel(channel.id)
                            if prev and prev["conclude_msg_id"]:
                                try:
                                    old_msg = await channel.fetch_message(prev["conclude_msg_id"])
                                    await old_msg.delete()
                                except Exception:
                                    pass
                                await _db.clear_conclude_msg(prev["id"])

                            content_msg = build_mix_message(match, signups, pug_role_id=pug_role_id)
                            view        = SignupView(match["id"])
                            msg         = await channel.send(content=content_msg, view=view)
                            await _db.set_message_id(match["id"], msg.id, channel.id)

                            try:
                                thread = await msg.create_thread(
                                    name=f"{match['team_name']} vs Mix — {match['division']}",
                                    auto_archive_duration=1440,
                                )
                                await _db.set_thread_id(match["id"], thread.id)
                            except Exception:
                                pass

                            from schedule import post_to_ongoing
                            await post_to_ongoing(bot, match["id"], channel.id)

        if isinstance(message.channel, discord.DMChannel):
            import time as _time
            import re as _re
            pending = bot._pending_connect.get(message.author.id)
            if pending and _time.time() < pending["expires"]:
                del bot._pending_connect[message.author.id]

                connect = None
                sdr     = None
                tv      = None

                def parse_embeds(embeds):
                    nonlocal connect, sdr, tv
                    for embed in embeds:
                        texts = []
                        if embed.description:
                            texts.append(embed.description)
                        for field in embed.fields:
                            if field.value:
                                texts.append(field.value)
                        for text in texts:
                            blocks = _re.findall(r"`{1,3}([^`]+)`{1,3}", text)
                            for block in blocks:
                                block = block.strip()
                                if _re.match(r"connect 169\.254\.", block, _re.I):
                                    sdr = block
                                elif _re.match(r"connect [\d.]+:270\d\d", block, _re.I):
                                    tv = block
                                elif _re.match(r"connect ", block, _re.I):
                                    connect = block

                def parse_text(text):
                    nonlocal connect, sdr, tv
                    # Try backtick code blocks first
                    blocks = _re.findall(r"`{1,3}([^`]+)`{1,3}", text)
                    # Also try raw lines starting with "connect"
                    raw_lines = [l.strip() for l in text.splitlines() if l.strip().lower().startswith("connect")]
                    all_candidates = [b.strip() for b in blocks] + raw_lines
                    for block in all_candidates:
                        block = block.strip()
                        if not block:
                            continue
                        if _re.match(r"connect 169\.254\.", block, _re.I):
                            sdr = sdr or block
                        elif _re.match(r"connect [\d.]+:270\d\d", block, _re.I):
                            tv = tv or block
                        elif _re.match(r"connect ", block, _re.I):
                            connect = connect or block

                # Parse plain pasted text (forwarding doesn't work — must paste)
                if message.content:
                    parse_text(message.content)

                if not connect and not sdr:
                    await message.channel.send(
                        "❌ Couldn't find connect strings. "
                        "Try forwarding the message again, or copy-paste the full message text."
                    )
                    return

                import db as _db
                accepted = await _db.get_accepted_signups(pending["match_id"])
                seen, pings = set(), []
                for s in accepted:
                    if s["class_name"] not in seen:
                        seen.add(s["class_name"])
                        pings.append(f"<@{s['user_id']}>")

                out_lines = []
                if pings:
                    out_lines.append(" ".join(pings))
                if connect:
                    out_lines.append("**Connect String**")
                    out_lines.append(f"```{connect}```")
                if sdr:
                    out_lines.append("**SDR Connect String**")
                    out_lines.append(f"```{sdr}```")
                if tv:
                    out_lines.append("**SourceTV**")
                    out_lines.append(f"```{tv}```")

                ch = bot.get_channel(pending["channel_id"])
                if ch:
                    await ch.send("\n".join(out_lines))
                    await message.channel.send(f"✅ Posted in {ch.mention}!")
                else:
                    await message.channel.send("❌ Couldn't find the match channel.")

    @bot.command()
    async def sync(ctx):
        bot.tree.copy_global_to(guild=GUILD_OBJ)
        synced = await bot.tree.sync(guild=GUILD_OBJ)
        await ctx.send(f"Synced {len(synced)} commands: {[s.name for s in synced]}")

    async with bot:
        await init_db()
        await bot.load_extension("schedule")
        await bot.load_extension("manage")
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
