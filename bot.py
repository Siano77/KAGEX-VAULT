"""KAGEX VAULT Telegram bot. Configure secrets through environment variables."""
from __future__ import annotations

import html
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kagex_vault")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DB_PATH = os.getenv("DATABASE_PATH", "data/kagex_vault.sqlite3")
REFERRAL_POINTS = int(os.getenv("REFERRAL_POINTS", "4"))
SUPPORT_URL = os.getenv("SUPPORT_URL", "").strip()

SMALL_CAPS = str.maketrans({
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ", "f": "ꜰ", "g": "ɢ",
    "h": "ʜ", "i": "ɪ", "j": "ᴊ", "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ",
    "o": "ᴏ", "p": "ᴘ", "q": "ǫ", "r": "ʀ", "s": "ꜱ", "t": "ᴛ", "u": "ᴜ",
    "v": "ᴠ", "w": "ᴡ", "x": "ˣ", "y": "ʏ", "z": "ᴢ",
    **{chr(code).upper(): value for code, value in zip(range(ord("a"), ord("z") + 1),
        "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡˣʏᴢ")},
})


def small_caps(text: str) -> str:
    return text.translate(SMALL_CAPS)


def styled_html(text: str) -> str:
    """Style visible copy but leave HTML tags and <code> snippets intact."""
    pieces = re.findall(r"<[^>]*>|&(?:#[0-9]+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);|[^<&]+|.", text)
    result = []
    in_code = False
    for piece in pieces:
        if piece.lower().startswith("<code"):
            in_code = True
            result.append(piece)
        elif piece.lower() == "</code>":
            in_code = False
            result.append(piece)
        elif piece.startswith("<") or piece.startswith("&") or in_code:
            result.append(piece)
        else:
            result.append(small_caps(piece))
    return "".join(result)


MENU = ReplyKeyboardMarkup(
    [["🏠 ʜᴏᴍᴇ", "👤 ᴘʀᴏꜰɪʟᴇ"], ["🎁 ᴛᴏᴏʟs", "🎟 ʀᴇᴅᴇᴇᴍ"],
     ["👥 ʀᴇꜰᴇʀʀᴀʟs", "📣 sᴜᴘᴘᴏʀᴛ"], ["↩️ ʀᴇᴛᴜʀɴ"]],
    resize_keyboard=True, input_field_placeholder="ᴄʜᴏᴏsᴇ ᴀɴ ᴏᴘᴛɪᴏɴ")

VIEWS = {
    "start": ("Welcome to KAGEX VAULT. Earn points by inviting friends, then redeem them for tools and files.", "start"),
    "profile": ("👤 <b>Your profile</b>\n\nPoints: <b>{points}</b>\nSuccessful referrals: <b>{referrals}</b>", "profile"),
    "tools": ("🎁 <b>Vault</b>\nChoose a reward below to see its point cost.", "tools"),
    "redeem": ("🎟 <b>Redeem a code</b>\n\nSend <code>/redeem YOUR_CODE</code> to claim points. Each code can be used once per account and may expire or reach its use limit.", "redeem"),
    "referrals": ("👥 <b>Referrals</b>\nInvite a new user with your personal link. You receive <b>{referral_points} points</b> after they start the bot and complete any required channel joins.\n\nYour link:\n<code>{link}</code>\n\nOnly a new Telegram account starting through your link is counted; self-referrals and repeat starts do not earn points.", "referrals"),
    "help": ("KAGEX VAULT help\n\nUse the buttons to check your profile, browse rewards, redeem a code, or get your referral link. To claim a reward, open Tools and select it.\n\nCommands: <code>/start</code>, <code>/profile</code>, <code>/tools</code>, <code>/referrals</code>, <code>/redeem CODE</code>, <code>/help</code>", "help"),
}


