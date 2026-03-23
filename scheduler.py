import time
import logging
import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import db
from embeds import build_match_embed, build_mix_message

log = logging.getLogger("scheduler")


def start_scheduler(bot):
    from views import SignupView, ManageView

    scheduler = AsyncIOScheduler()

    # ── Clean expired cancel notices (24h) ────────────────────────────────────
    async def clean_cancel_notices():
        notices = await db.get_expired_cancel_notices()
        for match in notices:
            try:
                channel = bot.get_channel(match["channel_id"])
                if channel and match["cancel_msg_id"]:
                    msg = await channel.fetch_message(match["cancel_msg_id"])
                    await msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            await db.clear_cancel_msg(match["id"])

    async def clean_conclude_notices():
        notices = await db.get_expired_conclude_notices()
        for match in notices:
            try:
                channel = bot.get_channel(match["channel_id"])
                if channel and match["conclude_msg_id"]:
                    msg = await channel.fetch_message(match["conclude_msg_id"])
                    await msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            await db.clear_conclude_msg(match["id"])

    # ── 1-hour reminder: ping roster in the match channel ────────────────────
    async def send_1h_reminders():
        matches = await db.get_matches_needing_1h_reminder()
        for match in matches:
            accepted = await db.get_accepted_signups(match["id"])
            # Only first accepted per class = actual roster
            seen = set()
            pings = []
            for s in accepted:
                if s["class_name"] not in seen:
                    seen.add(s["class_name"])
                    pings.append(f"<@{s['user_id']}>")

            if not pings:
                await db.mark_reminded(match["id"], "1h")
                continue

            channel = bot.get_channel(match["channel_id"])
            if channel:
                try:
                    team = match["team_name"] or "Mix"
                    await channel.send(
                        f"⏰ **1 hour reminder!** {' '.join(pings)}\n"
                        f"**{team} vs Mix Team** starts <t:{match['timestamp']}:R>. Get ready!"
                    )
                except Exception as e:
                    log.warning(f"Could not send 1h reminder for match #{match['id']}: {e}")

            await db.mark_reminded(match["id"], "1h")

    # ── 8-hour host reminder: DM host to conclude match ───────────────────────
    async def send_8h_reminders():
        matches = await db.get_matches_needing_8h_reminder()
        hoster_channel_id = bot.config.get("hoster_channel_id")
        for match in matches:
            if hoster_channel_id:
                hoster_ch = bot.get_channel(int(hoster_channel_id))
                if hoster_ch:
                    team = match["team_name"] or "Mix"
                    view = ManageView(match["id"])
                    await hoster_ch.send(
                        f"<@{match['created_by']}> ⏰ It's been 8 hours since "
                        f"<#{match['channel_id']}> ({team} vs Mix) started. "
                        f"If the match is over, please conclude it.",
                        view=view,
                    )
            await db.mark_reminded(match["id"], "8h")

    # ── Re-sort #ongoing-matches ──────────────────────────────────────────────
    async def re_sort():
        if not bot.config.get("re_sort_enabled", False):
            return
        channel = bot.get_channel(bot.ongoing_channel)
        if not channel:
            return
        matches = await db.get_all_active_matches()
        for match in matches:
            signups = await db.get_signups_for_match(match["id"])
            view    = SignupView(match["id"])
            if match["type"] == "mix":
                pug_role_id = bot.config.get("pug_role_id")
                content = build_mix_message(match, signups, pug_role_id=pug_role_id)
            else:
                content = None
            if match["message_id"]:
                try:
                    old = await channel.fetch_message(match["message_id"])
                    await old.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass
            if content:
                new_msg = await channel.send(content=content, view=view)
            else:
                embed   = build_match_embed(match, signups)
                new_msg = await channel.send(embed=embed, view=view)
            await db.set_message_id(match["id"], new_msg.id, channel.id)

    scheduler.add_job(clean_cancel_notices,  "interval", minutes=5,  id="clean_cancel_notices")
    scheduler.add_job(clean_conclude_notices, "interval", minutes=5,  id="clean_conclude_notices")
    scheduler.add_job(send_1h_reminders,    "interval", minutes=2,  id="remind_1h")
    scheduler.add_job(send_8h_reminders,    "interval", minutes=10, id="remind_8h")

    if bot.config.get("re_sort_enabled", False):
        scheduler.add_job(
            re_sort, "interval",
            minutes=bot.config.get("re_sort_interval_minutes", 30),
            id="re_sort",
        )

    scheduler.start()
    log.info("Scheduler started.")
