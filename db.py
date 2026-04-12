import aiosqlite
import os
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "matches.db")

CREATE_MATCHES = """
CREATE TABLE IF NOT EXISTS matches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    type             TEXT    NOT NULL,
    team_name        TEXT,
    timestamp        INTEGER NOT NULL,
    notes            TEXT,
    division         TEXT,
    map_name         TEXT,
    server           TEXT,
    pug_role_id      TEXT,
    host_roster      TEXT,
    ongoing_msg_id   INTEGER,
    message_id       INTEGER,
    channel_id       INTEGER,
    thread_id        INTEGER,
    created_by       INTEGER NOT NULL,
    created_by_name  TEXT    NOT NULL,
    ended            INTEGER DEFAULT 0,
    cancelled        INTEGER DEFAULT 0,
    cancel_msg_id    INTEGER,
    cancel_delete_at INTEGER,
    conclude_msg_id  INTEGER,
    conclude_delete_at INTEGER,
    reminded_1h      INTEGER DEFAULT 0,
    reminded_8h      INTEGER DEFAULT 0
);
"""

CREATE_SIGNUPS = """
CREATE TABLE IF NOT EXISTS signups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id    INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL,
    username    TEXT    NOT NULL,
    class_name  TEXT    NOT NULL,
    team        TEXT    NOT NULL DEFAULT 'mix',
    status      TEXT    DEFAULT 'pending',
    accepted_at INTEGER
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(CREATE_MATCHES)
        await db.execute(CREATE_SIGNUPS)
        for col, definition in [
            ("cancelled",        "INTEGER DEFAULT 0"),
            ("cancel_msg_id",    "INTEGER"),
            ("cancel_delete_at", "INTEGER"),
            ("division",         "TEXT"),
            ("map_name",         "TEXT"),
            ("server",           "TEXT"),
            ("pug_role_id",      "TEXT"),
            ("ongoing_msg_id",   "INTEGER"),
            ("thread_id",        "INTEGER"),
            ("reminded_1h",      "INTEGER DEFAULT 0"),
            ("reminded_8h",      "INTEGER DEFAULT 0"),
            ("conclude_msg_id",  "INTEGER"),
            ("conclude_delete_at", "INTEGER"),
            ("host_roster",        "TEXT"),
            ("teams_posted",       "INTEGER DEFAULT 0"),
            ("pending_msg_id",     "INTEGER"),
            ("denied_msg_id",      "INTEGER"),
            ("ping_msg_id",        "INTEGER"),
            ("signup_list_msg_id", "INTEGER"),
        ]:
            try:
                await db.execute(f"ALTER TABLE matches ADD COLUMN {col} {definition}")
            except Exception:
                pass
        try:
            await db.execute("ALTER TABLE signups ADD COLUMN team TEXT NOT NULL DEFAULT 'mix'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE signups ADD COLUMN accepted_at INTEGER")
        except Exception:
            pass
        await db.commit()

async def create_match(type_, timestamp, created_by, created_by_name,
                       team_name=None, notes=None, division=None,
                       map_name=None, server=None, pug_role_id=None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO matches
               (type, team_name, timestamp, notes, division, map_name,
                server, pug_role_id, created_by, created_by_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (type_, team_name, timestamp, notes, division, map_name,
             server, pug_role_id, created_by, created_by_name)
        )
        await db.commit()
        return cur.lastrowid

async def get_match(match_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)) as cur:
            return await cur.fetchone()

async def get_match_by_channel(channel_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM matches WHERE channel_id=? AND ended=0", (channel_id,)
        ) as cur:
            return await cur.fetchone()

async def update_match_fields(match_id, **fields):
    allowed = {"team_name", "timestamp", "division", "map_name", "server", "notes", "host_roster"}
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if not filtered:
        return
    set_clause = ", ".join(f"{k}=?" for k in filtered)
    values     = list(filtered.values()) + [match_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE matches SET {set_clause} WHERE id=?", values)
        await db.commit()

async def set_message_id(match_id, message_id, channel_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE matches SET message_id=?, channel_id=? WHERE id=?",
            (message_id, channel_id, match_id)
        )
        await db.commit()

async def set_thread_id(match_id, thread_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE matches SET thread_id=? WHERE id=?", (thread_id, match_id))
        await db.commit()

async def set_ongoing_msg_id(match_id, ongoing_msg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE matches SET ongoing_msg_id=? WHERE id=?", (ongoing_msg_id, match_id)
        )
        await db.commit()

async def set_teams_posted(match_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE matches SET teams_posted=1 WHERE id=?", (match_id,))
        await db.commit()

async def set_pending_msg_id(match_id, msg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE matches SET pending_msg_id=? WHERE id=?", (msg_id, match_id))
        await db.commit()

async def set_denied_msg_id(match_id, msg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE matches SET denied_msg_id=? WHERE id=?", (msg_id, match_id))
        await db.commit()

async def set_ping_msg_id(match_id, msg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE matches SET ping_msg_id=? WHERE id=?", (msg_id, match_id))
        await db.commit()

async def set_signup_list_msg_id(match_id, msg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE matches SET signup_list_msg_id=? WHERE id=?", (msg_id, match_id))
        await db.commit()

async def remove_pending_slots_for_user(match_id, user_id, keep_class):
    """
    When a player is accepted on the main roster for keep_class,
    soft-delete their pending signups on other classes by setting status='cancelled'.
    These can be restored later if the accept is undone.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE signups SET status='cancelled'
               WHERE match_id=? AND user_id=? AND class_name!=? AND status='pending'""",
            (match_id, user_id, keep_class)
        )
        await db.commit()


async def set_accepted_at(signup_id, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE signups SET accepted_at=? WHERE id=?", (value, signup_id))
        await db.commit()


async def batch_set_accepted_at(updates):
    """
    Update accepted_at for multiple signups in a single DB transaction.
    updates: list of (signup_id, value) tuples.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "UPDATE signups SET accepted_at=? WHERE id=?",
            [(value, signup_id) for signup_id, value in updates]
        )
        await db.commit()


