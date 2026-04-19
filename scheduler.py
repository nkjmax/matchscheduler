import time
import logging
import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import db
from embeds import build_ongoing_line

log = logging.getLogger("scheduler")


def start_scheduler(bot):
    from views import ManageView

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
            except Exception as e:
                log.warning(f"clean_conclude_notices failed for match #{match['id']}: {e}")
            await db.clear_conclude_msg(match["id"])

    # ── 1-hour reminder: ping roster in the match channel ────────────────────
    async def send_1h_reminders():
        matches = await db.get_matches_needing_1h_reminder()
        for match in matches:
            accepted = await db.get_accepted_signups(match["id"])
            match_type = match["type"]

            from embeds import TF2_CLASSES, SIXS_CLASSES
            if match_type in ("6s_mix", "6s_opug"):
                class_order = SIXS_CLASSES
            else:
                class_order = TF2_CLASSES

            pings = []
            seen_users = set()

            if match_type in ("mix", "6s_mix"):
                # Main roster only — first accepted per class, in class order
                first_per_class = {}
                for s in accepted:
                    if s["class_name"] not in first_per_class:
                        first_per_class[s["class_name"]] = s
                for cls in class_order:
                    s = first_per_class.get(cls)
                    if s and s["user_id"] not in seen_users:
                        seen_users.add(s["user_id"])
                        pings.append(f"<@{s['user_id']}>")
            elif match_type in ("opug", "6s_opug"):
                # All accepted players (both slots per class), in class order
                by_class = {cls: [] for cls in class_order}
                for s in accepted:
                    if s["class_name"] in by_class:
                        by_class[s["class_name"]].append(s)
                for cls in class_order:
                    for s in by_class[cls]:
                        if s["user_id"] not in seen_users:
                            seen_users.add(s["user_id"])
                            pings.append(f"<@{s['user_id']}>")
            elif match_type in ("fresh_pug", "6s_fresh_pug"):
                # All signed-up players in signup order
                for s in accepted:
                    if s["user_id"] not in seen_users:
                        seen_users.add(s["user_id"])
                        pings.append(f"<@{s['user_id']}>")

            if not pings:
                await db.mark_reminded(match["id"], "1h")
                continue

            channel = bot.get_channel(match["channel_id"])
            if channel:
                try:
                    if match_type in ("opug", "6s_opug"):
                        match_label = f"{match['division'] or 'PUG'} PUG"
                    elif match_type == "6s_mix":
                        match_label = f"{match['team_name'] or 'Mix'} vs Mix 6s"
                    elif match_type in ("fresh_pug", "6s_fresh_pug"):
                        match_label = "Fresh PUG" if match_type == "fresh_pug" else "Fresh PUG 6v6"
                    else:
                        match_label = f"{match['team_name'] or 'Mix'} vs Mix"
                    await channel.send(
                        f"⏰ **1 hour reminder!** {' '.join(pings)}\n"
                        f"**{match_label}** starts <t:{match['timestamp']}:R>. Get ready!"
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
                    match_type = match["type"]
                    if match_type in ("opug", "6s_opug"):
                        match_label = f"{match['division'] or 'PUG'} PUG"
                    elif match_type == "6s_mix":
                        match_label = f"{match['team_name'] or 'Mix'} vs Mix 6s"
                    else:
                        match_label = f"{match['team_name'] or 'Mix'} vs Mix"
                    from views import SlimManageView
                    view = SlimManageView(match["id"])
                    await hoster_ch.send(
                        f"<@{match['created_by']}> ⏰ It's been 8 hours since "
                        f"<#{match['channel_id']}> ({match_label}) started. "
                        f"If the match is over, please conclude it.",
                        view=view,
                    )
            await db.mark_reminded(match["id"], "8h")

    # ── Re-sort #ongoing-matches ──────────────────────────────────────────────
    async def re_sort():
        if not bot.config.get("re_sort_enabled", False):
            return
        ongoing_channel = bot.get_channel(bot.ongoing_channel)
        if not ongoing_channel:
            return
        matches = await db.get_all_active_matches()
        for match in matches:
            # Delete the existing ongoing-matches line for this match
            if match["ongoing_msg_id"]:
                try:
                    old = await ongoing_channel.fetch_message(match["ongoing_msg_id"])
                    await old.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass
            # Repost as a fresh ongoing line (plain text, no view)
            signups = await db.get_signups_for_match(match["id"])
            line    = build_ongoing_line(
                match,
                channel_id=match["channel_id"],
                signups=signups if match["type"] in ("mix", "opug", "6s_mix", "6s_opug") else None,
            )
            new_msg = await ongoing_channel.send(line)
            await db.set_ongoing_msg_id(match["id"], new_msg.id)

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
