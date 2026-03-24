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

FP_DIVISIONS = ["Any", "Steel", "Silver", "Plat"]

OPUG_DIVISIONS = ["Iron/Steel", "Steel", "Silver", "Plat"]

OPUG_CHANNEL_KEY = {
    "Iron/Steel": "iron_steel",
    "Steel":      "steel",
    "Silver":     "silver",
    "Plat":       "plat",
}

OPUG_HEADER = {
    "Iron/Steel": "IRON/STEEL PUG",
    "Steel":      "STEEL PUG",
    "Silver":     "SILVER PUG",
    "Plat":       "PLATINUM PUG",
}


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
    if match["type"] == "fresh_pug":
        division = match["division"] or "Any"
        map_name = match["map_name"] or "—"
        hoster   = f"<@{match['created_by']}>"
        ts       = match["timestamp"]
        ts_str   = f"<t:{ts}:F>" if ts else "—"
        return (
            f"**Fresh PUG** | {division} | {ts_str}\n"
            f"Maps: {map_name} | Hoster: {hoster}"
        )

    if match["type"] == "opug":
        division = match["division"] or "—"
        map_name = match["map_name"] or "—"
        server   = match["server"]   or "—"
        hoster   = f"<@{match['created_by']}>"
        ts       = match["timestamp"]
        ts_str   = f"<t:{ts}:F>" if ts else "—"
        header   = f"**{division} PUG** | {ts_str}\nMap: {map_name} | Server: {server} | Hoster: {hoster}"

        # Check if teams were split (extra kwarg passed via signups being a dict)
        if isinstance(signups, dict) and "red" in signups and "blu" in signups:
            # Concluded with split teams
            def team_lines(team_signups):
                lines = []
                for cls in TF2_CLASSES:
                    players = [f"<@{s['user_id']}>" for s in team_signups if s["class_name"] == cls]
                    if players:
                        lines.append(f"{CLASS_EMOJI[cls]} {', '.join(players)}")
                return "\n".join(lines) if lines else "—"
            subs = [f"<@{s['user_id']}> ({s['class_name']})" for s in signups.get("subs", [])]
            subs_str = ", ".join(subs) if subs else "—"
            return (
                f"{header}\n"
                f"**RED:**\n{team_lines(signups['red'])}\n"
                f"**BLU:**\n{team_lines(signups['blu'])}\n"
                f"Subs: {subs_str}"
            )
        else:
            # Cancelled — no split, just list signups
            signup_lines = []
            for cls in TF2_CLASSES:
                players = [f"<@{s['user_id']}>" for s in signups if s["status"] == "accepted" and s["class_name"] == cls]
                if players:
                    signup_lines.append(f"{CLASS_EMOJI[cls]} {', '.join(players)}")
            signups_str = "\n".join(signup_lines) if signup_lines else "—"
            return f"{header}\nSignups:\n{signups_str}"

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


def build_fresh_pug_message(match, pug_role_id=None):
    ts       = match["timestamp"]
    ts_line  = f"<t:{ts}:F> <t:{ts}:R>" if ts else "tbc"
    hoster   = f"<@{match['created_by']}>"
    division = match["division"] or "Any"
    maps     = match["map_name"] or ""
    role_id  = match["pug_role_id"] or pug_role_id
    pug_ping = f"<@&{role_id}>" if role_id else "@here"
    vc_channel   = "<#1390666665410297869>"
    spec_channel = "<#1354060038657806408>"
    rules_channel = "<#1363089707038281898>"

    map_line = f"> Maps: {maps}" if maps else "> Maps: tbc"
    div_line = f"> Division: {division}"

    lines = [
        f"> Fresh Pug **{ts_line}** {pug_ping}",
        "> ",
        f"> **18 react <:PUG:1367589835874893885> ** will **HOST** the pug.",
        "> ",
        div_line,
        map_line,
        "> ",
        "> **PUGGERS READ THESE RULES! 👀 **",
        "> ",
        "> - Captains will 1v1 mid as med. Winner gets first pick.",
        "> - Captain can play any class they want.",
        f"> - IMPORTANT Captain will pick TEAM based on people in voice chat {vc_channel}. (Limit of 18 - get in early to be the first of 18!)",
        "> - First come first serve basis (for first map)",
        "> - **WHOEVER PLAYS MED GETS +1 PRIORITY FOR NEXT MAP - medics can opt out of +1 if they want**",
        "> - Spectator will have +1 for second map/round **priority pick**",
        f"> - Spectator must wait in {spec_channel} for +1 picks. If spectator joins main lobby, they may not get +1.",
        "> - Do not throw the game / troll etc.",
        "> - Everyone use **MIC DO NOT MUTE**",
        "> - Hoster may intervene to balance teams/classes",
        f"> - **FOLLOW THE RULES (READ IT)** {rules_channel}",
        "> ",
        "> Lets roll 🔥",
    ]
    return "\n".join(lines)