async def move_accepted_to_pending(signup_id):
    """Move a single accepted signup back to pending, clearing accepted_at."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE signups SET status='pending', accepted_at=NULL WHERE id=?",
            (signup_id,)
        )
        await db.commit()


async def restore_cancelled_to_pending(match_id, user_id):
    """Restore all cancelled signups for a user in a match back to pending."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE signups SET status='pending'
               WHERE match_id=? AND user_id=? AND status='cancelled'""",
            (match_id, user_id)
        )
        await db.commit()


async def get_accepted_signups_for_class_with_user(match_id, class_name, user_id):
    """Get the accepted signup row for a specific user+class combination."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM signups
               WHERE match_id=? AND class_name=? AND user_id=? AND status='accepted'""",
            (match_id, class_name, user_id)
        ) as cur:
            return await cur.fetchone()


async def end_match(match_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE matches SET ended=1 WHERE id=?", (match_id,))
        await db.commit()

async def cancel_match(match_id, cancel_msg_id):
    delete_at = int(time.time()) + 86400
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE matches SET ended=1, cancelled=1, cancel_msg_id=?, cancel_delete_at=? WHERE id=?",
            (cancel_msg_id, delete_at, match_id)
        )
        await db.commit()

async def mark_reminded(match_id, reminder_type):
    col = "reminded_1h" if reminder_type == "1h" else "reminded_8h"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE matches SET {col}=1 WHERE id=?", (match_id,))
        await db.commit()

async def get_expired_cancel_notices():
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM matches
               WHERE cancelled=1 AND cancel_msg_id IS NOT NULL
                 AND cancel_delete_at IS NOT NULL AND cancel_delete_at < ?""",
            (now,)
        ) as cur:
            return await cur.fetchall()

async def clear_cancel_msg(match_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE matches SET cancel_msg_id=NULL, cancel_delete_at=NULL WHERE id=?",
            (match_id,)
        )
        await db.commit()

async def get_all_active_matches():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM matches WHERE ended=0 AND message_id IS NOT NULL ORDER BY timestamp ASC"
        ) as cur:
            return await cur.fetchall()

