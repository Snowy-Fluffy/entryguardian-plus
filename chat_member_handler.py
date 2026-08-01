# Entry Guardian - a Telegram bot that prevents spam bots from joining a group
# Copyright: 2026 thmunix and Luna River

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
from aiogram.types.chat_join_request import ChatJoinRequest
from aiogram.types.chat_permissions import ChatPermissions
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import IS_MEMBER, IS_NOT_MEMBER
from dbmanager import DBManager
from datetime import datetime
import asyncio
import html
import config
from translator import Translator

router = Router()
db_man = DBManager()
translator = Translator(config.LOCALE)

bot_username: str | None = None

_welcome_msg_by_user: dict[int, list[tuple[int, int]]] = {}

_raid_bans: dict[int, int] = {}

_raid_reminder_msg: dict[int, int] = {}

_WELCOME_COOLDOWN = 3600

_CAPTCHA_KICK_AFTER = 86400


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

    db_man.remember_user(user_id, user.username, user.full_name)
    db_man.remember_chat(chat_id)

    if db_man.is_blocklisted(user_id) and not db_man.is_ban_exception(chat_id, user_id):
        try:
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        except Exception:
            pass
        return

    if db_man.is_raid_mode(chat_id):
        try:
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            _raid_bans[chat_id] = _raid_bans.get(chat_id, 0) + 1
        except Exception:
            pass
        return

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
        db_man.add_mute(chat_id, user_id, until)
        return

    if not db_man.is_captcha_enabled(chat_id):
        return

    if db_man.is_user_allowed(user_id):
        return

    if not db_man.welcome_within(chat_id, user_id, _WELCOME_COOLDOWN):
        user_name = html.escape(user.full_name, quote=False)
        user_display = f'<a href="tg://user?id={user_id}">{user_name}</a>'
        welcome_key = 'welcome_msg' if db_man.is_kick_enabled(chat_id) else 'welcome_msg_nokick'
        msg = translator.get_string(welcome_key).format(user_display)

        keyboard = None
        if bot_username:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text=translator.get_string('start_button'),
                    url=f'https://t.me/{bot_username}?start=verify'
                )
            ]])

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

        sent = await bot.send_message(chat_id, msg, reply_markup=keyboard, parse_mode='HTML')
        _welcome_msg_by_user.setdefault(user_id, []).append((chat_id, sent.message_id))
        db_man.set_pending_since(chat_id, user_id)

    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=ChatPermissions(can_send_messages=False),
        until_date=int(datetime.now().timestamp()) + 5
    )

    db_man.add_pending_chat(user_id, chat_id)


@router.chat_join_request()
async def handle_join_request(event: ChatJoinRequest, bot: Bot) -> None:
    """A chat that requires admin approval to join. Discovered reactively (Telegram doesn't expose
    this setting up front) — the first request seen marks the chat so the admin panel can offer
    the auto-accept toggle. Blocklisted users are declined and banned outright; everyone else is
    approved only if the chat owner turned auto-accept on, then goes through the normal captcha
    flow same as any other join (approval fires the usual chat_member IS_NOT_MEMBER>>IS_MEMBER
    update)."""
    user = event.from_user
    user_id = user.id
    chat_id = event.chat.id

    db_man.remember_user(user_id, user.username, user.full_name)
    db_man.remember_chat(chat_id)
    db_man.mark_join_request_chat(chat_id)

    if db_man.is_blocklisted(user_id) and not db_man.is_ban_exception(chat_id, user_id):
        try:
            await bot.decline_chat_join_request(chat_id, user_id)
        except Exception:
            pass
        try:
            await bot.ban_chat_member(chat_id, user_id)
        except Exception:
            pass
        return

    if not db_man.is_auto_accept(chat_id):
        return

    try:
        await bot.approve_chat_join_request(chat_id, user_id)
    except Exception:
        pass


