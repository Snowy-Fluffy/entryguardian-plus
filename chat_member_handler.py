# Entry Guardian - a Telegram bot that prevents spam bots from joining a group
# Copyright: 2025 Entry Guardian Dev Team

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

from aiogram import Router, Bot
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter
from aiogram.types.chat_member_updated import ChatMemberUpdated
from aiogram.types.chat_permissions import ChatPermissions
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import IS_MEMBER, IS_NOT_MEMBER
from dbmanager import DBManager
from datetime import datetime
import asyncio
import config
from translator import Translator

router = Router()
db_man = DBManager()
translator = Translator(config.LOCALE)

# Set by run.py after bot.get_me() so we can build the deep-link URL
bot_username: str | None = None

# user_id → list of (chat_id, message_id) — one entry per chat the user joined
_welcome_msg_by_user: dict[int, list[tuple[int, int]]] = {}

# chat_id → number of newcomers banned by anti-raid since the last reminder
_raid_bans: dict[int, int] = {}


async def delete_welcome_msg(bot: Bot, user_id: int) -> None:
    """Delete all welcome messages for a user after they verify. Called from personal_msg_handler."""
    for chat_id, msg_id in _welcome_msg_by_user.pop(user_id, []):
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception:
            pass


@router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def handle_new_user(event: ChatMemberUpdated, bot: Bot):
    user = event.new_chat_member.user
    user_id = user.id
    chat_id = event.chat.id

    db_man.remember_user(user_id, user.username)
    db_man.remember_chat(chat_id)

    if db_man.is_blocklisted(user_id) and not db_man.is_ban_exception(chat_id, user_id):
        try:
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        except Exception:
            pass
        return

    # Anti-raid: ban every newcomer locally, silently, without showing the captcha.
    if db_man.is_raid_mode(chat_id):
        try:
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            _raid_bans[chat_id] = _raid_bans.get(chat_id, 0) + 1
        except Exception:
            pass
        return

    # A still-active mute (local or global) must follow the user in — re-apply it, skip the captcha.
    muted, until = db_man.effective_mute(chat_id, user_id)
    if muted:
        try:
            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until or None,
            )
        except Exception:
            pass
        db_man.add_mute(chat_id, user_id, until)  # record per-chat so /ungmute's sweep clears it
        return

    # The blocklist check above always runs; the captcha itself can be disabled per chat.
    if not db_man.is_captcha_enabled(chat_id):
        return

    if db_man.is_user_allowed(user_id):
        return

    if chat_id in db_man.get_pending_chats(user_id):
        return

    user_display = f'@{user.username}' if user.username else user.first_name
    msg = translator.get_string('welcome_msg').format(user_display)

    keyboard = None
    if bot_username:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=translator.get_string('start_button'),
                url=f'https://t.me/{bot_username}?start=verify'
            )
        ]])

    # Delete any existing welcome message in this chat (for a previous pending user)
    for uid, entries in list(_welcome_msg_by_user.items()):
        for cid, mid in entries:
            if cid == chat_id:
                try:
                    await bot.delete_message(chat_id, mid)
                except Exception:
                    pass
        _welcome_msg_by_user[uid] = [(cid, mid) for cid, mid in entries if cid != chat_id]
        if not _welcome_msg_by_user[uid]:
            del _welcome_msg_by_user[uid]

    sent = await bot.send_message(chat_id, msg, reply_markup=keyboard)
    _welcome_msg_by_user.setdefault(user_id, []).append((chat_id, sent.message_id))

    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=ChatPermissions(can_send_messages=False),
        until_date=int(datetime.now().timestamp()) + 5
    )

    db_man.add_pending_chat(user_id, chat_id)


async def raid_reminder_task(bot: Bot) -> None:
    """Every 5 minutes, remind each anti-raid chat that the mode is on and how many were banned."""
    while True:
        await asyncio.sleep(300)
        for chat_id in db_man.get_raid_chats():
            count = _raid_bans.pop(chat_id, 0)
            try:
                await bot.send_message(chat_id, translator.get_string('raid_reminder').format(count))
            except Exception:
                pass
        # Drop counters for chats that left raid mode so they don't carry stale numbers.
        _raid_bans.clear()
