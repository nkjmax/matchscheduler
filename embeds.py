import discord

TF2_CLASSES = [
    "Scout", "Soldier", "Pyro", "Demoman",
    "Heavy", "Engineer", "Medic", "Sniper", "Spy"
]

CLASS_EMOJI = {
    "Scout":    "<:1_Scout:1353833221799804989>",
    "Soldier":  "<:2_Soldier:1353833224358330519>",
    "Pyro":     "<:3_Pyro:1353833227508383764>",
    "Demoman":  "<:4_Demoman:1353833229731500033>",
    "Heavy":    "<:5_Heavy:1353833232390557868>",
    "Engineer": "<:6_Engineer:1353833234500157491>",
    "Medic":    "<:7_Medic:1353833236605960323>",
    "Sniper":   "<:8_Sniper:1353833239822733423>",
    "Spy":      "<:9_Spy:1353833249373421680>",
}

DIVISIONS = [
    "Iron",
    "Low Steel", "Steel", "High Steel",
    "Low Silver", "Silver", "High Silver",
    "Low Plat", "Plat",
]


def build_mix_message(match, signups, pug_role_id=None):
    mix_starters = {c: None for c in TF2_CLASSES}
    mix_subs     = []
    denied       = []

    sub_by_player = {}  # user_id -> {user_id, classes:[]}
    for s in signups:
        if s["status"] == "accepted":
            if mix_starters[s["class_name"]] is None:
                mix_starters[s["class_name"]] = f"<@{s['user_id']}>"
            else:
                uid = s["user_id"]
                if uid not in sub_by_player:
                    sub_by_player[uid] = {"user_id": uid, "classes": []}
                sub_by_player[uid]["classes"].append(s["class_name"])
        elif s["status"] == "denied":
            denied.append((f"<@{s['user_id']}>", s["class_name"]))

    # Consolidate pending by player (may be on multiple classes)
    pending_raw = [s for s in signups if s["status"] == "pending"]
    pending_by_player = {}
    for s in pending_raw:
        uid = s["user_id"]
        if uid not in pending_by_player:
            pending_by_player[uid] = {"user_id": uid, "classes": []}
        pending_by_player[uid]["classes"].append(s["class_name"])
    # Sort classes in TF2 order
    tf2_order = ["Scout","Soldier","Pyro","Demoman","Heavy","Engineer","Medic","Sniper","Spy"]
    for uid in pending_by_player:
        pending_by_player[uid]["classes"].sort(key=lambda c: tf2_order.index(c) if c in tf2_order else 99)
    pending_list = list(pending_by_player.values())

    team_name = match["team_name"] or "Team"
    division  = match["division"]  or "tbc"
    map_name  = match["map_name"]  or "tbc"
    server    = match["server"]    or "tbc"
    hoster    = f"<@{match['created_by']}>"

    role_id  = match["pug_role_id"] or pug_role_id
    pug_ping = f"<@&{role_id}>" if role_id else "@here"

    ts      = match["timestamp"]
    ts_line = f"<t:{ts}:F> <t:{ts}:R>" if ts else "tbc"

    SEP = "> ---------------------------------------------"

    # Parse host team roster — stored as newline-separated entries in class order
    host_roster_raw = match["host_roster"] if match["host_roster"] else None
    host_entries = [e.strip() for e in host_roster_raw.split("\n")] if host_roster_raw else []
    # Pad/truncate to 9
    while len(host_entries) < 9:
        host_entries.append("")
    host_map = {cls: host_entries[i] for i, cls in enumerate(TF2_CLASSES)}

    def class_lines(cmap):
        return "\n".join(
            f"> {CLASS_EMOJI[cls]}: {cmap[cls] or ''}"
            for cls in TF2_CLASSES
        )

    # Sort each sub's classes in TF2 order
    tf2_order = TF2_CLASSES
    subs_entries = []
    for p in sub_by_player.values():
        p["classes"].sort(key=lambda c: tf2_order.index(c) if c in tf2_order else 99)
        class_emojis = ", ".join(CLASS_EMOJI[c] for c in p["classes"])
        subs_entries.append(f"<@{p['user_id']}> - {class_emojis}")
    subs_line = "\n> ".join(subs_entries) if subs_entries else ""

    extra_lines = []
    if pending_list:
        extra_lines.append("> ⏳ **Pending:**")
        for p in pending_list:
            classes_str = ", ".join(p["classes"])
            extra_lines.append(f"> - <@{p['user_id']}> — {classes_str}")
    if denied:
        extra_lines.append("> ❌ **Denied:**")
        for ping, cls in denied:
            extra_lines.append(f"> - {ping} — {cls}")

    all_lines = [
        f"> ## ✨ {team_name} vs Mix team {pug_ping}",
        "> ",
        f"> ## - Date : {ts_line}",
        f"> - Division : {division}",
        f"> - Map : **{map_name}**",
        f"> - Server: {server}",
        "> - Mode : **9v9 HL**",
        f"> - Hoster: {hoster}",
        SEP,
        f"> {team_name} Team",
        class_lines(host_map),
        SEP,
        "> Mix Team",
        class_lines(mix_starters),
        "> ",
        f"> Subs : {subs_line}",
        "> Tag the hoster to be accepted on mix team",
        SEP,
    ] + extra_lines + [
        "> ",
        "> -# Thank you and enjoy the game ! 🫡",
    ]

    return "\n".join(all_lines)


