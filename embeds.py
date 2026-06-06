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

SIXS_DIVISIONS = ["Newcomer", "Div 3", "Div 2", "Div 1"]

SIXS_CLASSES = ["PScout", "FScout", "PSoldier", "Roamer", "Demoman", "Medic"]

SIXS_CLASS_EMOJI = {
    "PScout":   "<:pscout:1448938338764591187>",
    "FScout":   "<:fscout:1448941254678155346>",
    "PSoldier": "<:psoldier:1448938400722718720>",
    "Roamer":   "<:rsoldier:1448941835954294897>",
    "Demoman":  "<:4_Demoman:1353833229731500033>",
    "Medic":    "<:7_Medic:1353833236605960323>",
}

SIXS_OPUG_HEADER = {
    "Newcomer": "NEWCOMER PUG",
    "Div 3":    "DIV 3 PUG",
    "Div 2":    "DIV 2 PUG",
    "Div 1":    "DIV 1 PUG",
}

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
        "> ",
        "> :warning: **__REMINDER__**:",
        "> - Late arrivals, switching class without notifying the host, and no-shows may result in **penalties**",
        ">       - Please review https://discord.com/channels/1245647143704727644/1363089707038281898 before signing up.",
        "> -# Thank you and enjoy the game ! 🫡",
    ]

    return "\n".join(all_lines)


def build_pending_message(match, signups):
    """Separate message showing pending sign-ups."""
    is_sixs    = match["type"] in ("6s_mix", "6s_opug")
    cls_list   = SIXS_CLASSES if is_sixs else TF2_CLASSES

    pending_raw = [s for s in signups if s["status"] == "pending"]
    if not pending_raw:
        return "> ⏳ **Pending:** —"

    # Group by player, tracking signup id alongside class so we can find the first
    pending_by_player = {}
    for s in pending_raw:
        uid = s["user_id"]
        if uid not in pending_by_player:
            pending_by_player[uid] = {"user_id": uid, "username": s["username"], "signups": [], "min_id": s["id"]}
        pending_by_player[uid]["signups"].append(s)
        pending_by_player[uid]["min_id"] = min(pending_by_player[uid]["min_id"], s["id"])

    # Sort each player's signups by signup id (chronological) — first signup = :one: slot
    for uid in pending_by_player:
        pending_by_player[uid]["signups"].sort(key=lambda s: s["id"])

    # Sort players by their earliest signup id
    players = sorted(pending_by_player.values(), key=lambda p: p["min_id"])

    lines = ["> ⏳ **Pending:**"]
    for p in players:
        parts = [s["class_name"] for s in p["signups"]]
        classes_str = ", ".join(parts)
        lines.append(f"> - <@{p['user_id']}> — {classes_str}")

    return "\n".join(lines)


def build_denied_message(match, signups):
    """Separate message showing denied sign-ups."""
    is_sixs   = match["type"] in ("6s_mix", "6s_opug")
    cls_list  = SIXS_CLASSES if is_sixs else TF2_CLASSES
    emoji_map = SIXS_CLASS_EMOJI if is_sixs else CLASS_EMOJI

    denied = [s for s in signups if s["status"] == "denied"]
    if not denied:
        return "> ❌ **Denied:** —"

    # Group by player
    denied_by_player = {}
    for s in denied:
        uid = s["user_id"]
        if uid not in denied_by_player:
            denied_by_player[uid] = {"user_id": uid, "username": s["username"], "classes": []}
        denied_by_player[uid]["classes"].append(s["class_name"])

    for uid in denied_by_player:
        denied_by_player[uid]["classes"].sort(key=lambda c: cls_list.index(c) if c in cls_list else 99)

    lines = ["> ❌ **Denied:**"]
    for p in denied_by_player.values():
        classes_str = ", ".join(p["classes"])
        lines.append(f"> - <@{p['user_id']}> — {classes_str}")

    return "\n".join(lines)


