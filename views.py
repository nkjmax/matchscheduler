import time
import discord
from discord import ui

from embeds import TF2_CLASSES, CLASS_EMOJI, build_mix_message, build_match_embed, build_archive_message
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
        return  # Only one player, nothing to reorder

    # Check LP status for each
    lp_status = {}
    for s in accepted:
        lp_status[s["id"]] = await is_lp(client, s["user_id"])

    main_roster = accepted[0]  # First accepted = main roster
    subs        = accepted[1:]

    # If main roster is LP, find first non-LP sub to swap with
    if lp_status[main_roster["id"]]:
        non_lp_sub = next((s for s in subs if not lp_status[s["id"]]), None)
        if non_lp_sub:
            # Swap: demote main to sub order (move to after last LP sub),
            # promote non_lp_sub to main by swapping their signup IDs
            # We do this by updating the id ordering — easiest is to
            # re-insert: delete main, re-insert at end, so non_lp_sub becomes first
            async with __import__('aiosqlite').connect(__import__('db').DB_PATH) as adb:
                # Get the main roster signup details
                await adb.execute(
                    "UPDATE signups SET id = id WHERE id = ?",
                    (main_roster["id"],)
                )
                # Swap IDs by updating timestamps — use a temp value
                # Actually simplest: change the signup order by re-inserting
                # Get full rows
                main_data = dict(main_roster)
                sub_data  = dict(non_lp_sub)

                # Delete both
                await adb.execute("DELETE FROM signups WHERE id IN (?, ?)",
                    (main_data["id"], sub_data["id"]))

                # Re-insert non-LP first (gets lower auto-increment), LP second
                await adb.execute(
                    "INSERT INTO signups (match_id, user_id, username, class_name, team, status) VALUES (?,?,?,?,?,?)",
                    (sub_data["match_id"], sub_data["user_id"], sub_data["username"],
                     sub_data["class_name"], sub_data["team"], sub_data["status"])
                )
                await adb.execute(
                    "INSERT INTO signups (match_id, user_id, username, class_name, team, status) VALUES (?,?,?,?,?,?)",
                    (main_data["match_id"], main_data["user_id"], main_data["username"],
                     main_data["class_name"], main_data["team"], main_data["status"])
                )
                await adb.commit()


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
        if match["type"] == "mix":
            pug_role_id = getattr(client, "config", {}).get("pug_role_id")
            await msg.edit(content=build_mix_message(match, signups, pug_role_id=pug_role_id), embed=None)
        else:
            await msg.edit(embed=build_match_embed(match, signups))
    except Exception:
        pass

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