async def raid_reminder_task(bot: Bot) -> None:
    """Every 5 minutes, remind each anti-raid chat that the mode is on and how many were banned.
    Each reminder replaces the previous one (old one deleted first) so the chat isn't cluttered
    with a fresh copy every 5 minutes for as long as anti-raid stays on."""
    while True:
        await asyncio.sleep(300)
        for chat_id in db_man.get_raid_chats():
            count = _raid_bans.pop(chat_id, 0)
            old_msg_id = _raid_reminder_msg.pop(chat_id, None)
            if old_msg_id is not None:
                try:
                    await bot.delete_message(chat_id, old_msg_id)
                except Exception:
                    pass
            try:
                text = html.escape(translator.get_string('raid_reminder').format(count), quote=False)
                sent = await bot.send_message(chat_id, f'<i>{text}</i>', parse_mode='HTML')
                _raid_reminder_msg[chat_id] = sent.message_id
            except Exception:
                pass
        _raid_bans.clear()


async def _delete_user_welcome_in_chat(bot: Bot, user_id: int, chat_id: int) -> None:
    """Best-effort removal of a user's welcome message in a single chat (and our tracking of it)."""
    entries = _welcome_msg_by_user.get(user_id)
    if not entries:
        return
    remaining = []
    for cid, mid in entries:
        if cid == chat_id:
            try:
                await bot.delete_message(chat_id, mid)
            except Exception:
                pass
        else:
            remaining.append((cid, mid))
    if remaining:
        _welcome_msg_by_user[user_id] = remaining
    else:
        _welcome_msg_by_user.pop(user_id, None)


_KICK_THROTTLE = 1.0
_UNBAN_AFTER = 15
_UNBAN_RETRY_INTERVAL = 20


async def _flood_safe(call):
    """Await a bot call; if rate-limited, wait out Telegram's Retry-After once and retry."""
    try:
        return await call()
    except Exception as e:
        retry_after = int(getattr(e, 'retry_after', 0) or 0)
        if retry_after:
            await asyncio.sleep(retry_after + 1)
            return await call()
        raise


async def _try_unban(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Lift a kick's ban. On failure, leave a persistent retry obligation (honouring Retry-After)."""
    try:
        await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
        db_man.remove_pending_unban(chat_id, user_id)
        return True
    except Exception as e:
        delay = int(getattr(e, 'retry_after', 0) or 0) or 60
        db_man.add_pending_unban(chat_id, user_id, db_man.unix_time() + delay)
        return False


async def _kick_expired(bot: Bot, chat_id: int, user_id: int) -> None:
    """Kick one timed-out user: record the unban obligation, then ban. The unban itself is done
    later by pending_unban_retry_task, once the ban has settled (avoids the ban/unban race)."""
    db_man.add_pending_unban(chat_id, user_id, db_man.unix_time() + _UNBAN_AFTER)
    try:
        await _flood_safe(lambda: bot.ban_chat_member(chat_id, user_id))
    except Exception:
        return
    db_man.remove_pending_chat(user_id, chat_id)
    await _delete_user_welcome_in_chat(bot, user_id, chat_id)


async def captcha_timeout_task(bot: Bot) -> None:
    """Kick users who never passed the captcha within _CAPTCHA_KICK_AFTER seconds.
    They are unbanned shortly after by the retry task, so they can rejoin. Throttled so a large
    backlog drains steadily instead of tripping the rate limit and stalling."""
    while True:
        await asyncio.sleep(600)
        for user_id, chat_id in db_man.get_expired_pending(_CAPTCHA_KICK_AFTER):
            if not db_man.is_captcha_enabled(chat_id) or not db_man.is_kick_enabled(chat_id):
                continue
            await _kick_expired(bot, chat_id, user_id)
            await asyncio.sleep(_KICK_THROTTLE)


async def pending_unban_retry_task(bot: Bot) -> None:
    """Perform every kick's unban once its ban has settled, retrying until it succeeds, so a kick
    never leaves someone stuck banned."""
    while True:
        await asyncio.sleep(_UNBAN_RETRY_INTERVAL)
        try:
            due = db_man.get_due_unbans(db_man.unix_time())
        except Exception:
            continue
        for chat_id, user_id in due:
            await _try_unban(bot, chat_id, user_id)
            await asyncio.sleep(_KICK_THROTTLE)
