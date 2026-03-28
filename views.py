import time
import logging
import discord
from discord import ui

log = logging.getLogger("views")

from embeds import (TF2_CLASSES, CLASS_EMOJI, build_mix_message, build_match_embed, build_archive_message,
    build_opug_teams_message, build_split_view_text, build_pending_message, build_denied_message,
    SIXS_CLASSES, SIXS_CLASS_EMOJI, build_6s_opug_teams_message, build_6s_split_view_text)
import db

LOW_PRIORITY_ROLE_ID = None  # set to your role ID integer if desired


async def is_lp(client, user_id):
    """Check if a user has the LP role."""
    lp_role_id = getattr(client, "config", {}).get("lp_role_id")
    if not lp_role_id:
        return False
    guild_id = getattr(client, "config", {}).get("guild_id")
    if not guild_id:
        return False
    try:
        guild  = client.get_guild(int(guild_id))
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        return any(str(r.id) == str(lp_role_id) for r in member.roles)
    except Exception:
        return False


async def reorder_class_roster(client, match_id, class_name):
    """
    After any accept, ensure non-LP players have priority over LP players.
    If main roster slot is LP but a non-LP sub exists, swap them.
    """
    accepted = await db.get_accepted_signups_for_class(match_id, class_name)
    if len(accepted) < 2:
        return

    lp_status = {}
    for s in accepted:
        lp_status[s["id"]] = await is_lp(client, s["user_id"])

    main_roster = accepted[0]
    subs        = accepted[1:]

    if lp_status[main_roster["id"]]:
        non_lp_sub = next((s for s in subs if not lp_status[s["id"]]), None)
        if non_lp_sub:
            await db.swap_signup_order(dict(main_roster), dict(non_lp_sub))


# ── Helpers ───────────────────────────────────────────────────────────────────

async def refresh_message(client, match_id):
    match   = await db.get_match(match_id)
    signups = await db.get_signups_for_match(match_id)
    if not match or not match["message_id"]:
        return
    try:
        channel = client.get_channel(match["channel_id"])
        if not channel:
            return
        msg = await channel.fetch_message(match["message_id"])
        pug_role_id = getattr(client, "config", {}).get("pug_role_id")
        if match["type"] == "mix":
            await msg.edit(content=build_mix_message(match, signups, pug_role_id=pug_role_id), embed=None)
        elif match["type"] == "opug":
            from embeds import build_opug_message
            await msg.edit(content=build_opug_message(match, signups, pug_role_id=pug_role_id), embed=None)
        elif match["type"] == "6s_mix":
            from embeds import build_6s_mix_message
            await msg.edit(content=build_6s_mix_message(match, signups, pug_role_id=pug_role_id), embed=None)
        elif match["type"] == "6s_opug":
            from embeds import build_6s_opug_message
            await msg.edit(content=build_6s_opug_message(match, signups, pug_role_id=pug_role_id), embed=None)
        else:
            await msg.edit(embed=build_match_embed(match, signups))
    except Exception as e:
        log.warning(f"refresh_message (main) failed for match #{match_id}: {e}")

    # Refresh pending and denied messages for mix and opug types
    if match["type"] in ("mix", "6s_mix", "opug", "6s_opug"):
        channel = client.get_channel(match["channel_id"])
        if channel:
            try:
                pending_msg_id = match["pending_msg_id"]
            except (IndexError, KeyError):
                pending_msg_id = None
            try:
                denied_msg_id = match["denied_msg_id"]
            except (IndexError, KeyError):
                denied_msg_id = None

            if pending_msg_id:
                try:
                    pmsg = await channel.fetch_message(pending_msg_id)
                    await pmsg.edit(content=build_pending_message(match, signups))
                except Exception as e:
                    log.warning(f"refresh_message (pending) failed for match #{match_id}: {e}")
            if denied_msg_id:
                try:
                    dmsg = await channel.fetch_message(denied_msg_id)
                    await dmsg.edit(content=build_denied_message(match, signups))
                except Exception as e:
                    log.warning(f"refresh_message (denied) failed for match #{match_id}: {e}")

    # Also refresh the ongoing-matches line
    try:
        from schedule import refresh_ongoing_line
        await refresh_ongoing_line(client, match_id)
    except Exception:
        pass


async def archive_thread_to_channel(client, match, archive_ch, archive_summary_msg):
    """
    Fetch all messages from the match thread and re-post them
    as a new thread on the archive summary message.
    Skips bot messages that are just the connect string ping.
    """
    if not match["thread_id"]:
        return

    thread = client.get_channel(match["thread_id"])
    if not thread:
        try:
            thread = await client.fetch_channel(match["thread_id"])
        except Exception:
            return

    # Collect all messages chronologically
    messages = []
    try:
        async for msg in thread.history(limit=500, oldest_first=True):
            messages.append(msg)
    except Exception:
        return

    if not messages:
        return

    # Create an archive thread on the summary message
    team = match["team_name"] or "Mix"
    try:
        archive_thread = await archive_summary_msg.create_thread(
            name=f"{team} vs Mix — thread log"
        )
    except Exception:
        return

    # Re-post each message
    for msg in messages:
        if not msg.content and not msg.embeds and not msg.attachments:
            continue
        author = msg.author.display_name
        ts     = discord.utils.format_dt(msg.created_at, style="t")
        content_lines = [f"**{author}** {ts}"]
        if msg.content:
            content_lines.append(msg.content)
        text = "\n".join(content_lines)

        # Split if over 2000 chars
        while len(text) > 2000:
            await archive_thread.send(text[:2000])
            text = text[2000:]
        if text.strip():
            try:
                await archive_thread.send(text)
            except Exception:
                pass

        # Re-attach embeds as descriptions
        for embed in msg.embeds:
            try:
                await archive_thread.send(embed=embed)
            except Exception:
                pass


async def do_archive(client, match_id, concluded: bool, opug_split=None):
    """
    Shared archive logic for both conclude and cancel.
    Posts summary to archive channel, creates archive thread with message log,
    then locks and archives the original thread.
    opug_split: {"red": [...], "blu": [...], "subs": [...]} for concluded opugs
    """
    import logging
    log = logging.getLogger("discord")

    match   = await db.get_match(match_id)
    signups = await db.get_signups_for_match(match_id) if opug_split is None else opug_split

    archive_channel_id = client.config.get("archive_channel_id")
    if not archive_channel_id:
        log.warning("do_archive: no archive_channel_id in config")
        return

    archive_ch = client.get_channel(int(archive_channel_id))
    if not archive_ch:
        log.warning(f"do_archive: could not find archive channel {archive_channel_id}")
        return

    status_line = "🏁 Concluded" if concluded else "❌ Cancelled"
    summary     = build_archive_message(match, signups)
    full_text   = f"{status_line}\n{summary}"

    # Post the summary message — we'll create a thread on it for the log
    archive_msg = await archive_ch.send(full_text)

    # Archive thread messages into a sub-thread on the archive message
    await archive_thread_to_channel(client, match, archive_ch, archive_msg)

    # Lock and archive the original thread
    if match["thread_id"]:
        try:
            thread = client.get_channel(match["thread_id"])
            if not thread:
                thread = await client.fetch_channel(match["thread_id"])
            if thread:
                await thread.edit(locked=True)
                await thread.edit(archived=True)
        except Exception as e:
            import logging
            logging.getLogger("discord").warning(f"Failed to lock/archive thread {match['thread_id']}: {e}")