async def do_archive(client, match_id, concluded: bool):
    """
    Shared archive logic for both conclude and cancel.
    Posts summary to archive channel, creates archive thread with message log,
    then locks and archives the original thread.
    """
    match   = await db.get_match(match_id)
    signups = await db.get_signups_for_match(match_id)

    archive_channel_id = client.config.get("archive_channel_id")
    if not archive_channel_id:
        return

    archive_ch = client.get_channel(int(archive_channel_id))
    if not archive_ch:
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
            await thread.edit(archived=True, locked=True)
        except Exception:
            pass


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

    # Archive before ending
    await do_archive(client, match_id, concluded=False)

    cancel_embed = discord.Embed(
        title="❌ Match Cancelled",
        description=(
            f"**{'Mix' if match['type'] == 'mix' else 'PUG'}**"
            + (f" vs {match['team_name']}" if match["team_name"] else "")
            + f"\nHosted by {match['created_by_name']} has been **cancelled**."
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
    else:
        await db.end_match(match_id)

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
        await do_signout(interaction.client, self.match_id, self.user_id, self.class_name)
        await interaction.response.edit_message(
            content="You have been signed out. Please find a replacement as soon as possible.",
            view=None,
        )

    @ui.button(label="Stay signed up", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(
            content="Sign-out cancelled. You're still signed up.", view=None
        )


class SignOutClassPickerView(ui.View):
    """Shown when a user is signed up on multiple classes and wants to sign out."""
    def __init__(self, match_id, user_id, signups, within_hour):
        super().__init__(timeout=60)
        self.match_id    = match_id
        self.user_id     = user_id
        self.within_hour = within_hour
        options = [
            discord.SelectOption(
                label=s["class_name"],
                value=s["class_name"],
                emoji=CLASS_EMOJI.get(s["class_name"]),
                description=f"Status: {s['status']}"
            )
            for s in signups
        ]
        select = ui.Select(placeholder="Select class to sign out of…", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction):
        class_name = interaction.data["values"][0]
        if self.within_hour:
            lp_warning = ""
            if LOW_PRIORITY_ROLE_ID:
                lp_warning = f"\n⚠️ You will receive the <@&{LOW_PRIORITY_ROLE_ID}> role if you fail to find a replacement."
            view = SignOutConfirmView(self.match_id, self.user_id, class_name)
            await interaction.response.edit_message(
                content=f"⚠️ **Warning:** The match starts in less than 1 hour.{lp_warning}\nSign out of **{class_name}**?",
                view=view,
            )
        else:
            await do_signout(interaction.client, self.match_id, self.user_id, class_name)
            await interaction.response.edit_message(
                content=f"You have been signed out of **{class_name}**.", view=None
            )


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
    team_name    = match["team_name"] or "Mix"

    if is_rostered:
        next_sub = await db.get_next_accepted_for_class(match_id, class_name, user_id)

        # 6. Sub promotion — remove their other sub slots, ping in thread
        if next_sub:
            await db.remove_sub_slots_for_user(match_id, next_sub["user_id"], class_name)
            if match["thread_id"]:
                try:
                    thread = client.get_channel(match["thread_id"])
                    if thread:
                        await thread.send(
                            f"<@{next_sub['user_id']}> you have been moved to the **{class_name}** "
                            f"slot on the Mix Team — the previous player signed out."
                        )
                except Exception:
                    pass

        # 1. Ping original hoster in hoster channel (not role ping, just the hoster)
        hoster_channel_id = getattr(client, "config", {}).get("hoster_channel_id")
        if hoster_channel_id:
            hoster_ch = client.get_channel(int(hoster_channel_id))
            if hoster_ch:
                promoted_line = ""
                if next_sub:
                    promoted_line = f" **{next_sub['username']}** has been moved to the main roster."
                await hoster_ch.send(
                    f"<@{match['created_by']}> ⚠️ **{signup['username']}** has signed out of "
                    f"**{class_name}** in <#{match['channel_id']}> ({team_name} vs Mix).{promoted_line}"
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

        if len(signups) == 1:
            # Only one class — confirm or go straight to signout
            if within_hour:
                lp_warning = ""
                if LOW_PRIORITY_ROLE_ID:
                    lp_warning = f"\n⚠️ You will receive the <@&{LOW_PRIORITY_ROLE_ID}> role if you fail to find a replacement."
                view = SignOutConfirmView(self.match_id, interaction.user.id, signups[0]["class_name"])
                await interaction.followup.send(
                    f"⚠️ **Warning:** The match starts in less than 1 hour.{lp_warning}\nAre you sure?",
                    view=view, ephemeral=True,
                )
            else:
                await do_signout(interaction.client, self.match_id, interaction.user.id, signups[0]["class_name"])
                await interaction.followup.send("You have been signed out.", ephemeral=True)
        else:
            # Multiple classes — show a picker
            view = SignOutClassPickerView(self.match_id, interaction.user.id, signups, within_hour)
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
        earliest_pending = await db.get_earliest_pending_for_class(self.match_id, self.class_name)
        if earliest_pending and earliest_pending["id"] < self.signup_id:
            await interaction.response.send_message(
                f"⚠️ **{earliest_pending['username']}** signed up for **{self.class_name}** "
                f"before **{self.username}**. Please review their sign-up first.",
                ephemeral=True,
            )
            return

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

        # Block if already signed up (non-denied) for THIS specific class
        existing_class = await db.get_signup_by_user_and_class(self.match_id, interaction.user.id, self.class_name)
        if existing_class and existing_class["status"] != "denied":
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
                team_name  = match_row["team_name"] or "Mix"
                # Ping both: the hoster of the new mix AND hosters of clashing mixes
                hoster_pings = {match_row["created_by"]}
                for m in clashing:
                    hoster_pings.add(m["created_by"])
                pings_str  = " ".join(f"<@{uid}>" for uid in hoster_pings)
                clash_refs = ", ".join(
                    f"<#{m['channel_id']}> against {m['team_name'] or 'Mix'}" for m in clashing
                )
                await hoster_ch.send(
                    f"{pings_str} ⚠️ **{interaction.user.display_name}** signed up for **{class_name}** "
                    f"in <#{match_row['channel_id']}> ({team_name} vs Mix) "
                    f"but is already accepted in {clash_refs}."
                )

    await refresh_message(interaction.client, match_id)


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
                if s["class_name"] not in seen:
                    seen.add(s["class_name"])
                    pings.append(f"<@{s['user_id']}>")
            team = match["team_name"] or "Mix"
            conclude_msg = await channel.send(
                f"{' '.join(pings)}\n"
                f"🏁 **{team} vs Mix Team** has been concluded. Thanks for playing! 🫡"
            )
            await db.set_conclude_msg(self.match_id, conclude_msg.id, match["channel_id"])

        # Archive summary + thread messages to archive channel
        await do_archive(interaction.client, self.match_id, concluded=True)

        await db.end_match(self.match_id)
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

    player_data = {}
    for s in signups:
        uid = s["user_id"]
        if uid not in player_data:
            player_data[uid] = {"username": s["username"], "accepted": [], "pending": [], "denied": []}
        player_data[uid][s["status"]].append(s["class_name"])

    for uid in player_data:
        for key in ("accepted", "pending", "denied"):
            player_data[uid][key].sort(key=lambda c: TF2_CLASSES.index(c) if c in TF2_CLASSES else 99)

    def fmt(classes):
        return ", ".join(classes) if classes else "\u2014"

    header = "**" + team + " vs Mix \u2014 signups**\n"
    lines  = [header]

    accepted_players = [p for p in player_data.values() if p["accepted"]]
    pending_players  = [p for p in player_data.values() if p["pending"]]
    denied_players   = [p for p in player_data.values() if p["denied"] and not p["accepted"] and not p["pending"]]

    if accepted_players:
        lines.append("\u2705 **Accepted:**")
        for p in accepted_players:
            lines.append("\u2022 **" + p["username"] + "** \u2014 " + fmt(p["accepted"]))

    if pending_players:
        lines.append("\n\u23f3 **Pending:**")
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
    def __init__(self, match_id, pending_by_class):
        self.match_id = match_id
        options = []
        for cls in TF2_CLASSES:
            count = len(pending_by_class.get(cls, []))
            if count:
                options.append(discord.SelectOption(
                    label=cls,
                    value=cls,
                    description=str(count) + " pending",
                ))
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
        text = "**" + class_name + "**  —  click a player to accept"
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
        current = await db.get_signup_by_id(self.signup_id)
        already = current and current["status"] == "accepted"
        await _do_accept(interaction, self.match_id, self.signup_id, self.username, self.class_name, self.filled, already, current)

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Cancelled.", view=None)


async def _do_accept(interaction, match_id, signup_id, username, class_name, filled, already, current):
    await db.update_signup_status(signup_id, "accepted")

    is_main_roster = filled == 0 and not already
    if is_main_roster:
        user_id = current["user_id"] if current else None
        if user_id:
            await db.remove_sub_slots_for_user(match_id, user_id, class_name)

    # Reorder roster so non-LP players have priority over LP players
    await reorder_class_roster(interaction.client, match_id, class_name)
    await refresh_message(interaction.client, match_id)

    # Check actual position after reorder to determine if on main roster or sub
    accepted_after = await db.get_accepted_signups_for_class(match_id, class_name)
    user_id = current["user_id"] if current else None
    on_main = len(accepted_after) > 0 and accepted_after[0]["user_id"] == user_id
    result = "accepted on " + class_name if on_main else "added as sub"
    await interaction.response.send_message(
        "✅  " + username + " — " + result + ".", ephemeral=True
    )

    # Refresh class view
    try:
        pending    = await db.get_pending_signups(match_id)
        class_pend = sorted([s for s in pending if s["class_name"] == class_name], key=lambda s: s["id"])
        if class_pend:
            view = PlayerPickView(match_id, class_name, class_pend)
            text = "**" + class_name + "**  —  click a player to accept"
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

        await _do_accept(interaction, self.match_id, self.signup_id, self.username, self.class_name, filled, already, current)

        # Refresh the class view (message may have been dismissed — ignore if so)
        try:
            pending    = await db.get_pending_signups(self.match_id)
            class_pend = sorted([s for s in pending if s["class_name"] == self.class_name], key=lambda s: s["id"])
            if class_pend:
                view = PlayerPickView(self.match_id, self.class_name, class_pend)
                text = "**" + self.class_name + "**  —  click a player to accept"
                await interaction.message.edit(content=text, view=view)
            else:
                view = await ReviewView.create(self.match_id)
                text, _ = await build_manage_text(self.match_id)
                await interaction.message.edit(content=text, view=view)
        except Exception:
            pass


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
        await db.update_signup_status(self.signup_id, "denied")
        await refresh_message(interaction.client, self.match_id)
        await interaction.response.send_message(
            "❌  " + self.username + " denied for " + self.class_name + ".", ephemeral=True
        )

        pending    = await db.get_pending_signups(self.match_id)
        class_pend = sorted([s for s in pending if s["class_name"] == self.class_name], key=lambda s: s["id"])
        if class_pend:
            view = PlayerPickView(self.match_id, self.class_name, class_pend)
            text = "**" + self.class_name + "**  —  click a player to accept"
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
        pending = await db.get_pending_signups(match_id)
        pending_by_class = {}
        for s in pending:
            pending_by_class.setdefault(s["class_name"], []).append(s)
        if pending_by_class:
            self.add_item(ClassDropdownSelect(match_id, pending_by_class))
        return self


class ManageView(ui.View):
    def __init__(self, match_id):
        super().__init__(timeout=300)
        self.match_id = match_id

    @classmethod
    async def create(cls, match_id):
        return cls(match_id)

    @ui.button(label="Review pending sign-ups", style=discord.ButtonStyle.primary, row=0)
    async def review_pending(self, interaction, button):
        pending = await db.get_pending_signups(self.match_id)
        if not pending:
            await interaction.response.send_message("No pending sign-ups right now.", ephemeral=True)
            return
        text, _ = await build_manage_text(self.match_id)
        view     = await ReviewView.create(self.match_id)
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
        view = ConcludeConfirmView(self.match_id)
        await interaction.response.send_message(
            "Ready to conclude this match? This will post a conclusion notice, "
            "archive the thread, and log it to the archive channel.",
            view=view, ephemeral=True,
        )

    @ui.button(label="Cancel match", style=discord.ButtonStyle.danger, row=1)
    async def cancel_match(self, interaction, button):
        view = CancelConfirmView(self.match_id)
        await interaction.response.send_message(
            "⚠️ Are you sure you want to cancel this match?\n"
            "This will delete the embed, ping accepted players with a 24h notice, "
            "and archive the thread.",
            view=view, ephemeral=True,
        )