async def get_active_channel_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT channel_id FROM matches WHERE ended=0 AND message_id IS NOT NULL"
        ) as cur:
            rows = await cur.fetchall()
            return {row[0] for row in rows}

async def get_matches_needing_1h_reminder():
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM matches
               WHERE ended=0 AND reminded_1h=0 AND message_id IS NOT NULL
                 AND timestamp > ? AND timestamp <= ?""",
            (now, now + 3600)
        ) as cur:
            return await cur.fetchall()

async def get_matches_needing_8h_reminder():
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM matches
               WHERE ended=0 AND reminded_8h=0 AND message_id IS NOT NULL
                 AND timestamp <= ?""",
            (now - 8 * 3600,)
        ) as cur:
            return await cur.fetchall()

async def set_conclude_msg(match_id, msg_id, channel_id):
    delete_at = int(time.time()) + 86400
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE matches SET conclude_msg_id=?, conclude_delete_at=? WHERE id=?",
            (msg_id, delete_at, match_id)
        )
        await db.commit()

async def clear_conclude_msg(match_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE matches SET conclude_msg_id=NULL, conclude_delete_at=NULL WHERE id=?",
            (match_id,)
        )
        await db.commit()

async def get_expired_conclude_notices():
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM matches
               WHERE ended=1 AND conclude_msg_id IS NOT NULL
                 AND conclude_delete_at IS NOT NULL AND conclude_delete_at < ?""",
            (now,)
        ) as cur:
            return await cur.fetchall()

async def get_conclude_msg_for_channel(channel_id):
    """Get the active conclusion notice in a channel, if any."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM matches
               WHERE channel_id=? AND conclude_msg_id IS NOT NULL
               ORDER BY id DESC LIMIT 1""",
            (channel_id,)
        ) as cur:
            return await cur.fetchone()

async def add_signup(match_id, user_id, username, class_name, team="mix"):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Block only if already signed up (non-denied, non-cancelled) for this exact class.
        # Cancelled rows are invisible tombstones — delete them and allow re-signup.
        async with db.execute(
            "SELECT id, status FROM signups WHERE match_id=? AND user_id=? AND class_name=?",
            (match_id, user_id, class_name)
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            if existing["status"] in ("pending", "accepted"):
                return None  # Already actively signed up
            if existing["status"] == "cancelled":
                # Clean up the stale cancelled row before re-inserting
                await db.execute("DELETE FROM signups WHERE id=?", (existing["id"],))
            # 'denied' rows: fall through and allow re-signup
        cur = await db.execute(
            "INSERT INTO signups (match_id, user_id, username, class_name, team) VALUES (?, ?, ?, ?, ?)",
            (match_id, user_id, username, class_name, team)
        )
        await db.commit()
        return cur.lastrowid

async def update_signup_status(signup_id, status):
    async with aiosqlite.connect(DB_PATH) as db:
        if status == "accepted":
            await db.execute(
                "UPDATE signups SET status=?, accepted_at=? WHERE id=?",
                (status, int(time.time()), signup_id)
            )
        else:
            await db.execute("UPDATE signups SET status=? WHERE id=?", (status, signup_id))
        await db.commit()

async def get_signups_for_match(match_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Single atomic query: accepted rows ordered by accepted_at ASC (LP priority),
        # pending/denied rows ordered by id ASC (chronological signup order).
        # Cancelled rows are excluded — they are invisible tombstones for restore purposes.
        async with db.execute(
            """SELECT *,
                  CASE WHEN status='accepted' THEN 0 ELSE 1 END AS sort_group
               FROM signups
               WHERE match_id=? AND status != 'cancelled'
               ORDER BY
                  sort_group ASC,
                  CASE WHEN status='accepted' THEN accepted_at END ASC NULLS LAST,
                  CASE WHEN status='accepted' THEN id END ASC,
                  CASE WHEN status!='accepted' THEN id END ASC""",
            (match_id,)
        ) as cur:
            return await cur.fetchall()

async def get_pending_signups(match_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM signups WHERE match_id=? AND status='pending' ORDER BY id ASC",
            (match_id,)
        ) as cur:
            return await cur.fetchall()

async def get_accepted_signups(match_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM signups WHERE match_id=? AND status='accepted' ORDER BY accepted_at ASC NULLS LAST, id ASC",
            (match_id,)
        ) as cur:
            return await cur.fetchall()

async def count_accepted_for_class(match_id, class_name):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM signups WHERE match_id=? AND class_name=? AND status='accepted'",
            (match_id, class_name)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def count_accepted(match_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM signups WHERE match_id=? AND status='accepted'",
            (match_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def get_next_accepted_for_class(match_id, class_name, exclude_user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM signups
               WHERE match_id=? AND class_name=? AND status='accepted' AND user_id != ?
               ORDER BY accepted_at ASC NULLS LAST, id ASC LIMIT 1""",
            (match_id, class_name, exclude_user_id)
        ) as cur:
            return await cur.fetchone()