async def do_cancel(client, match_id):
    match = await db.get_match(match_id)
    if not match or match["ended"]:
        return False

    accepted = await db.get_accepted_signups(match_id)
    pings    = " ".join(dict.fromkeys(f"<@{s['user_id']}>" for s in accepted))
    channel  = client.get_channel(match["channel_id"])

    # Delete ALL bot messages in the channel (connect strings, embeds, everything)
    if channel:
        try:
            async for msg in channel.history(limit=200, oldest_first=True):
                if msg.author.id == client.user.id:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
        except Exception:
            pass

    # Delete ongoing summary
    ongoing_channel_id = getattr(client, "ongoing_channel", None)
    if ongoing_channel_id and match["ongoing_msg_id"]:
        try:
            oc   = client.get_channel(ongoing_channel_id)
            omsg = await oc.fetch_message(match["ongoing_msg_id"])
            await omsg.delete()
        except Exception:
            pass

    # Mark as ended FIRST so channel is freed up regardless of archive success
    await db.end_match(match_id)

    # Archive (best effort — errors won't block the cancel)
    await do_archive(client, match_id, concluded=False)

    match_type = match["type"]
    if match_type in ("mix", "6s_mix"):
        mode_label = f"**{match['team_name'] or 'Mix'} vs Mix{' 6s' if match_type == '6s_mix' else ''}**"
    elif match_type in ("opug", "6s_opug"):
        mode_label = f"**{match['division'] or 'PUG'} PUG{' (6s)' if match_type == '6s_opug' else ''}**"
    elif match_type in ("fresh_pug", "6s_fresh_pug"):
        mode_label = f"**Fresh PUG{' 6s' if match_type == '6s_fresh_pug' else ''}**"
    else:
        mode_label = "**Match**"

    cancel_embed = discord.Embed(
        title="❌ Match Cancelled",
        description=(
            f"{mode_label}\n"
            f"Hosted by {match['created_by_name']} has been **cancelled**."
        ),
        colour=discord.Colour.red(),
    )
    cancel_embed.set_footer(text="This notice will be removed in 24 hours.")

    if channel:
        notice = await channel.send(
            content=f"🚨 {pings}" if pings else None,
            embed=cancel_embed,
        )
        await db.cancel_match(match_id, notice.id)

    return True


# ── Sign-out confirmation (last-hour warning) ─────────────────────────────────