def build_archive_message(match, signups):
    """Concise archive summary posted to the archive channel on match conclusion."""
    team_name = match["team_name"] or "Mix"
    division  = match["division"]  or "—"
    map_name  = match["map_name"]  or "—"
    server    = match["server"]    or "—"
    hoster    = f"<@{match['created_by']}>"
    ts        = match["timestamp"]
    ts_str    = f"<t:{ts}:F>" if ts else "—"

    # Build accepted roster (first per class = starter, rest = sub)
    seen = set()
    roster_lines = []
    sub_pings    = []
    for s in signups:
        if s["status"] != "accepted":
            continue
        if s["class_name"] not in seen:
            seen.add(s["class_name"])
            roster_lines.append(f"{CLASS_EMOJI[s['class_name']]} <@{s['user_id']}>")
        else:
            sub_pings.append(f"<@{s['user_id']}>")

    roster_str = " ".join(roster_lines) if roster_lines else "—"
    subs_str   = " ".join(sub_pings) if sub_pings else "—"

    # Host team from stored roster (comma-separated entries saved as newlines)
    host_roster_raw = match["host_roster"] if match["host_roster"] else None
    if host_roster_raw:
        host_entries = [e.strip() for e in host_roster_raw.split("\n")]
        host_team_lines = []
        for cls, entry in zip(TF2_CLASSES, host_entries):
            if entry:
                host_team_lines.append(f"{CLASS_EMOJI[cls]} {entry}")
        host_team_str = " ".join(host_team_lines) if host_team_lines else "—"
    else:
        host_team_str = "—"

    return (
        f"**{team_name} vs Mix** | {division} | {ts_str}\n"
        f"Map: {map_name} | Server: {server} | Hoster: {hoster}\n"
        f"**{team_name} Team:** {host_team_str}\n"
        f"**Mix Team:** {roster_str}\n"
        f"Subs: {subs_str}"
    )


def build_ongoing_line(match, guild_id=None, channel_id=None, signups=None):
    ts          = match["timestamp"]
    ts_full     = f"<t:{ts}:F>" if ts else "tbc"
    ts_rel      = f"<t:{ts}:R>" if ts else ""
    cid         = channel_id or match["channel_id"]
    chan_mention = f"<#{cid}>" if cid else ""
    team_name   = match["team_name"] or match["created_by_name"].upper()
    kind        = match["type"].upper()
    if kind == "MIX":
        label = f"**{team_name}** vs Mix | HL | {ts_full}  {ts_rel}"
    else:
        label = f"**{team_name} PUG** | HL | {ts_full}  {ts_rel}"

    line1 = f"> {label} | {chan_mention}"

    # Build roster status line
    if signups is not None:
        filled_classes = set()
        for s in signups:
            if s["status"] == "accepted" and s["class_name"] not in filled_classes:
                filled_classes.add(s["class_name"])
        count    = len(filled_classes)
        missing  = [cls for cls in TF2_CLASSES if cls not in filled_classes]
        if missing:
            missing_emojis = " ".join(CLASS_EMOJI[cls] for cls in missing)
            line2 = f"> Mix roster: {count}/9 filled. Classes required: {missing_emojis}"
        else:
            line2 = f"> Mix roster: 9/9 filled."
        return line1 + "\n" + line2

    return line1


def build_match_embed(match, signups):
    colour = discord.Colour.blurple()
    embed  = discord.Embed(title="[PUG]", colour=colour)
    embed.add_field(
        name="🗓 Date & Time",
        value=f"<t:{match['timestamp']}:F> <t:{match['timestamp']}:R>",
        inline=False,
    )
    if match["notes"]:
        embed.add_field(name="📋 Notes", value=match["notes"], inline=False)

    accepted_map  = {c: [] for c in TF2_CLASSES}
    pending_count = 0
    for s in signups:
        if s["status"] == "accepted":
            accepted_map[s["class_name"]].append(f"<@{s['user_id']}>")
        elif s["status"] == "pending":
            pending_count += 1

    roster_lines = [
        f"{CLASS_EMOJI[cls]} **{cls}**: {', '.join(accepted_map[cls]) if accepted_map[cls] else '—'}"
        for cls in TF2_CLASSES
    ]
    embed.add_field(
        name=f"📝 Roster ({sum(len(v) for v in accepted_map.values())}/9 accepted)",
        value="\n".join(roster_lines), inline=False,
    )
    if pending_count:
        embed.add_field(
            name="⏳ Pending", value=f"{pending_count} sign-up(s) awaiting decision",
            inline=False,
        )
    embed.set_footer(text=f"Hosted by {match['created_by_name']}")
    return embed