async def get_earliest_pending_for_class(match_id, class_name):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM signups
               WHERE match_id=? AND class_name=? AND status='pending'
               ORDER BY id ASC LIMIT 1""",
            (match_id, class_name)
        ) as cur:
            return await cur.fetchone()

async def get_signup_by_id(signup_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM signups WHERE id=?", (signup_id,)) as cur:
            return await cur.fetchone()

async def get_signup_by_user(match_id, user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM signups WHERE match_id=? AND user_id=?", (match_id, user_id)
        ) as cur:
            return await cur.fetchone()

async def remove_signup(match_id, user_id, class_name=None):
    """Remove signup(s) for a user. If class_name given, removes only that class."""
    async with aiosqlite.connect(DB_PATH) as db:
        if class_name:
            await db.execute(
                "DELETE FROM signups WHERE match_id=? AND user_id=? AND class_name=?",
                (match_id, user_id, class_name)
            )
        else:
            await db.execute(
                "DELETE FROM signups WHERE match_id=? AND user_id=?", (match_id, user_id)
            )
        await db.commit()

async def get_non_denied_signups_for_user(match_id, user_id):
    """All active (pending or accepted) signups for a user in a match. Excludes denied and cancelled."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM signups
               WHERE match_id=? AND user_id=? AND status NOT IN ('denied', 'cancelled')
               ORDER BY id ASC""",
            (match_id, user_id)
        ) as cur:
            return await cur.fetchall()

async def get_accepted_matches_for_user(user_id, exclude_match_id=None, reference_timestamp=None):
    """
    Returns active matches where this user is accepted that clash with reference_timestamp.
    A clash means the other match falls within [ref_ts - 5400, ref_ts + 5400] (1.5h window each side).
    If reference_timestamp is None, returns all accepted active matches.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if reference_timestamp is not None:
            window_start = reference_timestamp - 5400
            window_end   = reference_timestamp + 5400
            async with db.execute(
                """SELECT m.* FROM matches m
                   JOIN signups s ON s.match_id = m.id
                   WHERE s.user_id = ? AND s.status = 'accepted'
                     AND m.ended = 0
                     AND (? IS NULL OR m.id != ?)
                     AND m.timestamp > ? AND m.timestamp < ?""",
                (user_id, exclude_match_id, exclude_match_id, window_start, window_end)
            ) as cur:
                return await cur.fetchall()
        else:
            async with db.execute(
                """SELECT m.* FROM matches m
                   JOIN signups s ON s.match_id = m.id
                   WHERE s.user_id = ? AND s.status = 'accepted'
                     AND m.ended = 0
                     AND (? IS NULL OR m.id != ?)""",
                (user_id, exclude_match_id, exclude_match_id)
            ) as cur:
                return await cur.fetchall()

async def get_signup_by_user_non_denied(match_id, user_id):
    """Returns a non-denied signup for this user in this match, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM signups
               WHERE match_id=? AND user_id=? AND status != 'denied'""",
            (match_id, user_id)
        ) as cur:
            return await cur.fetchone()