def build_archive_message(match, signups):
    """Concise archive summary posted to the archive channel on match conclusion."""
    ts = match["timestamp"]
    ts_str = f"<t:{ts}:F> <t:{ts}:R>" if ts else "—"

    if match["type"] in ("fresh_pug", "6s_fresh_pug"):
        mode_str = "6s Fresh PUG" if match["type"] == "6s_fresh_pug" else "Fresh PUG"
        division = match["division"] or "Any"
        map_name = match["map_name"] or "—"
        hoster   = f"<@{match['created_by']}>"
        return (
            f"**{mode_str}** | {division} | {ts_str}\n"
            f"Maps: {map_name} | Hoster: {hoster}"
        )

    if match["type"] in ("opug", "6s_opug"):
        is_sixs   = match["type"] == "6s_opug"
        cls_list  = SIXS_CLASSES if is_sixs else TF2_CLASSES
        emoji_map = SIXS_CLASS_EMOJI if is_sixs else CLASS_EMOJI
        division  = match["division"] or "—"
        map_name  = match["map_name"] or "—"
        server    = match["server"]   or "—"
        hoster    = f"<@{match['created_by']}>"
        mode_str  = "6s" if is_sixs else "HL"
        header    = f"**{division} PUG** ({mode_str}) | {ts_str}\nMap: {map_name} | Server: {server} | Hoster: {hoster}"

        def inline_team(team_signups):
            parts = []
            for cls in cls_list:  # class order
                players = [f"<@{s['user_id']}>" for s in team_signups if s["class_name"] == cls]
                for p in players:
                    parts.append(f"{emoji_map[cls]} {p}")
            return " ".join(parts) if parts else "—"

        # Concluded with split teams (dict passed from ConcludeConfirmView)
        if isinstance(signups, dict) and "red" in signups and "blu" in signups:
            subs_parts = []
            for cls in cls_list:  # class order for subs too
                for s in signups.get("subs", []):
                    if s["class_name"] == cls:
                        subs_parts.append(f"{emoji_map.get(cls, cls)} <@{s['user_id']}>")
            subs_str = " ".join(subs_parts) if subs_parts else "—"
            return (
                f"{header}\n"
                f"**RED:** {inline_team(signups['red'])}\n"
                f"**BLU:** {inline_team(signups['blu'])}\n"
                f"Subs: {subs_str}"
            )
        else:
            # Cancelled — flat inline list in class order
            parts = []
            for cls in cls_list:
                players = [f"<@{s['user_id']}>" for s in signups if s["status"] == "accepted" and s["class_name"] == cls]
                for p in players:
                    parts.append(f"{emoji_map[cls]} {p}")
            signups_str = " ".join(parts) if parts else "—"
            return f"{header}\nSignups: {signups_str}"

    # Mix / 6s_mix
    team_name = match["team_name"] or "Mix"
    division  = match["division"]  or "—"
    map_name  = match["map_name"]  or "—"
    server    = match["server"]    or "—"
    hoster    = f"<@{match['created_by']}>"

    is_sixs_mix = match["type"] == "6s_mix"
    cls_list    = SIXS_CLASSES if is_sixs_mix else TF2_CLASSES
    emoji_map   = SIXS_CLASS_EMOJI if is_sixs_mix else CLASS_EMOJI

    # Build host team in class order from stored roster
    host_roster_raw = match["host_roster"] if match["host_roster"] else None
    if host_roster_raw:
        host_entries = [e.strip() for e in host_roster_raw.split("\n")]
        host_team_lines = []
        for cls, entry in zip(cls_list, host_entries):
            if entry:
                host_team_lines.append(f"{emoji_map[cls]} {entry}")
        host_team_str = " ".join(host_team_lines) if host_team_lines else "—"
    else:
        host_team_str = "—"

    # Build mix team roster in class order (first per class = starter, rest = sub)
    seen = set()
    roster_lines = []
    sub_pings    = []
    for cls in cls_list:  # class order
        for s in signups:
            if s["status"] != "accepted" or s["class_name"] != cls:
                continue
            emoji = emoji_map.get(cls, cls)
            if cls not in seen:
                seen.add(cls)
                roster_lines.append(f"{emoji} <@{s['user_id']}>")
            else:
                sub_pings.append(f"<@{s['user_id']}>")

    roster_str = " ".join(roster_lines) if roster_lines else "—"
    subs_str   = " ".join(sub_pings) if sub_pings else "—"

    return (
        f"**{team_name} vs Mix** | {division} | {ts_str}\n"
        f"Map: {map_name} | Server: {server} | Hoster: {hoster}\n"
        f"**{team_name} Team:** {host_team_str}\n"
        f"**Mix Team:** {roster_str}\n"
        f"Subs: {subs_str}"
    )