def db() -> sqlite3.Connection:
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT NOT NULL DEFAULT '',
            points INTEGER NOT NULL DEFAULT 0 CHECK(points >= 0), referrer_id INTEGER,
            referral_paid INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            points INTEGER NOT NULL CHECK(points >= 0), media_type TEXT NOT NULL,
            file_id TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS redeem_codes (
            code TEXT PRIMARY KEY, points INTEGER NOT NULL CHECK(points > 0), expires_at TEXT,
            max_uses INTEGER, uses INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS code_redemptions (
            code TEXT NOT NULL REFERENCES redeem_codes(code) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            redeemed_at TEXT NOT NULL, PRIMARY KEY(code, user_id)
        );
        CREATE TABLE IF NOT EXISTS force_channels (chat_ref TEXT PRIMARY KEY, join_url TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS command_media (
            command TEXT PRIMARY KEY, media_type TEXT NOT NULL, file_id TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_items_active ON items(active);
        """)
        item_columns = {row["name"] for row in c.execute("PRAGMA table_info(items)").fetchall()}
        if "reward_text" not in item_columns:
            c.execute("ALTER TABLE items ADD COLUMN reward_text TEXT NOT NULL DEFAULT ''")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def upsert_user(tg_user: Any, referrer_id: int | None = None) -> tuple[bool, int | None]:
    with db() as c:
        old = c.execute("SELECT user_id, referrer_id FROM users WHERE user_id=?", (tg_user.id,)).fetchone()
        if old:
            c.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?", (tg_user.username, tg_user.first_name or "", tg_user.id))
            return False, old["referrer_id"]
        accepted = None
        if referrer_id and referrer_id != tg_user.id and c.execute("SELECT 1 FROM users WHERE user_id=?", (referrer_id,)).fetchone():
            accepted = referrer_id
        c.execute("INSERT INTO users(user_id,username,first_name,referrer_id,created_at) VALUES(?,?,?,?,?)",
                  (tg_user.id, tg_user.username, tg_user.first_name or "", accepted, datetime.now(timezone.utc).isoformat()))
        return True, accepted


def user_row(user_id: int) -> sqlite3.Row | None:
    with db() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def credit_referral_if_pending(user_id: int) -> bool:
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT referrer_id, referral_paid FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row or not row["referrer_id"] or row["referral_paid"]:
            return False
        c.execute("UPDATE users SET points=points+? WHERE user_id=?", (REFERRAL_POINTS, row["referrer_id"]))
        c.execute("UPDATE users SET referral_paid=1 WHERE user_id=?", (user_id,))
        return True


def channel_rows() -> list[sqlite3.Row]:
    with db() as c:
        return c.execute("SELECT * FROM force_channels ORDER BY chat_ref").fetchall()


def set_channel(chat_ref: str, join_url: str) -> None:
    with db() as c:
        c.execute("INSERT INTO force_channels(chat_ref,join_url) VALUES(?,?) ON CONFLICT(chat_ref) DO UPDATE SET join_url=excluded.join_url", (chat_ref, join_url))


def delete_channel(chat_ref: str) -> bool:
    with db() as c:
        cur = c.execute("DELETE FROM force_channels WHERE chat_ref=?", (chat_ref,))
        return cur.rowcount > 0


def add_item(title: str, points: int, media_type: str, file_id: str) -> int:
    with db() as c:
        cur = c.execute("INSERT INTO items(title,points,media_type,file_id,created_at) VALUES(?,?,?,?,?)", (title, points, media_type, file_id, datetime.now(timezone.utc).isoformat()))
        return int(cur.lastrowid)


def add_bundle_item(title: str, points: int, media_type: str, file_id: str, reward_text: str) -> int:
    with db() as c:
        cur = c.execute(
            "INSERT INTO items(title,points,media_type,file_id,reward_text,created_at) VALUES(?,?,?,?,?,?)",
            (title, points, media_type, file_id, reward_text, datetime.now(timezone.utc).isoformat()),
        )
        return int(cur.lastrowid)


def list_items(active_only: bool = True) -> list[sqlite3.Row]:
    with db() as c:
        sql = "SELECT * FROM items" + (" WHERE active=1" if active_only else "") + " ORDER BY item_id DESC"
        return c.execute(sql).fetchall()


def get_item(item_id: int) -> sqlite3.Row | None:
    with db() as c:
        return c.execute("SELECT * FROM items WHERE item_id=? AND active=1", (item_id,)).fetchone()


def deactivate_item(item_id: int) -> bool:
    with db() as c:
        cur = c.execute("UPDATE items SET active=0 WHERE item_id=? AND active=1", (item_id,))
        return cur.rowcount > 0


def set_media(command: str, media_type: str, file_id: str) -> None:
    with db() as c:
        c.execute("INSERT INTO command_media(command,media_type,file_id) VALUES(?,?,?) ON CONFLICT(command) DO UPDATE SET media_type=excluded.media_type,file_id=excluded.file_id", (command, media_type, file_id))


def get_media(command: str) -> sqlite3.Row | None:
    with db() as c:
        return c.execute("SELECT * FROM command_media WHERE command=?", (command,)).fetchone()


def create_code(code: str, points: int, expires_at: str | None, max_uses: int | None) -> None:
    with db() as c:
        c.execute("INSERT INTO redeem_codes(code,points,expires_at,max_uses,created_at) VALUES(?,?,?,?,?)", (code, points, expires_at, max_uses, datetime.now(timezone.utc).isoformat()))


def redeem_code(code: str, user_id: int) -> tuple[bool, str, int | None]:
    code = code.upper().strip()
    now = datetime.now(timezone.utc)
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM redeem_codes WHERE code=?", (code,)).fetchone()
        if not row:
            return False, "That code does not exist.", None
        if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= now:
            return False, "That code has expired.", None
        if row["max_uses"] is not None and row["uses"] >= row["max_uses"]:
            return False, "That code has reached its redemption limit.", None
        if c.execute("SELECT 1 FROM code_redemptions WHERE code=? AND user_id=?", (code, user_id)).fetchone():
            return False, "You have already redeemed that code.", None
        if not c.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone():
            return False, "Please send /start first.", None
        c.execute("INSERT INTO code_redemptions(code,user_id,redeemed_at) VALUES(?,?,?)", (code, user_id, now.isoformat()))
        c.execute("UPDATE redeem_codes SET uses=uses+1 WHERE code=?", (code,))
        c.execute("UPDATE users SET points=points+? WHERE user_id=?", (row["points"], user_id))
        return True, "", int(row["points"])


def claim_item(user_id: int, item_id: int) -> tuple[bool, str, sqlite3.Row | None]:
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        item = c.execute("SELECT * FROM items WHERE item_id=? AND active=1", (item_id,)).fetchone()
        if not item:
            return False, "That reward is no longer available.", None
        user = c.execute("SELECT points FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not user:
            return False, "Please send /start first.", None
        if user["points"] < item["points"]:
            return False, f"You need {item['points']} points; your balance is {user['points']}.", None
        c.execute("UPDATE users SET points=points-? WHERE user_id=?", (item["points"], user_id))
        return True, "", item


def adjust_points(user_id: int, amount: int) -> tuple[bool, int | None]:
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT points FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row or row["points"] + amount < 0:
            return False, row["points"] if row else None
        c.execute("UPDATE users SET points=points+? WHERE user_id=?", (amount, user_id))
        return True, row["points"] + amount


async def membership_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, list[sqlite3.Row]]:
    uid = update.effective_user.id
    missing = []
    for row in channel_rows():
        try:
            chat_ref = row["chat_ref"]
            chat_id: int | str = int(chat_ref) if re.fullmatch(r"-?\d+", chat_ref) else chat_ref
            member = await context.bot.get_chat_member(chat_id=chat_id, user_id=uid)
            is_member = member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER)
            if member.status == ChatMemberStatus.RESTRICTED:
                is_member = bool(getattr(member, "is_member", False))
            if not is_member:
                missing.append(row)
        except Exception as exc:
            log.warning("Could not verify membership for %s: %s", row["chat_ref"], exc)
            missing.append(row)
    return not missing, missing


async def ask_to_join(update: Update, missing: list[sqlite3.Row], context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    buttons = [[InlineKeyboardButton("ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ/ɢʀᴏᴜᴘ", url=r["join_url"])] for r in missing]
    buttons.append([InlineKeyboardButton("✅ ɪ ᴊᴏɪɴᴇᴅ — ᴄʜᴇᴄᴋ", callback_data="joincheck")])
    text = small_caps("To use KAGEX VAULT, join the required channel(s) or group(s), then press I joined — check.")
    if q:
        await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def require_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user or is_admin(user.id):
        return True
    joined, missing = await membership_status(update, context)
    if not joined:
        if update.callback_query:
            await update.callback_query.answer()
        await ask_to_join(update, missing, context)
        return False
    credit_referral_if_pending(user.id)
    return True


def inline_menu(rows: list[list[InlineKeyboardButton]] | None = None) -> InlineKeyboardMarkup:
    buttons = rows or []
    buttons.extend([
        [InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="home"), InlineKeyboardButton("👤 ᴘʀᴏꜰɪʟᴇ", callback_data="profile")],
        [InlineKeyboardButton("🎁 ᴛᴏᴏʟs", callback_data="tools"), InlineKeyboardButton("👥 ʀᴇꜰᴇʀʀᴀʟs", callback_data="referrals")],
        [InlineKeyboardButton("❓ ʜᴇʟᴘ", callback_data="help")],
    ])
    if SUPPORT_URL:
        buttons.append([InlineKeyboardButton("📣 sᴜᴘᴘᴏʀᴛ", url=SUPPORT_URL)])
    return InlineKeyboardMarkup(buttons)


async def send_view(update: Update, context: ContextTypes.DEFAULT_TYPE, view: str, *, use_reply_menu: bool = False, check_access: bool = True) -> None:
    if check_access and view in ("profile", "tools", "referrals", "redeem") and not await require_join(update, context):
        return
    text, media_key = VIEWS.get(view, VIEWS["help"])
    user_id = update.effective_user.id
    if view == "profile":
        with db() as c:
            u = c.execute("SELECT points FROM users WHERE user_id=?", (user_id,)).fetchone()
            referrals_count = c.execute("SELECT COUNT(*) FROM users WHERE referrer_id=? AND referral_paid=1", (user_id,)).fetchone()[0]
        text = text.format(points=u["points"] if u else 0, referrals=referrals_count)
    elif view == "referrals":
        me = await context.bot.get_me()
        text = text.format(referral_points=REFERRAL_POINTS, link=f"https://t.me/{me.username}?start=ref_{user_id}")
    markup: Any = inline_menu()
    if view == "tools":
        items = list_items()
        item_buttons = [[InlineKeyboardButton(small_caps(f"{html.escape(r['title'])} · {r['points']} pts"), callback_data=f"item:{r['item_id']}")] for r in items]
        if not items:
            text += "\n\nNo rewards are available yet."
        markup = inline_menu(item_buttons)
    if use_reply_menu:
        markup = MENU
    text = styled_html(text)
    media = get_media(media_key)
    target = update.callback_query.message if update.callback_query else update.effective_message
    if media:
        await send_media(context, target.chat_id, media["media_type"], media["file_id"], text, markup)
    else:
        await target.reply_text(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)


async def send_media(context: ContextTypes.DEFAULT_TYPE, chat_id: int, media_type: str, file_id: str, caption: str, reply_markup: Any = None) -> None:
    method = {"photo": context.bot.send_photo, "animation": context.bot.send_animation,
              "document": context.bot.send_document, "video": context.bot.send_video,
              "audio": context.bot.send_audio}.get(media_type, context.bot.send_document)
    await method(chat_id=chat_id, **{media_type: file_id}, caption=caption[:1024], reply_markup=reply_markup, parse_mode="HTML")


def format_redeem_result(code: str, success: bool, points: int | None, balance: int, reason: str = "") -> str:
    safe_code = html.escape(code.strip().upper())
    if success:
        return (
            "🎉 <b>REDEMPTION SUCCESSFUL</b>\n\n"
            f"🎟 <b>Code:</b> <code>{safe_code}</code>\n"
            f"➕ <b>Points added:</b> <b>{points} pts</b>\n"
            f"💰 <b>New balance:</b> <b>{balance} pts</b>\n\n"
            "The points were added to your existing balance. Enjoy your rewards!"
        )
    error_messages = {
        "That code does not exist.": ("CODE NOT FOUND", "Check the code spelling and try again. If it still fails, contact the bot owner."),
        "That code has expired.": ("CODE EXPIRED", "This code is no longer valid. Ask the bot owner for a current code."),
        "That code has reached its redemption limit.": ("CODE FULLY REDEEMED", "This code has reached its user limit. Ask the bot owner for another code."),
        "You have already redeemed that code.": ("CODE ALREADY USED", "Each account can redeem a code only once."),
        "Please send /start first.": ("START REQUIRED", "Open the bot with /start, then try the code again."),
    }
    heading, explanation = error_messages.get(reason, ("CODE NOT REDEEMED", reason or "Please check the code and try again."))
    return (
        f"❌ <b>{heading}</b>\n\n"
        f"🎟 <b>Code:</b> <code>{safe_code}</code>\n"
        f"ℹ️ {html.escape(explanation)}\n\n"
        f"💰 <b>Your balance:</b> <b>{balance} pts</b>"
    )


async def send_redeem_result(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, *, chat_id: int | None = None) -> None:
    message = update.effective_message
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 ᴠɪᴇᴡ ᴘʀᴏꜰɪʟᴇ", callback_data="profile")],
        [InlineKeyboardButton("🎁 ʙʀᴏᴡsᴇ ᴛᴏᴏʟs", callback_data="tools")],
    ])
    image = get_media("redeem")
    formatted = styled_html(text)
    destination = chat_id or message.chat_id
    if image:
        try:
            await send_media(context, destination, image["media_type"], image["file_id"], formatted, markup)
            return
        except Exception as exc:
            log.warning("Could not send custom redeem media; falling back to text: %s", exc)
    if chat_id is not None:
        await context.bot.send_message(chat_id=destination, text=formatted, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
    else:
        await message.reply_text(formatted, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ref_id = None
    if context.args:
        match = re.fullmatch(r"ref_(\d+)", context.args[0])
        if match:
            ref_id = int(match.group(1))
    is_new, _ = upsert_user(update.effective_user, ref_id)
    await update.effective_message.reply_text(small_caps("KAGEX VAULT is ready. Use the menu below."), reply_markup=MENU)
    if not await require_join(update, context):
        return
    await send_view(update, context, "start")
    if is_new:
        log.info("New user registered: %s", update.effective_user.id)


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not user_row(update.effective_user.id):
        await update.effective_message.reply_text("sᴇɴᴅ /start ᴛᴏ ʀᴇɢɪsᴛᴇʀ ғɪʀsᴛ.")
        return
    await send_view(update, context, "profile")


async def tools(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not user_row(update.effective_user.id):
        await update.effective_message.reply_text("sᴇɴᴅ /start ᴛᴏ ʀᴇɢɪsᴛᴇʀ ғɪʀsᴛ.")
        return
    await send_view(update, context, "tools")


async def referrals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not user_row(update.effective_user.id):
        await update.effective_message.reply_text("sᴇɴᴅ /start ᴛᴏ ʀᴇɢɪsᴛᴇʀ ғɪʀsᴛ.")
        return
    await send_view(update, context, "referrals")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_view(update, context, "help")


async def redeem_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1:
        await send_view(update, context, "redeem")
        return
    if not await require_join(update, context):
        return
    code = context.args[0]
    ok, reason, points = redeem_code(code, update.effective_user.id)
    account = user_row(update.effective_user.id)
    balance = account["points"] if account else 0
    response = format_redeem_result(code, ok, points, balance, reason)
    await send_redeem_result(update, context, response)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = q.data or ""
    if data.startswith("redeem_code:"):
        code = data.split(":", 1)[1].strip().upper()
        user_id = update.effective_user.id
        if not re.fullmatch(r"[A-Z0-9_-]{3,40}", code):
            await q.answer("Invalid redeem code button.", show_alert=True)
            return
        if not user_row(user_id):
            await q.answer("Open the bot in a private chat and send /start first, then tap Redeem again.", show_alert=True)
            return
        if not await require_join(update, context):
            return
        ok, reason, points = redeem_code(code, user_id)
        account = user_row(user_id)
        balance = account["points"] if account else 0
        response = format_redeem_result(code, ok, points, balance, reason)
        try:
            await send_redeem_result(update, context, response, chat_id=user_id)
        except Exception as exc:
            log.warning("Could not send private redeem receipt to user %s: %s", user_id, exc)
            await q.answer("Open the bot and send /start (or unblock it), then tap Redeem again.", show_alert=True)
            return
        await q.answer("Redeemed! Check your private chat for the receipt." if ok else "Check your private chat for the redemption details.")
        return
    if data == "joincheck":
        joined, missing = await membership_status(update, context)
        if missing:
            await q.answer("I still can't verify all required memberships.", show_alert=True)
            await ask_to_join(update, missing, context)
            return
        credit_referral_if_pending(update.effective_user.id)
        await q.answer("Membership verified!")
        await send_view(update, context, "start")
        return
    if data.startswith("item:"):
        if not await require_join(update, context):
            return
        try:
            item = get_item(int(data.split(":", 1)[1]))
        except ValueError:
            item = None
        if not item:
            await q.answer("ʀᴇᴡᴀʀᴅ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ", show_alert=True)
            return
        await q.answer()
        bundle_note = "\n\nIncludes one file plus a separate text message." if item["reward_text"] else ""
        text = f"<b>{html.escape(item['title'])}</b>\nCost: <b>{item['points']} points</b>{bundle_note}\n\nRedeem this reward?"
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("✅ ʀᴇᴅᴇᴇᴍ", callback_data=f"claim:{item['item_id']}")],
                                       [InlineKeyboardButton("← ʙᴀᴄᴋ ᴛᴏ ᴛᴏᴏʟs", callback_data="tools")]])
        await q.message.reply_text(styled_html(text), parse_mode="HTML", reply_markup=markup)
        return
    if data.startswith("claim:"):
        if not await require_join(update, context):
            return
        try:
            item_id = int(data.split(":", 1)[1])
        except ValueError:
            await q.answer("Invalid reward", show_alert=True)
            return
        ok, reason, item = claim_item(update.effective_user.id, item_id)
        if not ok:
            await q.answer(small_caps(reason), show_alert=True)
            return
        await q.answer("ʀᴇᴡᴀʀᴅ ʀᴇᴅᴇᴇᴍᴇᴅ")
        if item["media_type"] == "text":
            # Deliberately send the stored reward as an ordinary Telegram text message.
            await context.bot.send_message(chat_id=q.message.chat_id,
                text=f"{item['title']}\n\n{item['file_id']}", parse_mode=None)
        else:
            caption = styled_html(f"✅ Redeemed: <b>{html.escape(item['title'])}</b>")
            await send_media(context, q.message.chat_id, item["media_type"], item["file_id"], caption)
            if item["reward_text"]:
                await context.bot.send_message(chat_id=q.message.chat_id, text=item["reward_text"], parse_mode=None)
        return
    if data == "home":
        await q.answer()
        await send_view(update, context, "start")
        return
    if data in VIEWS:
        if data in ("profile", "tools", "referrals", "redeem") and not await require_join(update, context):
            return
        await q.answer()
        await send_view(update, context, data, check_access=False)
        return
    await q.answer()


async def add_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Usage: /addchannel <@username-or-chat_id> <https://t.me/join_link>")
        return
    chat_ref, link = context.args
    if not link.startswith("https://t.me/"):
        await update.effective_message.reply_text("Join link must begin with https://t.me/")
        return
    set_channel(chat_ref, link)
    await update.effective_message.reply_text(f"Force-join target saved: {chat_ref}. Make the bot an admin there so Telegram can verify membership.")


async def remove_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /removechannel <@username-or-chat_id>")
        return
    await update.effective_message.reply_text("Removed." if delete_channel(context.args[0]) else "No matching force-join target.")


async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    rows = channel_rows()
    await update.effective_message.reply_text("Force-join targets:\n" + ("\n".join(f"• {r['chat_ref']} → {r['join_url']}" for r in rows) or "(none)"))


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    chat = update.effective_chat
    await update.effective_message.reply_text(f"Chat ID: <code>{chat.id}</code>", parse_mode="HTML")


async def createcode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 4:
        await update.effective_message.reply_text("Usage: /createcode <CODE> <POINTS> <EXPIRY_HOURS|0> <MAX_USES|0>\n0 expiry means never; 0 max uses means unlimited.")
        return
    code = context.args[0].upper()
    try:
        points, hours, max_uses = map(int, context.args[1:])
        if not re.fullmatch(r"[A-Z0-9_-]{3,40}", code) or points <= 0 or hours < 0 or max_uses < 0:
            raise ValueError
        expires = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat() if hours else None
        create_code(code, points, expires, max_uses or None)
    except sqlite3.IntegrityError:
        await update.effective_message.reply_text("That code already exists. Choose a unique code.")
        return
    except ValueError:
        await update.effective_message.reply_text("Use a 3–40 character code (letters, numbers, _ or -), positive points, and non-negative expiry/limit values.")
        return
    expiry_label = f"{hours} hour(s) from creation" if hours else "Never"
    uses_label = str(max_uses) if max_uses else "Unlimited"
    confirmation = (
        "🎟 <b>REDEEM CODE READY</b>\n\n"
        f"🔐 <b>Code:</b> <code>{html.escape(code)}</code>\n"
        f"💰 <b>Reward:</b> <b>{points} points</b>\n"
        f"⏳ <b>Expires:</b> {expiry_label}\n"
        f"👥 <b>Available redemptions:</b> {uses_label}\n\n"
        "Tap the button below to redeem this code."
    )
    button = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎟 ʀᴇᴅᴇᴇᴍ ɴᴏᴡ", callback_data=f"redeem_code:{code}")
    ]])
    await update.effective_message.reply_text(styled_html(confirmation), parse_mode="HTML", reply_markup=button)


def extract_media(message: Any) -> tuple[str, str | None]:
    if message.photo:
        return "photo", message.photo[-1].file_id
    for key in ("document", "video", "animation", "audio"):
        obj = getattr(message, key, None)
        if obj:
            return key, obj.file_id
    return "", None


async def additem_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    msg = update.effective_message
    if not msg or not msg.caption:
        await msg.reply_text("Attach the reward file and use this as its caption: /additem <POINTS> <title>\nSupported: document, photo, video, animation, audio.")
        return
    match = re.match(r"^/additem(?:@\w+)?\s+(\d+)\s+(.+)$", msg.caption.strip(), flags=re.S)
    if not match:
        await msg.reply_text("Caption format: /additem <POINTS> <title>")
        return
    points, title = int(match.group(1)), match.group(2).strip()
    if points <= 0 or not title:
        await msg.reply_text("Points and title must be valid; cost must be at least 1.")
        return
    media_type, file_id = extract_media(msg)
    if not file_id:
        await msg.reply_text("I couldn't read the attached file. Send it as a document/photo/video/animation/audio.")
        return
    item_id = add_item(title[:120], points, media_type, file_id)
    await msg.reply_text(f"✅ Added reward #{item_id}: {title[:120]} ({points} points).")


async def addtext_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /addtext <POINTS> <title>, then send the reward text as your next message.")
        return
    try:
        points = int(context.args[0])
        title = " ".join(context.args[1:]).strip()
        if points <= 0 or not title:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("Points must be a positive whole number, followed by a title.")
        return
    context.application.bot_data.setdefault("pending_bundle", {}).pop(update.effective_user.id, None)
    context.application.bot_data.setdefault("pending_media", {}).pop(update.effective_user.id, None)
    context.application.bot_data.setdefault("pending_text", {})[update.effective_user.id] = {
        "points": points, "title": title[:120],
    }
    prompt = small_caps(f"Now send the plain text content for '{title[:120]}'. It will be delivered to users as a normal text message.")
    await update.effective_message.reply_text(prompt + "\nUse /cancel to stop.")


async def addbundle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage: /addbundle <POINTS> <title>. Then send the file, followed by its text message."
        )
        return
    try:
        points = int(context.args[0])
        title = " ".join(context.args[1:]).strip()
        if points <= 0 or not title:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("Points must be a positive whole number, followed by a title.")
        return
    uid = update.effective_user.id
    context.application.bot_data.setdefault("pending_text", {}).pop(uid, None)
    context.application.bot_data.setdefault("pending_media", {}).pop(uid, None)
    context.application.bot_data.setdefault("pending_bundle", {})[uid] = {
        "points": points, "title": title[:120],
    }
    await update.effective_message.reply_text(
        f"Now send the main reward file for '{title[:120]}' (ZIP or another supported file). "
        "After the file is received, send the accompanying plain text. Use /cancel to stop."
    )


async def items_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    rows = list_items(active_only=False)
    text = "Rewards:\n" + ("\n".join(
        f"#{r['item_id']} — {r['title']} ({r['points']} pts)"
        f"{' [FILE + TEXT]' if r['reward_text'] else ''} {'ACTIVE' if r['active'] else 'DISABLED'}"
        for r in rows
    ) or "(none)")
    await update.effective_message.reply_text(text)


async def delitem_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /delitem <item_id>")
        return
    await update.effective_message.reply_text("Reward disabled." if deactivate_item(int(context.args[0])) else "Active reward not found.")


async def setpoints_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Usage: /setpoints <user_id> <signed_delta>\nExample: /setpoints 123456 10 or /setpoints 123456 -5")
        return
    try:
        user_id, amount = map(int, context.args)
        if amount == 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("User ID and non-zero signed point change must be integers.")
        return
    ok, balance = adjust_points(user_id, amount)
    await update.effective_message.reply_text(f"Updated. New balance: {balance}." if ok else (f"Change would make balance negative (current: {balance})." if balance is not None else "User is not registered yet."))


async def setimage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /setimage <start|profile|tools|redeem|referrals|help> then send the image or animation.")
        return
    key = context.args[0].lower().lstrip("/")
    if key not in VIEWS:
        await update.effective_message.reply_text("Supported views: " + ", ".join(VIEWS))
        return
    context.application.bot_data.setdefault("pending_bundle", {}).pop(update.effective_user.id, None)
    context.application.bot_data.setdefault("pending_text", {}).pop(update.effective_user.id, None)
    context.application.bot_data.setdefault("pending_media", {})[update.effective_user.id] = key
    await update.effective_message.reply_text(f"Now send the photo or animation for /{key}. Send /cancel to cancel.")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.application.bot_data.setdefault("pending_media", {}).pop(update.effective_user.id, None)
    context.application.bot_data.setdefault("pending_text", {}).pop(update.effective_user.id, None)
    context.application.bot_data.setdefault("pending_bundle", {}).pop(update.effective_user.id, None)
    await update.effective_message.reply_text("ᴄᴀɴᴄᴇʟʟᴇᴅ.")


async def media_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    uid = update.effective_user.id
    bundle = context.application.bot_data.setdefault("pending_bundle", {}).get(uid)
    if bundle and is_admin(uid):
        if bundle.get("file_id"):
            await msg.reply_text("The bundle file is already saved. Send the accompanying text now, or /cancel to restart.")
            return
        media_type, file_id = extract_media(msg)
        if not file_id:
            await msg.reply_text("I couldn't read that file. Send a document/photo/video/animation/audio, or /cancel.")
            return
        bundle["media_type"] = media_type
        bundle["file_id"] = file_id
        await msg.reply_text("✅ File received. Now send the accompanying plain-text message (up to 4096 characters).")
        return
    pending = context.application.bot_data.setdefault("pending_media", {}).get(uid)
    if pending and is_admin(uid):
        media_type, file_id = extract_media(msg)
        if not file_id or media_type not in ("photo", "animation"):
            await msg.reply_text("Please send a photo or animation, or /cancel.")
            return
        set_media(pending, media_type, file_id)
        context.application.bot_data["pending_media"].pop(uid, None)
        await msg.reply_text(f"✅ Custom {media_type} saved for /{pending}.")
        return
    if (msg.caption or "").startswith("/additem"):
        await additem_command(update, context)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("This command is for bot admins only.")
        return
    await update.effective_message.reply_text(
        "<b>KAGEX VAULT admin commands</b>\n"
        "/additem — attach a file and caption it: <code>/additem POINTS Title</code>\n"
        "/addtext — create a text reward: <code>/addtext POINTS Title</code>, then send the text\n"
        "/addbundle — create one reward with a file and text: <code>/addbundle POINTS Title</code>, then send the file and text\n"
        "/items, /delitem ID\n"
        "/createcode CODE POINTS EXPIRY_HOURS MAX_USES (0 = unlimited)\n"
        "/addchannel CHAT JOIN_URL, /removechannel CHAT, /channels\n"
        "/chatid — show the numeric ID of this chat for private-group setup\n"
        "/setpoints USER_ID SIGNED_DELTA\n"
        "/setimage start|profile|tools|redeem|referrals|help then send a photo/GIF\n",
        parse_mode="HTML")


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw_text = update.effective_message.text or ""
    bundle = context.application.bot_data.setdefault("pending_bundle", {}).get(update.effective_user.id)
    if bundle and is_admin(update.effective_user.id):
        if not bundle.get("file_id"):
            await update.effective_message.reply_text("Please send the reward file first, then send its accompanying text.")
            return
        if len(raw_text) > 4096:
            await update.effective_message.reply_text("That text is too long for one Telegram message. Send up to 4096 characters, or /cancel.")
            return
        item_id = add_bundle_item(bundle["title"], bundle["points"], bundle["media_type"], bundle["file_id"], raw_text)
        context.application.bot_data["pending_bundle"].pop(update.effective_user.id, None)
        await update.effective_message.reply_text(
            f"✅ Added bundle reward #{item_id}: {bundle['title']} ({bundle['points']} points). "
            "Users will receive the file and text after one claim."
        )
        return
    pending = context.application.bot_data.setdefault("pending_text", {}).get(update.effective_user.id)
    if pending and is_admin(update.effective_user.id):
        if len(raw_text) + len(pending["title"]) + 2 > 4096:
            await update.effective_message.reply_text("ᴛʜᴀᴛ ᴛᴇxᴛ ɪs ᴛᴏᴏ ʟᴏɴɢ ғᴏʀ ᴀ sɪɴɢʟᴇ ᴛᴇʟᴇɢʀᴀᴍ ᴍᴇssᴀɢᴇ. sᴇɴᴅ ᴀ sʜᴏʀᴛᴇʀ ᴠᴇʀsɪᴏɴ ᴏʀ send /cancel.")
            return
        context.application.bot_data["pending_text"].pop(update.effective_user.id, None)
        item_id = add_item(pending["title"], pending["points"], "text", raw_text)
        await update.effective_message.reply_text(small_caps(f"Plain-text reward #{item_id} saved at {pending['points']} points."))
        return
    text = raw_text.strip().lower()
    mapping = {
        "🏠 ʜᴏᴍᴇ": "start", "home": "start", "↩️ ʀᴇᴛᴜʀɴ": "start", "return": "start",
        "👤 ᴘʀᴏꜰɪʟᴇ": "profile", "profile": "profile", "🎁 ᴛᴏᴏʟs": "tools", "tools": "tools",
        "👥 ʀᴇꜰᴇʀʀᴀʟs": "referrals", "referrals": "referrals", "📣 sᴜᴘᴘᴏʀᴛ": "support", "support": "support",
        "🎟 ʀᴇᴅᴇᴇᴍ": "redeem", "redeem": "redeem",
    }
    view = mapping.get(text)
    if view == "support":
        await update.effective_message.reply_text(f"sᴜᴘᴘᴏʀᴛ: {SUPPORT_URL}" if SUPPORT_URL else "ᴄᴏɴᴛᴀᴄᴛ ᴛʜᴇ ʙᴏᴛ ᴏᴡɴᴇʀ ғᴏʀ sᴜᴘᴘᴏʀᴛ.")
    elif view:
        await send_view(update, context, view)


async def post_init(application: Application) -> None:
    init_db()
    try:
        await application.bot.set_my_commands([
            ("start", "Open the vault"), ("profile", "View points and referrals"),
            ("tools", "Browse rewards"), ("referrals", "Get your invite link"),
            ("redeem", "Redeem a points code"), ("help", "How to use the bot"),
            ("admin", "Admin controls"), ("chatid", "Show this chat's ID (admins)")])
    except Exception as exc:
        log.warning("Could not set bot commands: %s", exc)


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Missing BOT_TOKEN. Copy .env.example to .env and set your token.")
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("tools", tools))
    app.add_handler(CommandHandler("referrals", referrals))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("redeem", redeem_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("addchannel", add_channel_command))
    app.add_handler(CommandHandler("removechannel", remove_channel_command))
    app.add_handler(CommandHandler("channels", channels_command))
    app.add_handler(CommandHandler("chatid", chatid_command))
    app.add_handler(CommandHandler("createcode", createcode_command))
    app.add_handler(CommandHandler("additem", additem_command))
    app.add_handler(CommandHandler("addtext", addtext_command))
    app.add_handler(CommandHandler("addbundle", addbundle_command))
    app.add_handler(CommandHandler("items", items_command))
    app.add_handler(CommandHandler("delitem", delitem_command))
    app.add_handler(CommandHandler("setpoints", setpoints_command))
    app.add_handler(CommandHandler("setimage", setimage_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.ANIMATION | filters.Document.ALL | filters.VIDEO | filters.AUDIO, media_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    log.info("KAGEX VAULT bot starting (admins=%s, db=%s)", sorted(ADMIN_IDS), DB_PATH)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()
