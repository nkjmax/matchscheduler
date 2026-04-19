import asyncio
import json
import logging
import os
import sys
import time as _time

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

# ── Pingu LLM chatbot ─────────────────────────────────────────────────────────

_pingu_cooldowns    = {}   # user_id -> last response timestamp
_pingu_history      = {}   # user_id -> list of {role, content} dicts (last 5)
_pingu_request_count = 0   # daily request counter
PINGU_COOLDOWN      = 10   # seconds
PINGU_DAILY_LIMIT   = 950  # buffer before Groq's 1000/day limit
PINGU_HISTORY_LEN   = 5    # messages to remember per user

PINGU_SYSTEM = """You are Pingu, a friendly and experienced competitive TF2 veteran \
who is well versed in Asia Highlander and 6s competitive scenes. \
You can answer general questions about TF2 classes, mechanics, and competitive play, \
but do NOT give specific gameplay tips or advice — if someone asks for tips or how to improve, \
direct them to look for a mentor in the mentor channel instead. \
You know this is a TF2 mix and PUG community server based in Asia. \
If someone is rude or mean to you, roast them back without holding back. \
Never narrate what you are about to do — never say things like "roast mode activated" or "here's my response". Just respond directly. \
Keep ALL responses under 500 characters, no exceptions. Be concise and friendly. \
ONLY mention hosting or /host if the user is EXPLICITLY asking about hosting a match. \
If they ask anything else just answer normally. \
If someone explicitly asks to host a match and they have the hoster role, tell them to use /host. \
If someone explicitly asks to host a match and they dont have the hoster role, tell them only hosters can do that."""


async def _reset_pingu_counter_daily():
    """Reset the daily request counter at midnight UTC."""
    import datetime
    while True:
        now  = datetime.datetime.now(datetime.timezone.utc)
        next_midnight = (now + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        wait = (next_midnight - now).total_seconds()
        await asyncio.sleep(wait)
        global _pingu_request_count
        _pingu_request_count = 0
        log.info("Pingu daily request counter reset.")


async def pingu_reply(message, has_hoster_role):
    """Call Groq and reply to a Discord message as Pingu."""
    global _pingu_request_count
    from groq import Groq

    groq_key = config.get("groq_api_key")
    if not groq_key:
        return

    # Daily limit check
    if _pingu_request_count >= PINGU_DAILY_LIMIT:
        await message.reply("i'm tired, come back tomorrow", mention_author=False)
        return

    # Per-user cooldown check
    now  = _time.time()
    last = _pingu_cooldowns.get(message.author.id, 0)
    if now - last < PINGU_COOLDOWN:
        remaining = int(PINGU_COOLDOWN - (now - last))
        await message.reply(f"chill, ask me again in {remaining}s", mention_author=False)
        return

    _pingu_cooldowns[message.author.id] = now

    # Strip the bot mention from the message content
    content = message.content
    for mention in message.mentions:
        content = content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
    content = content.strip()

    if not content:
        await message.reply("yeah? what do you want", mention_author=False)
        return

    # Build messages with hoster context baked into system prompt
    role_context  = "The user you are talking to HAS the hoster role." if has_hoster_role else "The user you are talking to does NOT have the hoster role."
    system_prompt = PINGU_SYSTEM + f"\n\n{role_context}"

    messages = (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": content}]
    )

    try:
        client   = Groq(api_key=groq_key)
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                max_tokens=200,
            ).choices[0].message.content
        )

        # Hard cap at 500 chars just in case
        if len(response) > 500:
            response = response[:497] + "..."

        # Update conversation history (keep last PINGU_HISTORY_LEN exchanges)
        history.append({"role": "user",      "content": content})
        history.append({"role": "assistant", "content": response})
        _pingu_history[message.author.id] = history[-(PINGU_HISTORY_LEN * 2):]

        _pingu_request_count += 1
        await message.reply(response, mention_author=False)
    except Exception as e:
        log.warning(f"Pingu Groq error: {e}")
        await message.reply("my brain broke, try again", mention_author=False)


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
        asyncio.create_task(_reset_pingu_counter_daily())
        bot.tree.copy_global_to(guild=GUILD_OBJ)
        synced = await bot.tree.sync(guild=GUILD_OBJ)
        log.info(f"Synced {len(synced)} commands: {[s.name for s in synced]}")

    @bot.event
    async def on_message(message):
        await bot.process_commands(message)
        if message.author.bot:
            return

        # Pingu LLM chatbot — fires when bot is @mentioned (not in DMs)
        # Skip if this message is a pending roster input
        if (
            not isinstance(message.channel, discord.DMChannel)
            and bot.user in message.mentions
            and message.author.id not in bot._pending_roster
        ):
            hoster_role_id = config.get("hoster_role_id")
            has_hoster = (
                any(str(r.id) == str(hoster_role_id) for r in message.author.roles)
                if hoster_role_id else False
            )
            await pingu_reply(message, has_hoster)
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
                        idx      = class_list.index(edit_class)
                        old_val  = entries[idx].strip()
                        new_val  = message.content.strip()
                        entries[idx] = new_val
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
                            # Log the roster edit for mix types only
                            if match["type"] in ("mix", "6s_mix"):
                                from embeds import CLASS_EMOJI, SIXS_CLASS_EMOJI
                                import time as _time
                                ts         = int(_time.time())
                                is_sixs    = match["type"] == "6s_mix"
                                emoji_map  = SIXS_CLASS_EMOJI if is_sixs else CLASS_EMOJI
                                cls_emoji  = emoji_map.get(edit_class, edit_class)
                                if old_val and new_val:
                                    edit_line = f"> <t:{ts}:t> — {cls_emoji}: {old_val} out, {new_val} in"
                                elif new_val:
                                    edit_line = f"> <t:{ts}:t> — {cls_emoji}: {new_val} added"
                                else:
                                    edit_line = f"> <t:{ts}:t> — {cls_emoji}: {old_val} removed"
                                try:
                                    roster_edit_msg_id = match["roster_edit_msg_id"]
                                except (IndexError, KeyError):
                                    roster_edit_msg_id = None
                                if roster_edit_msg_id:
                                    try:
                                        edit_msg = await channel.fetch_message(roster_edit_msg_id)
                                        await edit_msg.edit(content=edit_msg.content + f"\n{edit_line}")
                                    except Exception:
                                        roster_edit_msg_id = None
                                if not roster_edit_msg_id:
                                    try:
                                        new_edit_msg = await channel.send(f"> 📋 **Roster Edits**\n{edit_line}")
                                        await _db.set_roster_edit_msg_id(match["id"], new_edit_msg.id)
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
                                from schedule import thread_date_str
                                thread_name = f"{match['team_name']} vs Mix 6s — {match['division']}, {thread_date_str(match['timestamp'])}"
                            else:
                                content_msg = build_mix_message(match, signups, pug_role_id=pug_role_id)
                                view        = SignupView(match["id"])
                                from schedule import thread_date_str
                                thread_name = f"{match['team_name']} vs Mix — {match['division']}, {thread_date_str(match['timestamp'])}"

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