class SignOutConfirmView(ui.View):
    def __init__(self, match_id, user_id, class_name=None):
        super().__init__(timeout=60)
        self.match_id   = match_id
        self.user_id    = user_id
        self.class_name = class_name

    @ui.button(label="Yes, sign out anyway", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        await do_signout(interaction.client, self.match_id, self.user_id, self.class_name)
        await interaction.edit_original_response(
            content="You have been signed out. Please find a replacement as soon as possible.",
            view=None,
        )

    @ui.button(label="Stay signed up", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(
            content="Sign-out cancelled. You're still signed up.", view=None
        )


class SignOutAllConfirmView(ui.View):
    def __init__(self, match_id, user_id):
        super().__init__(timeout=60)
        self.match_id = match_id
        self.user_id  = user_id

    @ui.button(label="Yes, sign out of everything", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        signups = await db.get_non_denied_signups_for_user(self.match_id, self.user_id)
        for s in signups:
            await do_signout(interaction.client, self.match_id, self.user_id, s["class_name"])
        await interaction.edit_original_response(
            content="You have been signed out of all classes. Please find replacements as soon as possible.",
            view=None,
        )

    @ui.button(label="Stay signed up", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(
            content="Sign-out cancelled. You're still signed up.", view=None
        )


class SignOutClassPickerView(ui.View):
    """Shown when a user is signed up on multiple classes and wants to sign out."""
    def __init__(self, match_id, user_id, signups, within_hour, rostered_classes):
        super().__init__(timeout=60)
        self.match_id        = match_id
        self.user_id         = user_id
        self.within_hour     = within_hour
        self.rostered_classes = rostered_classes  # set of class names where user is on main roster
        all_emojis = {**CLASS_EMOJI, **SIXS_CLASS_EMOJI}
        options = [
            discord.SelectOption(
                label=s["class_name"],
                value=s["class_name"],
                emoji=all_emojis.get(s["class_name"]),
                description=f"Status: {s['status']}"
            )
            for s in signups
        ]
        options.append(discord.SelectOption(
            label="All classes",
            value="_all",
            emoji="🚪",
            description="Sign out of everything"
        ))
        select = ui.Select(placeholder="Select class to sign out of…", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction):
        value = interaction.data["values"][0]

        if value == "_all":
            # Within-hour warning only if rostered on at least one class
            if self.within_hour and self.rostered_classes:
                lp_warning = ""
                if LOW_PRIORITY_ROLE_ID:
                    lp_warning = f"\n⚠️ You will receive the <@&{LOW_PRIORITY_ROLE_ID}> role if you fail to find replacements."
                view = SignOutAllConfirmView(self.match_id, self.user_id)
                await interaction.response.edit_message(
                    content=f"⚠️ **Warning:** The match starts in less than 1 hour.{lp_warning}\nSign out of **all classes**?",
                    view=view,
                )
            else:
                await interaction.response.defer(ephemeral=True)
                signups = await db.get_non_denied_signups_for_user(self.match_id, self.user_id)
                for s in signups:
                    await do_signout(interaction.client, self.match_id, self.user_id, s["class_name"])
                await interaction.followup.send("You have been signed out of all classes.", ephemeral=True)
            return

        class_name = value
        # Within-hour warning only if rostered on this class
        if self.within_hour and class_name in self.rostered_classes:
            lp_warning = ""
            if LOW_PRIORITY_ROLE_ID:
                lp_warning = f"\n⚠️ You will receive the <@&{LOW_PRIORITY_ROLE_ID}> role if you fail to find a replacement."
            view = SignOutConfirmView(self.match_id, self.user_id, class_name)
            await interaction.response.edit_message(
                content=f"⚠️ **Warning:** The match starts in less than 1 hour.{lp_warning}\nSign out of **{class_name}**?",
                view=view,
            )
        else:
            await interaction.response.defer(ephemeral=True)
            await do_signout(interaction.client, self.match_id, self.user_id, class_name)
            await interaction.followup.send(f"You have been signed out of **{class_name}**.", ephemeral=True)


async def do_signout(client, match_id, user_id, class_name=None):
    if class_name:
        signup = await db.get_signup_by_user_and_class(match_id, user_id, class_name)
    else:
        signup = await db.get_signup_by_user(match_id, user_id)
    if not signup:
        return None

    class_name   = signup["class_name"]
    was_accepted = signup["status"] == "accepted"
    # Check if this player is the FIRST accepted (rostered, not sub)
    accepted_for_class = await db.get_accepted_signups_for_class(match_id, class_name)
    is_rostered = was_accepted and len(accepted_for_class) > 0 and accepted_for_class[0]["user_id"] == user_id

    await db.remove_signup(match_id, user_id, class_name)

    match        = await db.get_match(match_id)
    channel      = client.get_channel(match["channel_id"]) if match else None
    channel_name = channel.name if channel else "the match channel"
    match_type   = match["type"] if match else "mix"

    if match_type in ("opug", "6s_opug"):
        match_label = f"{match['division'] or 'PUG'} PUG"
    elif match_type == "6s_mix":
        match_label = f"{match['team_name'] or 'Mix'} vs Mix 6s"
    else:
        match_label = f"{match['team_name'] or 'Mix'} vs Mix"

    if is_rostered:
        next_sub = await db.get_next_accepted_for_class(match_id, class_name, user_id)

        # Sub promotion — remove their other sub slots, ping in thread
        if next_sub:
            await db.remove_sub_slots_for_user(match_id, next_sub["user_id"], class_name)
            if match["thread_id"]:
                try:
                    thread = client.get_channel(match["thread_id"])
                    if thread:
                        if match_type in ("opug", "6s_opug"):
                            slot_context = f"**{class_name}** slot — the previous player signed out."
                        else:
                            slot_context = f"**{class_name}** slot on the Mix Team — the previous player signed out."
                        await thread.send(
                            f"<@{next_sub['user_id']}> you have been moved to the {slot_context}"
                        )
                except Exception:
                    pass

        # Ping hoster in hoster channel
        hoster_channel_id = getattr(client, "config", {}).get("hoster_channel_id")
        if hoster_channel_id:
            hoster_ch = client.get_channel(int(hoster_channel_id))
            if hoster_ch:
                promoted_line = ""
                if next_sub:
                    promoted_line = f" **{next_sub['username']}** has been moved to the main roster."
                await hoster_ch.send(
                    f"<@{match['created_by']}> ⚠️ **{signup['username']}** has signed out of "
                    f"**{class_name}** in <#{match['channel_id']}> ({match_label}).{promoted_line}"
                )

    await refresh_message(client, match_id)
    return signup


# ── Sign-out button ───────────────────────────────────────────────────────────

class SignOutButton(ui.Button):
    def __init__(self, match_id):
        super().__init__(
            label="Sign Out",
            emoji="🚪",
            custom_id=f"signout:{match_id}",
            style=discord.ButtonStyle.danger,
            row=4,
        )
        self.match_id = match_id

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)
        match = await db.get_match(self.match_id)

        if not match or match["ended"]:
            await interaction.followup.send(
                "This match has already ended or been cancelled.", ephemeral=True
            )
            return

        signups = await db.get_non_denied_signups_for_user(self.match_id, interaction.user.id)
        if not signups:
            await interaction.followup.send(
                "You're not signed up for this match.", ephemeral=True
            )
            return

        time_until = match["timestamp"] - time.time()
        within_hour = 0 < time_until <= 3600

        # Determine which classes this player is on the main (first) roster slot
        rostered_classes = set()
        if within_hour:
            for s in signups:
                if s["status"] == "accepted":
                    accepted_for_class = await db.get_accepted_signups_for_class(self.match_id, s["class_name"])
                    if accepted_for_class and accepted_for_class[0]["user_id"] == interaction.user.id:
                        rostered_classes.add(s["class_name"])

        if len(signups) == 1:
            class_name = signups[0]["class_name"]
            if within_hour and class_name in rostered_classes:
                lp_warning = ""
                if LOW_PRIORITY_ROLE_ID:
                    lp_warning = f"\n⚠️ You will receive the <@&{LOW_PRIORITY_ROLE_ID}> role if you fail to find a replacement."
                view = SignOutConfirmView(self.match_id, interaction.user.id, class_name)
                await interaction.followup.send(
                    f"⚠️ **Warning:** The match starts in less than 1 hour.{lp_warning}\nAre you sure?",
                    view=view, ephemeral=True,
                )
            else:
                await do_signout(interaction.client, self.match_id, interaction.user.id, class_name)
                await interaction.followup.send("You have been signed out.", ephemeral=True)
        else:
            # Multiple classes — show a picker with All option
            view = SignOutClassPickerView(self.match_id, interaction.user.id, signups, within_hour, rostered_classes)
            classes = ", ".join(f"**{s['class_name']}**" for s in signups)
            await interaction.followup.send(
                f"You're signed up on {classes}. Which class do you want to sign out of?",
                view=view, ephemeral=True,
            )


# ── Per-player decision view ──────────────────────────────────────────────────

class PlayerDecisionView(ui.View):
    def __init__(self, match_id, signup_id, username, class_name, channel_name):
        super().__init__(timeout=300)
        self.match_id     = match_id
        self.signup_id    = signup_id
        self.username     = username
        self.class_name   = class_name
        self.channel_name = channel_name

    @ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction, button):
        current = await db.get_signup_by_id(self.signup_id)
        already = current and current["status"] == "accepted"
        filled  = await db.count_accepted_for_class(self.match_id, self.class_name)

        await db.update_signup_status(self.signup_id, "accepted")
        label = "added to subs list" if (filled >= 1 and not already) else f"accepted as **{self.class_name}**"

        await interaction.response.edit_message(
            content=f"✅ **{self.username}** {label} in **#{self.channel_name}**.",
            view=None,
        )
        await refresh_message(interaction.client, self.match_id)

    @ui.button(label="❌ Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction, button):
        await db.update_signup_status(self.signup_id, "denied")
        await interaction.response.edit_message(
            content=f"❌ **{self.username}** denied for **{self.class_name}** in **#{self.channel_name}**.",
            view=None,
        )
        await refresh_message(interaction.client, self.match_id)


# ── Sign-up buttons ───────────────────────────────────────────────────────────

class ClassButton(ui.Button):
    def __init__(self, class_name, match_id):
        super().__init__(
            label=class_name,
            emoji=CLASS_EMOJI[class_name],
            custom_id=f"signup:{match_id}:{class_name}",
            style=discord.ButtonStyle.secondary,
            row=TF2_CLASSES.index(class_name) // 5,
        )
        self.class_name = class_name
        self.match_id   = match_id

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)
        match = await db.get_match(self.match_id)

        if not match or match["ended"]:
            await interaction.followup.send(
                "This match has already ended or been cancelled.", ephemeral=True
            )
            return

        # Block if already on the main roster for any class in this match
        all_signups = await db.get_non_denied_signups_for_user(self.match_id, interaction.user.id)
        for s in all_signups:
            if s["status"] == "accepted":
                accepted_for = await db.get_accepted_signups_for_class(self.match_id, s["class_name"])
                if accepted_for and accepted_for[0]["user_id"] == interaction.user.id:
                    await interaction.followup.send(
                        f"You're already playing this match as **{s['class_name']}**.",
                        ephemeral=True,
                    )
                    return

        # Block if already signed up (non-denied) for THIS specific class
        existing_class = await db.get_signup_by_user_and_class(self.match_id, interaction.user.id, self.class_name)
        if existing_class:
            if existing_class["status"] == "denied":
                await interaction.followup.send(
                    f"You've been denied for **{self.class_name}**. Please contact the hoster.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                f"You're already signed up for **{self.class_name}**. "
                "Sign out of this class first if you want to change it.",
                ephemeral=True,
            )
            return

        # Check for clashing accepted mixes (within 2h window either side)
        clashing = await db.get_accepted_matches_for_user(
            interaction.user.id,
            exclude_match_id=self.match_id,
            reference_timestamp=match["timestamp"]
        )
        if clashing:
            clash_names = ", ".join(
                f"{m['team_name'] or 'a mix'} (<#{m['channel_id']}>)" for m in clashing
            )
            view = ClashConfirmView(self.match_id, self.class_name, clash_names)
            warn = "⚠️ **Warning:** You are already accepted in " + clash_names + ". Are you sure you want to sign up for this mix too?"
            await interaction.followup.send(warn, view=view, ephemeral=True)
            return

        await _do_signup(interaction, self.match_id, self.class_name)


# ── Clash confirmation ────────────────────────────────────────────────────────

class ClashConfirmView(ui.View):
    def __init__(self, match_id, class_name, clash_names):
        super().__init__(timeout=60)
        self.match_id   = match_id
        self.class_name = class_name
        self.clash_names = clash_names

    @ui.button(label="Yes, sign up anyway", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        await _do_signup(interaction, self.match_id, self.class_name)

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(
            content="Sign-up cancelled.", view=None
        )


async def _do_signup(interaction, match_id, class_name):
    """Shared signup logic used by ClassButton and ClashConfirmView."""
    match = await db.get_match(match_id)

    signup_id = await db.add_signup(
        match_id, interaction.user.id,
        interaction.user.display_name, class_name,
    )
    if signup_id is None:
        await interaction.followup.send("Could not add sign-up. Try again.", ephemeral=True)
        return

    await interaction.followup.send(
        f"✅ Signed up as **{class_name}**! The host will review shortly.",
        ephemeral=True,
    )

    match_row = await db.get_match(match_id)

    # Inform hoster channel if this player is already accepted in a clashing mix
    clashing = await db.get_accepted_matches_for_user(
        interaction.user.id,
        exclude_match_id=match_id,
        reference_timestamp=match["timestamp"]
    )
    if clashing:
        hoster_channel_id = getattr(interaction.client, "config", {}).get("hoster_channel_id")
        if hoster_channel_id:
            hoster_ch = interaction.client.get_channel(int(hoster_channel_id))
            if hoster_ch:
                match_type = match_row["type"]
                if match_type in ("opug", "6s_opug"):
                    this_match_label = f"{match_row['division'] or 'PUG'} PUG"
                elif match_type == "6s_mix":
                    this_match_label = f"{match_row['team_name'] or 'Mix'} vs Mix 6s"
                else:
                    this_match_label = f"{match_row['team_name'] or 'Mix'} vs Mix"

                hoster_pings = {match_row["created_by"]}
                for m in clashing:
                    hoster_pings.add(m["created_by"])
                pings_str = " ".join(f"<@{uid}>" for uid in hoster_pings)

                def clash_label(m):
                    t = m["type"] if m["type"] else "mix"
                    if t in ("opug", "6s_opug"):
                        return f"<#{m['channel_id']}> ({m['division'] or 'PUG'} PUG)"
                    elif t == "6s_mix":
                        return f"<#{m['channel_id']}> ({m['team_name'] or 'Mix'} vs Mix 6s)"
                    else:
                        return f"<#{m['channel_id']}> ({m['team_name'] or 'Mix'} vs Mix)"

                clash_refs = ", ".join(clash_label(m) for m in clashing)
                await hoster_ch.send(
                    f"{pings_str} ⚠️ **{interaction.user.display_name}** signed up for **{class_name}** "
                    f"in <#{match_row['channel_id']}> ({this_match_label}) "
                    f"but is already accepted in {clash_refs}."
                )

    await refresh_message(interaction.client, match_id)


# ── Organised PUG sign-up view ────────────────────────────────────────────────

class OPugClassButton(ui.Button):
    def __init__(self, class_name, match_id):
        super().__init__(
            label=class_name,
            emoji=CLASS_EMOJI[class_name],
            custom_id=f"opug_signup:{match_id}:{class_name}",
            style=discord.ButtonStyle.secondary,
            row=TF2_CLASSES.index(class_name) // 5,
        )
        self.class_name = class_name
        self.match_id   = match_id

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)
        match = await db.get_match(self.match_id)

        if not match or match["ended"]:
            await interaction.followup.send(
                "This PUG has already ended or been cancelled.", ephemeral=True
            )
            return

        existing_class = await db.get_signup_by_user_and_class(self.match_id, interaction.user.id, self.class_name)
        if existing_class:
            if existing_class["status"] == "denied":
                await interaction.followup.send(
                    f"You've been denied for **{self.class_name}**. Please contact the hoster.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                f"You're already signed up for **{self.class_name}**.", ephemeral=True
            )
            return

        # Block if already on main roster for any class in this match
        all_signups = await db.get_non_denied_signups_for_user(self.match_id, interaction.user.id)
        for s in all_signups:
            if s["status"] == "accepted":
                accepted_for = await db.get_accepted_signups_for_class(self.match_id, s["class_name"])
                if accepted_for and accepted_for[0]["user_id"] == interaction.user.id:
                    await interaction.followup.send(
                        f"You're already playing this match as **{s['class_name']}**.",
                        ephemeral=True,
                    )
                    return

        # Clash check
        clashing = await db.get_accepted_matches_for_user(
            interaction.user.id,
            exclude_match_id=self.match_id,
            reference_timestamp=match["timestamp"]
        )
        if clashing:
            clash_names = ", ".join(
                f"{m['team_name'] or 'a mix'} (<#{m['channel_id']}>)" for m in clashing
            )
            view = ClashConfirmView(self.match_id, self.class_name, clash_names)
            warn = "⚠️ **Warning:** You are already accepted in " + clash_names + ". Are you sure you want to sign up for this PUG too?"
            await interaction.followup.send(warn, view=view, ephemeral=True)
            return

        await _do_signup(interaction, self.match_id, self.class_name)


class OPugSignupView(ui.View):
    def __init__(self, match_id):
        super().__init__(timeout=None)
        for cls in TF2_CLASSES:
            self.add_item(OPugClassButton(cls, match_id))
        self.add_item(SignOutButton(match_id))


class SignupView(ui.View):
    def __init__(self, match_id):
        super().__init__(timeout=None)
        for cls in TF2_CLASSES:
            self.add_item(ClassButton(cls, match_id))
        self.add_item(SignOutButton(match_id))


# ── Withdraw ──────────────────────────────────────────────────────────────────

class WithdrawView(ui.View):
    def __init__(self, match_id, user_id):
        super().__init__(timeout=60)
        self.match_id = match_id
        self.user_id  = user_id

    @ui.button(label="Withdraw sign-up", style=discord.ButtonStyle.danger)
    async def withdraw(self, interaction, button):
        await do_signout(interaction.client, self.match_id, self.user_id)
        await interaction.response.edit_message(content="Your sign-up has been withdrawn.", view=None)

    @ui.button(label="Keep sign-up", style=discord.ButtonStyle.secondary)
    async def keep(self, interaction, button):
        await interaction.response.edit_message(content="No changes made.", view=None)


# ── Conclude confirmation ─────────────────────────────────────────────────────

class ConcludeConfirmView(ui.View):
    def __init__(self, match_id):
        super().__init__(timeout=60)
        self.match_id = match_id

    @ui.button(label="Yes, conclude match", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        match = await db.get_match(self.match_id)
        if not match:
            await interaction.followup.send("Match not found.", ephemeral=True)
            return

        channel = interaction.client.get_channel(match["channel_id"])

        # Delete ALL bot messages in the match channel
        if channel:
            try:
                async for msg in channel.history(limit=200, oldest_first=True):
                    if msg.author.id == interaction.client.user.id:
                        try:
                            await msg.delete()
                        except Exception:
                            pass
            except Exception:
                pass

        # Delete ongoing summary
        ongoing_channel_id = getattr(interaction.client, "ongoing_channel", None)
        if ongoing_channel_id and match["ongoing_msg_id"]:
            try:
                oc   = interaction.client.get_channel(ongoing_channel_id)
                omsg = await oc.fetch_message(match["ongoing_msg_id"])
                await omsg.delete()
            except Exception:
                pass

        # Post conclusion notice in match channel pinging roster
        if channel:
            accepted = await db.get_accepted_signups(self.match_id)
            seen, pings = set(), []
            for s in accepted:
                if s["user_id"] not in seen:
                    seen.add(s["user_id"])
                    pings.append(f"<@{s['user_id']}>")
            ping_str = " ".join(pings)
            if match["type"] in ("opug", "6s_opug"):
                division = match["division"] or "PUG"
                notice_text = f"🏁 **{division} PUG** has been concluded. Thanks for playing! 🫡"
            else:
                team = match["team_name"] or "Mix"
                notice_text = f"{ping_str}\n🏁 **{team} vs Mix Team** has been concluded. Thanks for playing! 🫡"
            conclude_msg = await channel.send(notice_text)
            await db.set_conclude_msg(self.match_id, conclude_msg.id, match["channel_id"])

        # Mark as ended FIRST so channel is freed regardless of archive success
        await db.end_match(self.match_id)

        # For opug types, pass team split into archive so it shows RED/BLU layout
        opug_split = None
        if match["type"] in ("opug", "6s_opug"):
            split = await db.get_team_split(self.match_id)
            if split:
                all_signups = await db.get_signups_for_match(self.match_id)
                accepted_all = [s for s in all_signups if s["status"] == "accepted"]
                red_uids = set(split["red"])
                blu_uids = set(split["blu"])
                opug_split = {
                    "red":  [s for s in accepted_all if s["user_id"] in red_uids],
                    "blu":  [s for s in accepted_all if s["user_id"] in blu_uids],
                    "subs": [s for s in accepted_all if s["user_id"] not in red_uids and s["user_id"] not in blu_uids],
                }

        # Archive (best effort)
        await do_archive(interaction.client, self.match_id, concluded=True, opug_split=opug_split)
        await interaction.followup.send("✅ Match concluded and archived.", ephemeral=True)

    @ui.button(label="Never mind", style=discord.ButtonStyle.secondary)
    async def abort(self, interaction, button):
        await interaction.response.edit_message(content="Conclusion cancelled.", view=None)


# ── Cancel confirmation ───────────────────────────────────────────────────────

class CancelConfirmView(ui.View):
    def __init__(self, match_id):
        super().__init__(timeout=60)
        self.match_id = match_id

    @ui.button(label="Yes, cancel the match", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        success = await do_cancel(interaction.client, self.match_id)
        if success:
            await interaction.followup.send(
                "✅ Match cancelled. Notice posted for 24 hours.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "❌ Could not cancel — match may already be ended.", ephemeral=True
            )

    @ui.button(label="Never mind", style=discord.ButtonStyle.secondary)
    async def abort(self, interaction, button):
        await interaction.response.edit_message(
            content="Cancellation aborted. Match is still active.", view=None
        )


# ── Manage panel ──────────────────────────────────────────────────────────────

# ── Manage overview helpers ──────────────────────────────────────────────────

async def build_manage_text(match_id):
    signups = await db.get_signups_for_match(match_id)
    match   = await db.get_match(match_id)
    team    = match["team_name"] or "Mix"

    # Track earliest signup id per player for chronological sort
    # signups are already ORDER BY id ASC so the first seen is always the minimum
    player_data = {}
    for s in signups:
        uid = s["user_id"]
        if uid not in player_data:
            player_data[uid] = {"username": s["username"], "accepted": [], "pending": [], "denied": [], "min_id": s["id"]}
        player_data[uid][s["status"]].append((s["id"], s["class_name"]))

    class_list = SIXS_CLASSES if match["type"] in ("6s_mix", "6s_opug", "6s_fresh_pug") else TF2_CLASSES

    # Sort each player's classes chronologically (by signup id)
    for uid in player_data:
        for key in ("accepted", "pending", "denied"):
            player_data[uid][key].sort(key=lambda x: x[0])
            player_data[uid][key] = [cls for _, cls in player_data[uid][key]]

    def fmt(classes):
        return ", ".join(classes) if classes else "\u2014"

    if match["type"] in ("opug", "6s_opug"):
        division = match["division"] or "PUG"
        header = "**" + division + " PUG \u2014 signups**\n"
    elif match["type"] in ("fresh_pug", "6s_fresh_pug"):
        header = "**Fresh PUG \u2014 signups**\n"
    elif match["type"] == "6s_mix":
        header = "**" + team + " vs Mix 6s \u2014 signups**\n"
    else:
        header = "**" + team + " vs Mix \u2014 signups**\n"
    lines  = [header]

    accepted_players = [p for p in player_data.values() if p["accepted"]]
    # Sort pending players chronologically by their earliest signup
    pending_players  = sorted(
        [p for p in player_data.values() if p["pending"]],
        key=lambda p: p["min_id"]
    )
    denied_players   = [p for p in player_data.values() if p["denied"] and not p["accepted"] and not p["pending"]]

    if accepted_players:
        lines.append("\u2705 **Accepted:**")
        for p in accepted_players:
            lines.append("\u2022 **" + p["username"] + "** \u2014 " + fmt(p["accepted"]))

    if pending_players:
        lines.append("\n\u23f3 **Pending** *(chronological order)*:")
        for p in pending_players:
            lines.append("\u2022 **" + p["username"] + "** \u2014 " + fmt(p["pending"]))

    if denied_players:
        lines.append("\n\u274c **Denied:**")
        for p in denied_players:
            lines.append("\u2022 **" + p["username"] + "** \u2014 " + fmt(p["denied"]))

    if not accepted_players and not pending_players and not denied_players:
        lines.append("No sign-ups yet.")

    total_pending = sum(len(p["pending"]) for p in player_data.values())
    return "\n".join(lines), total_pending


class ClassDropdownSelect(ui.Select):
    """Dropdown to pick a class — only shows classes with pending signups."""
    def __init__(self, match_id, pending_by_class, is_sixs=False):
        self.match_id = match_id
        self.is_sixs  = is_sixs
        class_list    = SIXS_CLASSES if is_sixs else TF2_CLASSES
        options = []
        for cls in class_list:
            count = len(pending_by_class.get(cls, []))
            if count:
                options.append(discord.SelectOption(
                    label=cls,
                    value=cls,
                    description=str(count) + " pending",
                ))
        if not options:
            options = [discord.SelectOption(label="No pending", value="_none", description="No pending sign-ups")]
        super().__init__(
            placeholder="Select a class to review...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction):
        class_name = self.values[0]
        pending    = await db.get_pending_signups(self.match_id)
        class_pend = sorted([s for s in pending if s["class_name"] == class_name], key=lambda s: s["id"])

        if not class_pend:
            await interaction.response.edit_message(
                content="No pending sign-ups for **" + class_name + "** anymore.",
                view=await ReviewView.create(self.match_id),
            )
            return

        view = PlayerPickView(self.match_id, class_name, class_pend)
        text = "**" + class_name + "**  —  click a player to accept *(chronological order)*"
        await interaction.response.edit_message(content=text, view=view)


class LPConfirmView(ui.View):
    def __init__(self, match_id, signup_id, username, class_name, filled):
        super().__init__(timeout=60)
        self.match_id   = match_id
        self.signup_id  = signup_id
        self.username   = username
        self.class_name = class_name
        self.filled     = filled

    @ui.button(label="Yes, accept anyway", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        current = await db.get_signup_by_id(self.signup_id)
        already = current and current["status"] == "accepted"
        await _do_accept(interaction, self.match_id, self.signup_id, self.username, self.class_name, self.filled, already, current)

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Cancelled.", view=None)


async def _do_accept(interaction, match_id, signup_id, username, class_name, filled, already, current):
    # Caller must have already deferred the interaction
    await db.update_signup_status(signup_id, "accepted")

    is_main_roster = filled == 0 and not already
    user_id = current["user_id"] if current else None

    if is_main_roster and user_id:
        # Remove their accepted sub slots on other classes
        await db.remove_sub_slots_for_user(match_id, user_id, class_name)
        # Remove their pending signups on other classes (#2)
        await db.remove_pending_slots_for_user(match_id, user_id, class_name)

    # Reorder roster so non-LP players have priority over LP players
    await reorder_class_roster(interaction.client, match_id, class_name)
    await refresh_message(interaction.client, match_id)

    # Check actual position after reorder to determine if on main roster or sub
    accepted_after = await db.get_accepted_signups_for_class(match_id, class_name)
    on_main = len(accepted_after) > 0 and accepted_after[0]["user_id"] == user_id
    result = "accepted on " + class_name if on_main else "added as sub"
    await interaction.followup.send(
        "✅  " + username + " — " + result + ".", ephemeral=True
    )

    # Ping player in thread (#3)
    match = await db.get_match(match_id)
    if match and user_id:
        try:
            thread_id = match["thread_id"]
        except (IndexError, KeyError):
            thread_id = None
        if thread_id:
            try:
                thread = interaction.client.get_channel(thread_id)
                if thread:
                    role_str = f"**{class_name}**" + (" (sub)" if not on_main else "")
                    await thread.send(f"<@{user_id}> you've been accepted as {role_str}! ✅")
            except Exception:
                pass

    # Refresh class view
    try:
        pending    = await db.get_pending_signups(match_id)
        class_pend = sorted([s for s in pending if s["class_name"] == class_name], key=lambda s: s["id"])
        if class_pend:
            view = PlayerPickView(match_id, class_name, class_pend)
            text = "**" + class_name + "**  —  click a player to accept *(chronological order)*"
            await interaction.message.edit(content=text, view=view)
        else:
            view = await ReviewView.create(match_id)
            text, _ = await build_manage_text(match_id)
            await interaction.message.edit(content=text, view=view)
    except Exception:
        pass


class PlayerPickView(ui.View):
    """Shows pending players as plain buttons — click to accept, no deny needed here."""
    def __init__(self, match_id, class_name, pending_signups):
        super().__init__(timeout=300)
        self.match_id   = match_id
        self.class_name = class_name
        row = 0
        for i, s in enumerate(pending_signups):
            if i > 0 and i % 5 == 0:
                row += 1
            if row > 3:
                break
            self.add_item(AcceptPlayerButton(match_id, s["id"], s["username"], class_name, row))
        self.add_item(BackToReviewButton(match_id, row=4))


class AcceptPlayerButton(ui.Button):
    def __init__(self, match_id, signup_id, username, class_name, row):
        super().__init__(
            label=username,
            style=discord.ButtonStyle.success,
            custom_id="acc:" + str(match_id) + ":" + str(signup_id),
            row=row,
        )
        self.match_id   = match_id
        self.signup_id  = signup_id
        self.username   = username
        self.class_name = class_name

    async def callback(self, interaction):
        current = await db.get_signup_by_id(self.signup_id)
        already = current and current["status"] == "accepted"
        filled  = await db.count_accepted_for_class(self.match_id, self.class_name)

        # LP warning — show confirm before accepting if player has LP role
        user_id    = current["user_id"] if current else None
        player_lp  = await is_lp(interaction.client, user_id) if user_id else False
        if player_lp and not already:
            view = LPConfirmView(self.match_id, self.signup_id, self.username, self.class_name, filled)
            await interaction.response.send_message(
                "⚠️ **" + self.username + "** currently has the Low Priority role. Are you sure you want to accept them?",
                view=view, ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await _do_accept(interaction, self.match_id, self.signup_id, self.username, self.class_name, filled, already, current)


class DenyPlayerButton(ui.Button):
    def __init__(self, match_id, signup_id, username, class_name, row):
        super().__init__(
            label="Deny  " + username,
            style=discord.ButtonStyle.danger,
            custom_id="den:" + str(match_id) + ":" + str(signup_id),
            row=row,
        )
        self.match_id   = match_id
        self.signup_id  = signup_id
        self.username   = username
        self.class_name = class_name

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await db.update_signup_status(self.signup_id, "denied")
        await refresh_message(interaction.client, self.match_id)
        await interaction.followup.send(
            "❌  " + self.username + " denied for " + self.class_name + ".", ephemeral=True
        )

        pending    = await db.get_pending_signups(self.match_id)
        class_pend = sorted([s for s in pending if s["class_name"] == self.class_name], key=lambda s: s["id"])
        if class_pend:
            view = PlayerPickView(self.match_id, self.class_name, class_pend)
            text = "**" + self.class_name + "**  —  click a player to accept *(chronological order)*"
            await interaction.message.edit(content=text, view=view)
        else:
            view = await ReviewView.create(self.match_id)
            text, _ = await build_manage_text(self.match_id)
            await interaction.message.edit(content=text, view=view)


class BackToReviewButton(ui.Button):
    def __init__(self, match_id, row):
        super().__init__(
            label="Back",
            style=discord.ButtonStyle.secondary,
            custom_id="back_review:" + str(match_id),
            row=row,
        )
        self.match_id = match_id

    async def callback(self, interaction):
        view = await ReviewView.create(self.match_id)
        text, _ = await build_manage_text(self.match_id)
        await interaction.response.edit_message(content=text, view=view)


class ReviewView(ui.View):
    """The main review panel — dropdown to pick a class."""
    def __init__(self, match_id):
        super().__init__(timeout=300)
        self.match_id = match_id

    @classmethod
    async def create(cls, match_id):
        self    = cls(match_id)
        match   = await db.get_match(match_id)
        is_sixs = match["type"] in ("6s_opug", "6s_mix") if match else False
        pending = await db.get_pending_signups(match_id)
        pending_by_class = {}
        for s in pending:
            pending_by_class.setdefault(s["class_name"], []).append(s)
        if pending_by_class:
            self.add_item(ClassDropdownSelect(match_id, pending_by_class, is_sixs=is_sixs))
        return self


# ── Deny review panel ─────────────────────────────────────────────────────────

class DenyClassDropdownSelect(ui.Select):
    """Dropdown to pick a class for denying — only shows classes with pending signups."""
    def __init__(self, match_id, pending_by_class, is_sixs=False):
        self.match_id = match_id
        class_list    = SIXS_CLASSES if is_sixs else TF2_CLASSES
        options = []
        for cls in class_list:
            count = len(pending_by_class.get(cls, []))
            if count:
                options.append(discord.SelectOption(
                    label=cls,
                    value=cls,
                    description=str(count) + " pending",
                ))
        if not options:
            options = [discord.SelectOption(label="No pending", value="_none", description="No pending sign-ups")]
        super().__init__(
            placeholder="Select a class to deny from...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction):
        class_name = self.values[0]
        pending    = await db.get_pending_signups(self.match_id)
        class_pend = sorted([s for s in pending if s["class_name"] == class_name], key=lambda s: s["id"])

        if not class_pend:
            await interaction.response.edit_message(
                content="No pending sign-ups for **" + class_name + "** anymore.",
                view=await DenyReviewView.create(self.match_id),
            )
            return

        view = DenyPlayerPickView(self.match_id, class_name, class_pend)
        text = "**" + class_name + "**  —  click a player to deny *(chronological order)*"
        await interaction.response.edit_message(content=text, view=view)


class DenyPlayerPickView(ui.View):
    """Shows pending players as red buttons — click to deny."""
    def __init__(self, match_id, class_name, pending_signups):
        super().__init__(timeout=300)
        self.match_id   = match_id
        self.class_name = class_name
        row = 0
        for i, s in enumerate(pending_signups):
            if i > 0 and i % 5 == 0:
                row += 1
            if row > 3:
                break
            self.add_item(DenyOnlyButton(match_id, s["id"], s["username"], class_name, row))
        self.add_item(BackToDenyReviewButton(match_id, row=4))


class DenyOnlyButton(ui.Button):
    def __init__(self, match_id, signup_id, username, class_name, row):
        super().__init__(
            label=username,
            style=discord.ButtonStyle.danger,
            custom_id="dny:" + str(match_id) + ":" + str(signup_id),
            row=row,
        )
        self.match_id   = match_id
        self.signup_id  = signup_id
        self.username   = username
        self.class_name = class_name

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await db.update_signup_status(self.signup_id, "denied")
        await refresh_message(interaction.client, self.match_id)
        await interaction.followup.send(
            "❌ " + self.username + " denied for **" + self.class_name + "**.", ephemeral=True
        )
        pending    = await db.get_pending_signups(self.match_id)
        class_pend = sorted([s for s in pending if s["class_name"] == self.class_name], key=lambda s: s["id"])
        try:
            if class_pend:
                view = DenyPlayerPickView(self.match_id, self.class_name, class_pend)
                text = "**" + self.class_name + "**  —  click a player to deny *(chronological order)*"
            else:
                view = await DenyReviewView.create(self.match_id)
                text, _ = await build_manage_text(self.match_id)
            await interaction.edit_original_response(content=text, view=view)
        except Exception:
            pass


class BackToDenyReviewButton(ui.Button):
    def __init__(self, match_id, row):
        super().__init__(
            label="Back",
            style=discord.ButtonStyle.secondary,
            custom_id="back_deny_review:" + str(match_id),
            row=row,
        )
        self.match_id = match_id

    async def callback(self, interaction):
        view = await DenyReviewView.create(self.match_id)
        text, _ = await build_manage_text(self.match_id)
        await interaction.response.edit_message(content=text, view=view)


class DenyReviewView(ui.View):
    """The deny panel — dropdown to pick a class."""
    def __init__(self, match_id):
        super().__init__(timeout=300)
        self.match_id = match_id

    @classmethod
    async def create(cls, match_id):
        self    = cls(match_id)
        match   = await db.get_match(match_id)
        is_sixs = match["type"] in ("6s_opug", "6s_mix") if match else False
        pending = await db.get_pending_signups(match_id)
        pending_by_class = {}
        for s in pending:
            pending_by_class.setdefault(s["class_name"], []).append(s)
        if pending_by_class:
            self.add_item(DenyClassDropdownSelect(match_id, pending_by_class, is_sixs=is_sixs))
        return self


# ── 6s Sign-up views ─────────────────────────────────────────────────────────

class SixsClassButton(ui.Button):
    def __init__(self, class_name, match_id):
        super().__init__(
            label=class_name,
            emoji=SIXS_CLASS_EMOJI[class_name],
            custom_id=f"sixs_signup:{match_id}:{class_name}",
            style=discord.ButtonStyle.secondary,
            row=SIXS_CLASSES.index(class_name) // 4,
        )
        self.class_name = class_name
        self.match_id   = match_id

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)
        match = await db.get_match(self.match_id)
        if not match or match["ended"]:
            await interaction.followup.send("This match has already ended.", ephemeral=True)
            return
        existing_class = await db.get_signup_by_user_and_class(self.match_id, interaction.user.id, self.class_name)
        if existing_class:
            if existing_class["status"] == "denied":
                await interaction.followup.send(
                    f"You've been denied for **{self.class_name}**. Please contact the hoster.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(f"You're already signed up for **{self.class_name}**.", ephemeral=True)
            return

        # Block if already on main roster for any class in this match
        all_signups = await db.get_non_denied_signups_for_user(self.match_id, interaction.user.id)
        for s in all_signups:
            if s["status"] == "accepted":
                accepted_for = await db.get_accepted_signups_for_class(self.match_id, s["class_name"])
                if accepted_for and accepted_for[0]["user_id"] == interaction.user.id:
                    await interaction.followup.send(
                        f"You're already playing this match as **{s['class_name']}**.",
                        ephemeral=True,
                    )
                    return

        clashing = await db.get_accepted_matches_for_user(
            interaction.user.id, exclude_match_id=self.match_id, reference_timestamp=match["timestamp"]
        )
        if clashing:
            clash_names = ", ".join(f"{m['team_name'] or 'a match'} (<#{m['channel_id']}>)" for m in clashing)
            view = ClashConfirmView(self.match_id, self.class_name, clash_names)
            await interaction.followup.send(
                "⚠️ **Warning:** You are already accepted in " + clash_names + ". Sign up anyway?",
                view=view, ephemeral=True
            )
            return
        await _do_signup(interaction, self.match_id, self.class_name)


class SixsSignupView(ui.View):
    def __init__(self, match_id):
        super().__init__(timeout=None)
        for cls in SIXS_CLASSES:
            self.add_item(SixsClassButton(cls, match_id))
        self.add_item(SignOutButton(match_id))


# ── 6s Split view ─────────────────────────────────────────────────────────────

class SixsSwapClassButton(ui.Button):
    def __init__(self, class_name, match_id, row):
        super().__init__(
            label=class_name,
            emoji=SIXS_CLASS_EMOJI[class_name],
            style=discord.ButtonStyle.secondary,
            custom_id="sixs_swap:" + str(match_id) + ":" + class_name,
            row=row,
        )
        self.class_name = class_name
        self.match_id   = match_id

    async def callback(self, interaction):
        split    = await db.get_team_split(self.match_id)
        signups  = await db.get_signups_for_match(self.match_id)
        accepted = [s for s in signups if s["status"] == "accepted"]
        if not split:
            await interaction.response.send_message("No split data.", ephemeral=True)
            return
        red, blu = split["red"], split["blu"]
        red_s = [s for s in accepted if s["user_id"] in red and s["class_name"] == self.class_name]
        blu_s = [s for s in accepted if s["user_id"] in blu and s["class_name"] == self.class_name]
        if not red_s or not blu_s:
            await interaction.response.send_message("Can't swap — missing player on one side.", ephemeral=True)
            return
        red_uid, blu_uid = red_s[0]["user_id"], blu_s[0]["user_id"]
        new_red = [blu_uid if u == red_uid else u for u in red]
        new_blu = [red_uid if u == blu_uid else u for u in blu]
        await db.save_team_split(self.match_id, new_red, new_blu)
        red_team = [s for s in accepted if s["user_id"] in new_red]
        blu_team = [s for s in accepted if s["user_id"] in new_blu]
        text = build_6s_split_view_text(red_team, blu_team)
        view = SixsSplitView(self.match_id, red_team, blu_team)
        await interaction.response.edit_message(content=text, view=view)


class SixsPostTeamsButton(ui.Button):
    def __init__(self, match_id):
        super().__init__(label="Post teams", style=discord.ButtonStyle.success,
                         custom_id="sixs_post_teams:" + str(match_id), row=2)
        self.match_id = match_id

    async def callback(self, interaction):
        await interaction.response.defer()
        split    = await db.get_team_split(self.match_id)
        signups  = await db.get_signups_for_match(self.match_id)
        accepted = [s for s in signups if s["status"] == "accepted"]
        match    = await db.get_match(self.match_id)
        red_uids, blu_uids = split["red"], split["blu"]
        red_team = [s for s in accepted if s["user_id"] in red_uids]
        blu_team = [s for s in accepted if s["user_id"] in blu_uids]
        subs     = [s for s in accepted if s["user_id"] not in red_uids and s["user_id"] not in blu_uids]
        channel  = interaction.client.get_channel(match["channel_id"])
        if channel:
            await channel.send(build_6s_opug_teams_message(match, red_team, blu_team, subs))

        await db.set_teams_posted(self.match_id)

        try:
            await interaction.message.delete()
        except Exception:
            pass
        await interaction.followup.send("✅ Teams posted!", ephemeral=True)


class SixsSplitView(ui.View):
    def __init__(self, match_id, red_team, blu_team):
        super().__init__(timeout=None)
        self.match_id = match_id
        for i, cls in enumerate(SIXS_CLASSES):
            row = i // 4
            self.add_item(SixsSwapClassButton(cls, match_id, row))
        self.add_item(SixsPostTeamsButton(match_id))


# ── OPUG Team Split ───────────────────────────────────────────────────────────

class SwapClassButton(ui.Button):
    def __init__(self, class_name, match_id, row):
        super().__init__(
            label=class_name,
            emoji=CLASS_EMOJI[class_name],
            style=discord.ButtonStyle.secondary,
            custom_id="swap:" + str(match_id) + ":" + class_name,
            row=row,
        )
        self.class_name = class_name
        self.match_id   = match_id

    async def callback(self, interaction):
        split = await db.get_team_split(self.match_id)
        if not split:
            await interaction.response.send_message("No split data found.", ephemeral=True)
            return

        red = split["red"]
        blu = split["blu"]

        # Find this class's players in each team and swap
        red_player = next((uid for uid in red if uid in red), None)
        blu_player = next((uid for uid in blu if uid in blu), None)

        # Get all accepted signups to find class assignments
        signups = await db.get_signups_for_match(self.match_id)
        accepted = [s for s in signups if s["status"] == "accepted"]

        red_signups = [s for s in accepted if s["user_id"] in red and s["class_name"] == self.class_name]
        blu_signups = [s for s in accepted if s["user_id"] in blu and s["class_name"] == self.class_name]

        if not red_signups or not blu_signups:
            await interaction.response.send_message(
                "Can't swap — one team has no player for " + self.class_name + ".", ephemeral=True
            )
            return

        red_uid = red_signups[0]["user_id"]
        blu_uid = blu_signups[0]["user_id"]

        # Swap
        new_red = [blu_uid if uid == red_uid else uid for uid in red]
        new_blu = [red_uid if uid == blu_uid else uid for uid in blu]

        await db.save_team_split(self.match_id, new_red, new_blu)

        # Rebuild view text
        new_split    = {"red": new_red, "blu": new_blu}
        red_team     = [s for s in accepted if s["user_id"] in new_red and s["class_name"] == self.class_name or
                        (s["user_id"] in new_red and s["class_name"] != self.class_name and s["user_id"] in new_red)]
        # Simpler: rebuild from new split
        red_s = [s for s in accepted if s["user_id"] in new_red]
        blu_s = [s for s in accepted if s["user_id"] in new_blu]

        text = build_split_view_text(red_s, blu_s)
        view = SplitView(self.match_id, red_s, blu_s)
        await interaction.response.edit_message(content=text, view=view)


class PostTeamsButton(ui.Button):
    def __init__(self, match_id):
        super().__init__(
            label="Post teams",
            style=discord.ButtonStyle.success,
            custom_id="post_teams:" + str(match_id),
            row=4,
        )
        self.match_id = match_id

    async def callback(self, interaction):
        await interaction.response.defer()
        split    = await db.get_team_split(self.match_id)
        signups  = await db.get_signups_for_match(self.match_id)
        accepted = [s for s in signups if s["status"] == "accepted"]
        match    = await db.get_match(self.match_id)

        red_uids = split["red"]
        blu_uids = split["blu"]

        # Main roster slots only (first 2 per class)
        red_team = []
        blu_team = []
        subs     = []
        for cls in TF2_CLASSES:
            cls_accepted = [s for s in accepted if s["class_name"] == cls]
            for s in cls_accepted:
                if s["user_id"] in red_uids:
                    red_team.append(s)
                elif s["user_id"] in blu_uids:
                    blu_team.append(s)
                else:
                    subs.append(s)

        channel = interaction.client.get_channel(match["channel_id"])
        if channel:
            msg_text = build_opug_teams_message(match, red_team, blu_team, subs)
            await channel.send(msg_text)

        await db.set_teams_posted(self.match_id)

        # Delete the balancing chat message
        try:
            await interaction.message.delete()
        except Exception:
            pass

        await interaction.followup.send("✅ Teams posted!", ephemeral=True)


class SplitView(ui.View):
    def __init__(self, match_id, red_team, blu_team):
        super().__init__(timeout=None)
        self.match_id = match_id
        # Add swap buttons rows 0-1
        for i, cls in enumerate(TF2_CLASSES):
            row = 2 + i // 5
            self.add_item(SwapClassButton(cls, match_id, row))
        self.add_item(PostTeamsButton(match_id))


# ── Fresh Pug Manage View ─────────────────────────────────────────────────────

class OPugCancelAfterStartView(ui.View):
    def __init__(self, match_id):
        super().__init__(timeout=60)
        self.match_id = match_id

    @ui.button(label="Yes, cancel anyway", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        success = await do_cancel(interaction.client, self.match_id)
        if success:
            await interaction.followup.send("✅ Match cancelled.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Could not cancel.", ephemeral=True)

    @ui.button(label="Never mind", style=discord.ButtonStyle.secondary)
    async def abort(self, interaction, button):
        await interaction.response.edit_message(content="Cancellation aborted.", view=None)


class FreshPugManageView(ui.View):
    def __init__(self, match_id):
        super().__init__(timeout=300)
        self.match_id = match_id

    @ui.button(label="Conclude PUG", style=discord.ButtonStyle.success, row=0)
    async def conclude(self, interaction, button):
        match = await db.get_match(self.match_id)
        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return
        if time.time() < match["timestamp"]:
            remaining = int(match["timestamp"] - time.time())
            h, m = divmod(remaining // 60, 60)
            await interaction.response.send_message(
                f"❌ You can only conclude after the PUG has started. Starts in **{h}h {m}m**.",
                ephemeral=True,
            )
            return
        view = FreshPugConcludeConfirmView(self.match_id)
        await interaction.response.send_message(
            "Conclude this Fresh PUG? This will archive the thread and clean up.",
            view=view, ephemeral=True,
        )

    @ui.button(label="Cancel PUG", style=discord.ButtonStyle.danger, row=0)
    async def cancel(self, interaction, button):
        view = FreshPugCancelConfirmView(self.match_id)
        await interaction.response.send_message(
            "⚠️ Cancel this Fresh PUG?",
            view=view, ephemeral=True,
        )


class FreshPugConcludeConfirmView(ui.View):
    def __init__(self, match_id):
        super().__init__(timeout=60)
        self.match_id = match_id

    @ui.button(label="Yes, conclude", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        match   = await db.get_match(self.match_id)
        channel = interaction.client.get_channel(match["channel_id"])

        if channel:
            try:
                async for msg in channel.history(limit=200, oldest_first=True):
                    if msg.author.id == interaction.client.user.id:
                        try:
                            await msg.delete()
                        except Exception:
                            pass
            except Exception:
                pass

        ongoing_channel_id = getattr(interaction.client, "ongoing_channel", None)
        if ongoing_channel_id and match["ongoing_msg_id"]:
            try:
                oc   = interaction.client.get_channel(ongoing_channel_id)
                omsg = await oc.fetch_message(match["ongoing_msg_id"])
                await omsg.delete()
            except Exception:
                pass

        await db.end_match(self.match_id)
        await do_archive(interaction.client, self.match_id, concluded=True)
        await interaction.followup.send("✅ Fresh PUG concluded and archived.", ephemeral=True)

    @ui.button(label="Never mind", style=discord.ButtonStyle.secondary)
    async def abort(self, interaction, button):
        await interaction.response.edit_message(content="Cancelled.", view=None)


class FreshPugCancelConfirmView(ui.View):
    def __init__(self, match_id):
        super().__init__(timeout=60)
        self.match_id = match_id

    @ui.button(label="Yes, cancel", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        match   = await db.get_match(self.match_id)
        channel = interaction.client.get_channel(match["channel_id"])

        if channel:
            try:
                async for msg in channel.history(limit=200, oldest_first=True):
                    if msg.author.id == interaction.client.user.id:
                        try:
                            await msg.delete()
                        except Exception:
                            pass
            except Exception:
                pass

        ongoing_channel_id = getattr(interaction.client, "ongoing_channel", None)
        if ongoing_channel_id and match["ongoing_msg_id"]:
            try:
                oc   = interaction.client.get_channel(ongoing_channel_id)
                omsg = await oc.fetch_message(match["ongoing_msg_id"])
                await omsg.delete()
            except Exception:
                pass

        await db.end_match(self.match_id)
        await do_archive(interaction.client, self.match_id, concluded=False)
        await interaction.followup.send("✅ Fresh PUG cancelled.", ephemeral=True)

    @ui.button(label="Never mind", style=discord.ButtonStyle.secondary)
    async def abort(self, interaction, button):
        await interaction.response.edit_message(content="Cancellation aborted.", view=None)


class ManageView(ui.View):
    def __init__(self, match_id):
        super().__init__(timeout=300)
        self.match_id = match_id

    @classmethod
    async def create(cls, match_id):
        return cls(match_id)

    @ui.button(label="Accept players", style=discord.ButtonStyle.primary, row=0)
    async def review_pending(self, interaction, button):
        pending = await db.get_pending_signups(self.match_id)
        if not pending:
            await interaction.response.send_message("No pending sign-ups right now.", ephemeral=True)
            return
        text, _ = await build_manage_text(self.match_id)
        view     = await ReviewView.create(self.match_id)
        await interaction.response.send_message(text, view=view, ephemeral=True)

    @ui.button(label="Deny players", style=discord.ButtonStyle.danger, row=0)
    async def deny_players(self, interaction, button):
        pending = await db.get_pending_signups(self.match_id)
        if not pending:
            await interaction.response.send_message("No pending sign-ups to deny right now.", ephemeral=True)
            return
        text, _ = await build_manage_text(self.match_id)
        view     = await DenyReviewView.create(self.match_id)
        await interaction.response.send_message(text, view=view, ephemeral=True)

    @ui.button(label="Conclude match", style=discord.ButtonStyle.success, row=1)
    async def conclude_match(self, interaction, button):
        match = await db.get_match(self.match_id)
        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return
        if time.time() < match["timestamp"]:
            remaining = int(match["timestamp"] - time.time())
            h, m = divmod(remaining // 60, 60)
            await interaction.response.send_message(
                f"❌ You can only conclude after the match has started. "
                f"Starts in **{h}h {m}m**.",
                ephemeral=True,
            )
            return
        if match["type"] in ("opug", "6s_opug"):
            split = await db.get_team_split(self.match_id)
            if not split:
                await interaction.response.send_message(
                    "❌ Teams haven't been split yet. Use **Split teams** first, then post the teams before concluding.",
                    ephemeral=True,
                )
                return
            try:
                teams_posted = match["teams_posted"]
            except (IndexError, KeyError):
                teams_posted = None
            if not teams_posted:
                await interaction.response.send_message(
                    "❌ Teams have been split but not posted yet. Press **Post teams** in the balancing chat before concluding.",
                    ephemeral=True,
                )
                return
        view = ConcludeConfirmView(self.match_id)
        await interaction.response.send_message(
            "Ready to conclude this match? This will post a conclusion notice, "
            "archive the thread, and log it to the archive channel.",
            view=view, ephemeral=True,
        )

    @ui.button(label="Split teams", style=discord.ButtonStyle.primary, row=1)
    async def split_teams(self, interaction, button):
        match = await db.get_match(self.match_id)
        if not match or match["type"] not in ("opug", "6s_opug"):
            await interaction.response.send_message(
                "❌ Team splitting is only available for Organised PUGs.", ephemeral=True
            )
            return

        signups  = await db.get_signups_for_match(self.match_id)
        accepted = [s for s in signups if s["status"] == "accepted"]

        # Count filled slots
        is_sixs     = match["type"] == "6s_opug"
        class_list  = SIXS_CLASSES if is_sixs else TF2_CLASSES
        cap         = 12 if is_sixs else 18
        slot_counts = {}
        for s in accepted:
            slot_counts[s["class_name"]] = slot_counts.get(s["class_name"], 0) + 1
        total = sum(min(v, 2) for v in slot_counts.values())

        if total < cap:
            await interaction.response.send_message(
                f"❌ Not all {cap} slots are filled yet ({total}/{cap}). Please wait until all players have signed up.",
                ephemeral=True,
            )
            return

        # Default split: first accepted per class → RED, second → BLU
        red_uids = []
        blu_uids = []
        for cls in class_list:
            cls_players = [s for s in accepted if s["class_name"] == cls][:2]
            if len(cls_players) >= 1:
                red_uids.append(cls_players[0]["user_id"])
            if len(cls_players) >= 2:
                blu_uids.append(cls_players[1]["user_id"])

        await db.save_team_split(self.match_id, red_uids, blu_uids)

        red_team = [s for s in accepted if s["user_id"] in red_uids]
        blu_team = [s for s in accepted if s["user_id"] in blu_uids]

        if is_sixs:
            text = build_6s_split_view_text(red_team, blu_team)
            view = SixsSplitView(self.match_id, red_team, blu_team)
        else:
            text = build_split_view_text(red_team, blu_team)
            view = SplitView(self.match_id, red_team, blu_team)

        # Post to balancing chat
        bal_ch_id = interaction.client.config.get("balancing_chat_id")
        if bal_ch_id:
            bal_ch = interaction.client.get_channel(int(bal_ch_id))
            if bal_ch:
                await bal_ch.send(text, view=view)
                await interaction.response.send_message(
                    f"✅ Split posted to {bal_ch.mention}!", ephemeral=True
                )
                return
        await interaction.response.send_message(text, view=view, ephemeral=True)

    @ui.button(label="Cancel match", style=discord.ButtonStyle.danger, row=1)
    async def cancel_match(self, interaction, button):
        match = await db.get_match(self.match_id)
        # Warn if match already started — they should conclude instead
        if match and match["type"] in ("opug", "6s_opug") and time.time() > match["timestamp"]:
            view = OPugCancelAfterStartView(self.match_id)
            await interaction.response.send_message(
                "⚠️ This PUG has already started. If the match was played, use **Conclude** instead.\n"
                "Are you sure you want to cancel?",
                view=view, ephemeral=True,
            )
            return
        view = CancelConfirmView(self.match_id)
        await interaction.response.send_message(
            "⚠️ Are you sure you want to cancel this match?\n"
            "This will delete the embed, ping accepted players with a 24h notice, "
            "and archive the thread.",
            view=view, ephemeral=True,
        )
