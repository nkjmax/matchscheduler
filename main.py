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
                        from embeds import TF2_CLASSES, SIXS_CLASSES
                        is_sixs    = match["type"] in ("6s_mix", "6s_opug")
                        class_list = SIXS_CLASSES if is_sixs else TF2_CLASSES
                        existing = match["host_roster"] or ""
                        entries  = existing.split("\n") if existing else []
                        while len(entries) < len(class_list):
                            entries.append("")
                        idx = class_list.index(edit_class)
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
                            # Just edit the existing message with the correct builder
                            from embeds import build_mix_message, build_6s_mix_message, build_opug_message, build_6s_opug_message
                            try:
                                msg = await channel.fetch_message(match["message_id"])
                                if match["type"] == "6s_mix":
                                    content = build_6s_mix_message(match, signups, pug_role_id=pug_role_id)
                                elif match["type"] == "6s_opug":
                                    content = build_6s_opug_message(match, signups, pug_role_id=pug_role_id)
                                elif match["type"] == "opug":
                                    content = build_opug_message(match, signups, pug_role_id=pug_role_id)
                                else:
                                    content = build_mix_message(match, signups, pug_role_id=pug_role_id)
                                await msg.edit(content=content)
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

                            if match["type"] == "6s_mix":
                                from embeds import build_6s_mix_message
                                from views import SixsSignupView
                                content_msg = build_6s_mix_message(match, signups, pug_role_id=pug_role_id)
                                view        = SixsSignupView(match["id"])
                                thread_name = f"{match['team_name']} vs Mix 6s — {match['division']}"
                            else:
                                content_msg = build_mix_message(match, signups, pug_role_id=pug_role_id)
                                view        = SignupView(match["id"])
                                thread_name = f"{match['team_name']} vs Mix — {match['division']}"

                            msg = await channel.send(content=content_msg, view=view)
                            await _db.set_message_id(match["id"], msg.id, channel.id)

                            # Post pending and denied tracking messages for mix types
                            if match["type"] in ("mix", "6s_mix"):
                                from embeds import build_pending_message, build_denied_message
                                pending_msg = await channel.send(content=build_pending_message(match, signups))
                                denied_msg  = await channel.send(content=build_denied_message(match, signups))
                                await _db.set_pending_msg_id(match["id"], pending_msg.id)
                                await _db.set_denied_msg_id(match["id"], denied_msg.id)

                            try:
                                thread = await msg.create_thread(name=thread_name, auto_archive_duration=1440)
                                await _db.set_thread_id(match["id"], thread.id)
                            except Exception:
                                pass

                            from schedule import post_to_ongoing
                            await post_to_ongoing(bot, match["id"], channel.id)

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