async def get_signup_by_user_and_class(match_id, user_id, class_name):
    """Returns the signup for this user+class combination, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM signups WHERE match_id=? AND user_id=? AND class_name=?",
            (match_id, user_id, class_name)
        ) as cur:
            return await cur.fetchone()

async def swap_signup_order(main_signup, sub_signup):
    """
    Swap two signups in the roster order by deleting both and re-inserting
    sub first (gets lower auto-increment id = higher priority), main second.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM signups WHERE id IN (?, ?)", (main_signup["id"], sub_signup["id"]))
        await db.execute(
            "INSERT INTO signups (match_id, user_id, username, class_name, team, status) VALUES (?,?,?,?,?,?)",
            (sub_signup["match_id"], sub_signup["user_id"], sub_signup["username"],
             sub_signup["class_name"], sub_signup["team"], sub_signup["status"])
        )
        await db.execute(
            "INSERT INTO signups (match_id, user_id, username, class_name, team, status) VALUES (?,?,?,?,?,?)",
            (main_signup["match_id"], main_signup["user_id"], main_signup["username"],
             main_signup["class_name"], main_signup["team"], main_signup["status"])
        )
        await db.commit()


async def count_unique_signedup_players(match_id):
    """Count distinct players with any non-denied signup in this match."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM signups WHERE match_id=? AND status != 'denied'",
            (match_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def get_accepted_signups_for_class(match_id, class_name):
    """All accepted signups for a specific class, in priority order (non-LP by accepted_at first)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM signups
               WHERE match_id=? AND class_name=? AND status='accepted'
               ORDER BY accepted_at ASC NULLS LAST, id ASC""",
            (match_id, class_name)
        ) as cur:
            return await cur.fetchall()

async def remove_sub_slots_for_user(match_id, user_id, keep_class):
    """
    When a player is promoted to main roster on keep_class,
    soft-delete their other accepted sub signups by setting status='cancelled'.
    These can be restored later if the accept is undone.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE signups SET status='cancelled'
               WHERE match_id=? AND user_id=? AND class_name!=? AND status='accepted'""",
            (match_id, user_id, keep_class)
        )
        await db.commit()

async def get_match_by_id_for_user(user_id, match_id):
    """Get match only if user is accepted in it."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT m.* FROM matches m
               JOIN signups s ON s.match_id=m.id
               WHERE m.id=? AND s.user_id=? AND s.status='accepted'""",
            (match_id, user_id)
        ) as cur:
            return await cur.fetchone()

async def get_accepted_signups_for_class_ordered(match_id, class_name):
    """
    Returns accepted signups for a class in priority order:
    non-LP first (by accepted_at), then LP (by accepted_at).
    LP sorting is done in views.py where we have guild context.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM signups
               WHERE match_id=? AND class_name=? AND status='accepted'
               ORDER BY accepted_at ASC NULLS LAST, id ASC""",
            (match_id, class_name)
        ) as cur:
            return await cur.fetchall()

async def get_active_fresh_pug():
    """Returns the active fresh pug match if one exists."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM matches WHERE type='fresh_pug' AND ended=0 LIMIT 1"
        ) as cur:
            return await cur.fetchone()

async def save_team_split(match_id, red_user_ids, blu_user_ids):
    """Store team assignments as comma-separated user IDs."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Store in a simple key-value using the notes column won't work —
        # add team_split column if not exists
        try:
            await db.execute("ALTER TABLE matches ADD COLUMN team_split TEXT")
            await db.commit()
        except Exception:
            pass
        import json
        split_data = json.dumps({"red": red_user_ids, "blu": blu_user_ids})
        await db.execute("UPDATE matches SET team_split=? WHERE id=?", (split_data, match_id))
        await db.commit()

async def get_team_split(match_id):
    """Get stored team split, returns {"red": [...], "blu": [...]} or None."""
    import json
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute("SELECT team_split FROM matches WHERE id=?", (match_id,)) as cur:
                row = await cur.fetchone()
                if row and row["team_split"]:
                    return json.loads(row["team_split"])
        except Exception:
            pass
    return None

async def get_active_6s_fresh_pug():
    """Returns the active 6s fresh pug match if one exists."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM matches WHERE type='6s_fresh_pug' AND ended=0 LIMIT 1"
        ) as cur:
            return await cur.fetchone()