def build_opug_message(match, signups, pug_role_id=None):
    ts       = match["timestamp"]
    ts_line  = f"<t:{ts}:F> <t:{ts}:R>" if ts else "tbc"
    hoster   = f"<@{match['created_by']}>"
    division = match["division"] or "Steel"
    map_name = match["map_name"] or "tbc"
    server   = match["server"] or "tbc"
    role_id  = match["pug_role_id"] or pug_role_id
    pug_ping = f"<@&{role_id}>" if role_id else "@here"
    header   = OPUG_HEADER.get(division, "PUG")

    # Build 2-slot roster per class
    slots = {cls: [] for cls in TF2_CLASSES}
    subs  = []
    for s in signups:
        if s["status"] == "accepted":
            if len(slots[s["class_name"]]) < 2:
                slots[s["class_name"]].append(f"<@{s['user_id']}>")
            else:
                subs.append(f"<@{s['user_id']}>")

    # Pending consolidated per player
    pending_raw = [s for s in signups if s["status"] == "pending"]
    pending_by_player = {}
    for s in pending_raw:
        uid = s["user_id"]
        if uid not in pending_by_player:
            pending_by_player[uid] = {"user_id": uid, "classes": []}
        pending_by_player[uid]["classes"].append(s["class_name"])
    for uid in pending_by_player:
        pending_by_player[uid]["classes"].sort(key=lambda c: TF2_CLASSES.index(c) if c in TF2_CLASSES else 99)

    denied = []
    for s in signups:
        if s["status"] == "denied":
            denied.append((f"<@{s['user_id']}>", s["class_name"]))

    SEP = "> ---------------------------------------------"

    lines = [
        f"> # ✨  {header} {pug_ping}",
        f"> Division : {division}",
        f"> ## Date : {ts_line}",
        f"> Map : {map_name}",
        f"> Server: {server}",
        f"> Hoster: {hoster}",
        "> Mode : Highlander 9v9",
        "> ",
    ]

    # Class slots
    for cls in TF2_CLASSES:
        emoji = CLASS_EMOJI[cls]
        slot1 = slots[cls][0] if len(slots[cls]) > 0 else ""
        slot2 = slots[cls][1] if len(slots[cls]) > 1 else ""
        lines.append(f"> {emoji}  : {slot1}")
        lines.append(f"> {emoji}  : {slot2}")

    lines.append("> ")

    # Subs
    subs_line = " ".join(subs) if subs else ""
    lines.append(f"> Sub : {subs_line}")

    lines.append("> ")

    # Priority line only for Iron/Steel and Steel
    if division in ("Iron/Steel", "Steel"):
        div_upper = division.upper()
        lines.append(f"> **PRIORITISING {div_upper} ROLES, PLAT/SILVER OFFCLASSERS WILL BE HELD**")

    lines.append("> Captain will balance the team.")
    lines.append("> Tag hoster in signup thread and write your preferred classes!")
    lines.append("> Thank you and enjoy the game ! ✌️")

    # Pending and denied sections
    extra = []
    if pending_by_player:
        extra.append("> ⏳ **Pending:**")
        for p in pending_by_player.values():
            classes_str = ", ".join(p["classes"])
            extra.append(f"> - <@{p['user_id']}> — {classes_str}")
    if denied:
        extra.append("> ❌ **Denied:**")
        for ping, cls in denied:
            extra.append(f"> - {ping} — {cls}")
    if extra:
        lines.append("> ")
        lines.extend(extra)

    return "\n".join(lines)