def build_fresh_pug_signup_list(signups):
    """
    Builds the numbered signup list message for fresh pugs.
    signups: list of accepted signups ordered by accepted_at ASC (signup order).
    Always shows at least '1.' even when empty.
    """
    # Filter to accepted, in order
    players = [s for s in signups if s["status"] == "accepted"]
    lines = ["> # SIGN UPS"]
    if not players:
        lines.append("> 1.")
    else:
        for i, s in enumerate(players, 1):
            lines.append(f"> {i}. <@{s['user_id']}>")
    return "\n".join(lines)


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



def build_6s_fresh_pug_message(match, pug_role_id=None):
    ts       = match["timestamp"]
    ts_line  = f"<t:{ts}:F> <t:{ts}:R>" if ts else "tbc"
    division = match["division"] or "Newcomer"
    maps     = match["map_name"] or ""
    role_id  = match["pug_role_id"] or pug_role_id
    pug_ping = f"<@&{role_id}>" if role_id else "@here"
    vc_channel    = "<#1386223548137345135>"
    spec_channel  = "<#1449606731574411395>"
    rules_channel = "<#1363089707038281898>"
    map_line = f"> Maps: {maps}" if maps else "> Maps: tbc"
    div_line = f"> Division: {division}"
    lines = [
        f"> Fresh Pug **6v6** **{ts_line}** {pug_ping}",
        "> ",
        "> **12 react <:PUG:1367589835874893885> ** will **HOST** the pug.",
        "> ",
        div_line,
        map_line,
        "> ",
        "> **PUGGERS READ THESE RULES! 👀 **",
        "> ",
        "> - Captains will 1v1 mid as med. Winner gets first pick.",
        "> - Captain can play any class they want.",
        f"> - IMPORTANT Captain will pick TEAM based on people in voice chat {vc_channel} (Limit of 12 - get in early to be the first of 12!)",
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


def build_6s_opug_message(match, signups, pug_role_id=None):
    ts       = match["timestamp"]
    ts_line  = f"<t:{ts}:F> <t:{ts}:R>" if ts else "tbc"
    hoster   = f"<@{match['created_by']}>"
    division = match["division"] or "Newcomer"
    map_name = match["map_name"] or "tbc"
    server   = match["server"] or "tbc"
    role_id  = match["pug_role_id"] or pug_role_id
    pug_ping = f"<@&{role_id}>" if role_id else "@here"
    header   = SIXS_OPUG_HEADER.get(division, "PUG")

    slots = {cls: [] for cls in SIXS_CLASSES}
    subs  = []
    for s in signups:
        if s["status"] == "accepted":
            if len(slots[s["class_name"]]) < 2:
                slots[s["class_name"]].append(f"<@{s['user_id']}>")
            else:
                subs.append(f"<@{s['user_id']}>")

    pending_raw = [s for s in signups if s["status"] == "pending"]
    pending_by_player = {}
    for s in pending_raw:
        uid = s["user_id"]
        if uid not in pending_by_player:
            pending_by_player[uid] = {"user_id": uid, "classes": []}
        pending_by_player[uid]["classes"].append(s["class_name"])
    for uid in pending_by_player:
        pending_by_player[uid]["classes"].sort(key=lambda c: SIXS_CLASSES.index(c) if c in SIXS_CLASSES else 99)

    denied = [(f"<@{s['user_id']}>", s["class_name"]) for s in signups if s["status"] == "denied"]

    SEP = "> ---------------------------------------------"
    lines = [
        f"> ## ✨  {header} {pug_ping}",
        "> ",
        f"> Division : {division}",
        f"> ## Date : {ts_line}",
        f"> Map : {map_name}",
        f"> Server: {server}",
        f"> Hoster: {hoster}",
        "> Mode : 6v6",
        "> ",
    ]
    for cls in SIXS_CLASSES:
        emoji = SIXS_CLASS_EMOJI[cls]
        slot1 = slots[cls][0] if len(slots[cls]) > 0 else ""
        slot2 = slots[cls][1] if len(slots[cls]) > 1 else ""
        lines.append(f"> {emoji} : {slot1}")
        lines.append(f"> {emoji} : {slot2}")
    lines.append("> ")
    subs_line = " ".join(subs) if subs else ""
    lines.append(f"> Sub : {subs_line}")
    lines.append("> ")
    lines.append("> Captain will balance the team.")
    lines.append("> Tag hoster in signup **thread** and write your preferred classes! **Ensure you specify roamer or pocket**")
    lines.append("> Thank you and enjoy the game ! ✌️")

    return "\n".join(lines)


def build_6s_mix_message(match, signups, pug_role_id=None):
    mix_starters = {c: None for c in SIXS_CLASSES}
    sub_by_player = {}
    denied = []

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

    pending_raw = [s for s in signups if s["status"] == "pending"]
    pending_by_player = {}
    for s in pending_raw:
        uid = s["user_id"]
        if uid not in pending_by_player:
            pending_by_player[uid] = {"user_id": uid, "classes": []}
        pending_by_player[uid]["classes"].append(s["class_name"])
    for uid in pending_by_player:
        pending_by_player[uid]["classes"].sort(key=lambda c: SIXS_CLASSES.index(c) if c in SIXS_CLASSES else 99)

    for uid in sub_by_player:
        sub_by_player[uid]["classes"].sort(key=lambda c: SIXS_CLASSES.index(c) if c in SIXS_CLASSES else 99)

    team_name = match["team_name"] or "Team"
    division  = match["division"] or "tbc"
    map_name  = match["map_name"] or "tbc"
    server    = match["server"] or "tbc"
    hoster    = f"<@{match['created_by']}>"
    role_id   = match["pug_role_id"] or pug_role_id
    pug_ping  = f"<@&{role_id}>" if role_id else "@here"
    ts        = match["timestamp"]
    ts_line   = f"<t:{ts}:F> <t:{ts}:R>" if ts else "tbc"
    SEP = "> ---------------------------------------------"

    def class_lines(cmap):
        return "\n".join(
            f"> {SIXS_CLASS_EMOJI[cls]}: {cmap[cls] or ''}"
            for cls in SIXS_CLASSES
        )

    subs_entries = []
    for p in sub_by_player.values():
        p["classes"].sort(key=lambda c: SIXS_CLASSES.index(c) if c in SIXS_CLASSES else 99)
        class_emojis = ", ".join(SIXS_CLASS_EMOJI[c] for c in p["classes"])
        subs_entries.append(f"<@{p['user_id']}> - {class_emojis}")
    subs_line = "\n> ".join(subs_entries) if subs_entries else ""

    extra_lines = []

    host_roster_raw = match["host_roster"] if match["host_roster"] else None
    host_entries = [e.strip() for e in host_roster_raw.split("\n")] if host_roster_raw else []
    while len(host_entries) < 6:
        host_entries.append("")
    host_map = {cls: host_entries[i] for i, cls in enumerate(SIXS_CLASSES)}

    all_lines = [
        f"> ## ✨ {team_name} vs Mix team {pug_ping}",
        "> ",
        f"> ## - Date : {ts_line}",
        f"> - Division : {division}",
        f"> - Map : **{map_name}**",
        f"> - Server: {server}",
        "> - Mode : **6v6**",
        f"> - Hoster: {hoster}",
        SEP,
        f"> {team_name} Team",
        class_lines(host_map),
        SEP,
        "> Mix Team",
        class_lines(mix_starters),
        "> ",
        f"> Subs : {subs_line}",
        "> Tag the hoster to be accepted on mix team. **Ensure you specify roamer or pocket**",
        SEP,
    ] + extra_lines + [
        "> ",
        "> :warning: **__REMINDER__**:",
        "> - Late arrivals, switching class without notifying the host, and no-shows may result in **penalties**",
        ">       - Please review https://discord.com/channels/1245647143704727644/1363089707038281898 before signing up.",
        "> -# Thank you and enjoy the game ! 🫡",
    ]
    return "\n".join(all_lines)


def build_6s_opug_teams_message(match, red_team, blu_team, subs):
    red_ch = "<#1282804942599356417>"
    blu_ch = "<#1282805074858344489>"

    def team_lines(team):
        lines = []
        for cls in SIXS_CLASSES:
            players = [f"<@{s['user_id']}>" for s in team if s["class_name"] == cls]
            lines.append(f"> {SIXS_CLASS_EMOJI[cls]} : {' '.join(players) if players else ''}")
        return "\n".join(lines)

    subs_line = " ".join(f"<@{s['user_id']}> ({SIXS_CLASS_EMOJI.get(s['class_name'], s['class_name'])})" for s in subs) if subs else ""
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


def build_6s_split_view_text(red_team, blu_team):
    lines = ["**RED** vs **BLU**", ""]
    for cls in SIXS_CLASSES:
        red_p = next((f"<@{s['user_id']}>" for s in red_team if s["class_name"] == cls), "—")
        blu_p = next((f"<@{s['user_id']}>" for s in blu_team if s["class_name"] == cls), "—")
        lines.append(f"{SIXS_CLASS_EMOJI[cls]}  {red_p}  **|**  {blu_p}")
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
    division    = match["division"] or ""
    server      = match["server"] or ""
    server_part = f" | {server}" if server else ""

    if kind == "MIX":
        label = f"**{team_name}** vs Mix | HL{server_part} | {ts_full}  {ts_rel}"
    elif kind == "FRESH_PUG":
        label = f"**Fresh PUG** | HL | {ts_full}  {ts_rel}"
    elif kind == "OPUG":
        label = f"**{division} PUG** | HL{server_part} | {ts_full}  {ts_rel}"
    elif kind == "6S_MIX":
        label = f"**{team_name}** vs Mix | 6s{server_part} | {ts_full}  {ts_rel}"
    elif kind == "6S_OPUG":
        label = f"**{division} PUG** | 6s{server_part} | {ts_full}  {ts_rel}"
    elif kind == "6S_FRESH_PUG":
        label = f"**Fresh PUG 6v6** | {ts_full}  {ts_rel}"
    else:
        label = f"**{team_name} PUG** | HL{server_part} | {ts_full}  {ts_rel}"

    line1 = f"> {label} | {chan_mention}"

    # Build roster status line
    if signups is not None:
        match_type = match["type"] if match else "mix"
        cap        = 18 if match_type == "opug" else 12 if match_type == "6s_opug" else 12 if match_type == "6s_fresh_pug" else 6 if match_type == "6s_mix" else 9
        label_str  = "PUG roster" if match_type in ("opug", "6s_opug") else "Mix roster"
        classes    = SIXS_CLASSES if match_type in ("6s_mix", "6s_opug") else TF2_CLASSES

        if match_type in ("opug", "6s_opug"):  # opug=18 slots, 6s_opug=12 slots
            slot_counts = {}
            for s in signups:
                if s["status"] == "accepted":
                    slot_counts[s["class_name"]] = slot_counts.get(s["class_name"], 0) + 1
            count   = sum(min(v, 2) for v in slot_counts.values())
            missing = [cls for cls in classes if slot_counts.get(cls, 0) < 2]
        else:
            filled_classes = set()
            for s in signups:
                if s["status"] == "accepted" and s["class_name"] not in filled_classes:
                    filled_classes.add(s["class_name"])
            count   = len(filled_classes)
            missing = [cls for cls in classes if cls not in filled_classes]

        # Unique missing classes
        all_emojis = {**CLASS_EMOJI, **SIXS_CLASS_EMOJI}
        missing_unique = list(dict.fromkeys(missing))
        if missing_unique:
            missing_emojis = " ".join(all_emojis.get(cls, cls) for cls in missing_unique)
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