def build_opug_teams_message(match, red_team, blu_team, subs):
    """Posted to the OPUG channel after teams are split."""
    red_ch  = "<#1282804942599356417>"
    blu_ch  = "<#1282805074858344489>"

    def team_lines(team):
        lines = []
        for cls in TF2_CLASSES:
            players = [f"<@{s['user_id']}>" for s in team if s["class_name"] == cls]
            lines.append(f"> {CLASS_EMOJI[cls]} : {' '.join(players) if players else ''}")
        return "\n".join(lines)

    subs_line = " ".join(f"<@{s['user_id']}> ({CLASS_EMOJI[s['class_name']]})" for s in subs) if subs else ""

    lines = [
        f"> **RED** team use {red_ch}",
        team_lines(red_team),
        "> ",
        f"> **BLU** team use {blu_ch}",
        team_lines(blu_team),
    ]
    if subs_line:
        lines.append("> ")
        lines.append(f"> Subs: {subs_line}")
    return "\n".join(lines)


def build_split_view_text(red_team, blu_team):
    """Balancing-chat message showing current split with swap buttons."""
    lines = ["**RED** vs **BLU**", ""]
    for cls in TF2_CLASSES:
        red_p = next((f"<@{s['user_id']}>" for s in red_team if s["class_name"] == cls), "—")
        blu_p = next((f"<@{s['user_id']}>" for s in blu_team if s["class_name"] == cls), "—")
        lines.append(f"{CLASS_EMOJI[cls]}  {red_p}  **|**  {blu_p}")
    lines.append("")
    lines.append("**SWAP TEAMS FOR:**")
    return "\n".join(lines)


def build_ongoing_line(match, guild_id=None, channel_id=None, signups=None):
    ts          = match["timestamp"]
    ts_full     = f"<t:{ts}:F>" if ts else "tbc"
    ts_rel      = f"<t:{ts}:R>" if ts else ""
    cid         = channel_id or match["channel_id"]
    chan_mention = f"<#{cid}>" if cid else ""
    team_name   = match["team_name"] or match["created_by_name"].upper()
    kind        = match["type"].upper()
    division = match["division"] or ""
    if kind == "MIX":
        label = f"**{team_name}** vs Mix | HL | {ts_full}  {ts_rel}"
    elif kind == "FRESH_PUG":
        label = f"**Fresh PUG** | HL | {ts_full}  {ts_rel}"
    elif kind == "OPUG":
        label = f"**{division} PUG** | HL | {ts_full}  {ts_rel}"
    else:
        label = f"**{team_name} PUG** | HL | {ts_full}  {ts_rel}"

    line1 = f"> {label} | {chan_mention}"

    # Build roster status line
    if signups is not None:
        match_type = match["type"] if match else "mix"
        cap        = 18 if match_type == "opug" else 9
        label_str  = "PUG roster" if match_type == "opug" else "Mix roster"

        # For opug: count filled slots (max 2 per class = 18 total)
        # For mix: count filled classes (1 per class = 9 total)
        if match_type == "opug":
            slot_counts = {}
            for s in signups:
                if s["status"] == "accepted":
                    slot_counts[s["class_name"]] = slot_counts.get(s["class_name"], 0) + 1
            count   = sum(min(v, 2) for v in slot_counts.values())
            missing = [cls for cls in TF2_CLASSES if slot_counts.get(cls, 0) < 2]
        else:
            filled_classes = set()
            for s in signups:
                if s["status"] == "accepted" and s["class_name"] not in filled_classes:
                    filled_classes.add(s["class_name"])
            count   = len(filled_classes)
            missing = [cls for cls in TF2_CLASSES if cls not in filled_classes]

        # Unique missing classes
        missing_unique = list(dict.fromkeys(missing))
        if missing_unique:
            missing_emojis = " ".join(CLASS_EMOJI[cls] for cls in missing_unique)
            line2 = f"> {label_str}: {count}/{cap} filled. Classes required: {missing_emojis}"
        else:
            line2 = f"> {label_str}: {count}/{cap} filled."
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
