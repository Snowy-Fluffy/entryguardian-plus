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
import asyncio
import html
import logging
import config
from translator import Translator
from moderation_handler import invalidate_native_admins, build_chat_permissions
# Shared with the moderation package on purpose: the same Retry-After wrapper and the same
# raid-reminder registry (so /raid_off can delete the last reminder this task posted).
from moderation_handler.common import _flood_safe, _raid_reminder_msg

router = Router()
db_man = DBManager()
translator = Translator(config.LOCALE)
log = logging.getLogger('entryguardian.join')

bot_username: str | None = None

_raid_bans: dict[int, int] = {}

_WELCOME_COOLDOWN = 3600

_CAPTCHA_KICK_AFTER = 86400

_MUTED = ChatPermissions(can_send_messages=False)


async def delete_welcome_msg(bot: Bot, user_id: int, chat_id: int | None = None) -> None:
    """Delete a user's welcome/captcha prompt(s) — everywhere, or in one chat — once they're
    verified or kicked. Backed by the welcome_msgs table, so it works across restarts."""
    for cid, mid in db_man.pop_welcomes_for_user(user_id, chat_id):
        try:
            await bot.delete_message(cid, mid)
        except Exception:
            pass


@router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def handle_new_user(event: ChatMemberUpdated, bot: Bot):
    """A member joined. Order matters: anything that must *stop* them (ban, mute, captcha
    restriction) is applied before anything that merely talks to them (welcome message), so a
    flood error on the message can't leave the newcomer unrestricted."""
    user = event.new_chat_member.user
    user_id = user.id
    chat_id = event.chat.id

    if user.is_bot:
        return   # another bot added by an admin — not a captcha subject

    db_man.remember_user(user_id, user.username, user.full_name)
    db_man.remember_chat(chat_id)

    if db_man.is_blocklisted(user_id) and not db_man.is_ban_exception(chat_id, user_id):
        try:
            await _flood_safe(lambda: bot.ban_chat_member(chat_id=chat_id, user_id=user_id))
        except Exception:
            pass
        return

    if db_man.is_raid_mode(chat_id):
        try:
            await _flood_safe(lambda: bot.ban_chat_member(chat_id=chat_id, user_id=user_id))
            _raid_bans[chat_id] = _raid_bans.get(chat_id, 0) + 1
        except Exception:
            pass
        return

    # A standing mute (global or this chat's) is re-applied on join. It is *not* the end of the
    # story: an unverified member still goes through the captcha below, otherwise a timed mute
    # expiring would leave them a full member who never passed it.
    muted, until = db_man.effective_mute(chat_id, user_id)
    if muted:
        try:
            await _flood_safe(lambda: bot.restrict_chat_member(
                chat_id=chat_id, user_id=user_id, permissions=_MUTED, until_date=until or None))
        except Exception:
            pass

    if not db_man.is_captcha_enabled(chat_id):
        return

    if db_man.is_user_allowed(user_id):
        # Verified already. A leftover pending row here means an earlier unrestrict failed (or
        # the bot restarted mid-verification) — finish that job now instead of leaving them muted.
        if chat_id in db_man.get_pending_chats(user_id):
            db_man.remove_pending_chat(user_id, chat_id)
            if not muted:
                try:
                    await _flood_safe(lambda: bot.restrict_chat_member(
                        chat_id=chat_id, user_id=user_id, permissions=build_chat_permissions(chat_id)))
                except Exception:
                    pass
        return

    # Unverified: restrict first (no until_date = until we lift it), then record, then greet.
    if not muted:
        try:
            await _flood_safe(lambda: bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=_MUTED))
        except Exception:
            log.warning('could not restrict new member %s in chat %s', user_id, chat_id, exc_info=True)
    db_man.record_captcha_origin(user_id, chat_id)
    db_man.add_pending_chat(user_id, chat_id)

    if db_man.welcome_within(chat_id, user_id, _WELCOME_COOLDOWN):
        return

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

    # Only the newest welcome stays up in a chat (a join wave would otherwise pile them up);
    # earlier joiners keep their DM link, and the captcha prompt reaches them via /start anyway.
    for mid in db_man.pop_welcomes_in_chat(chat_id):
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass

    db_man.set_pending_since(chat_id, user_id)
    try:
        sent = await _flood_safe(lambda: bot.send_message(chat_id, msg, reply_markup=keyboard, parse_mode='HTML'))
        db_man.set_welcome(chat_id, user_id, sent.message_id)
    except Exception:
        log.warning('could not post welcome for %s in chat %s', user_id, chat_id, exc_info=True)


@router.chat_member()
async def cache_chat_member_identity(event: ChatMemberUpdated, bot: Bot) -> None:
    """Passive id<->username cache backfill. Registered after handle_new_user, whose narrower
    filter (join transitions only) is matched first — aiogram stops at the first handler whose
    filters pass, so joins stay handled exclusively there. This one only ever sees every *other*
    chat_member update (promotions, restrictions, leaves, kicks), which still carries a full User
    object worth caching for future @username command lookups. A status change (promotion,
    demotion, restriction) also drops the chat's cached Telegram-admin list, so the antispam
    exemption for native admins follows the change immediately."""
    user = event.new_chat_member.user
    db_man.remember_user(user.id, user.username, user.full_name)
    if event.old_chat_member.status != event.new_chat_member.status:
        invalidate_native_admins(event.chat.id)


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


_KICK_THROTTLE = 1.0
_KICK_BATCH = 50
_UNBAN_AFTER = 15
_UNBAN_RETRY_INTERVAL = 20
_UNBAN_MAX_ATTEMPTS = 10
_UNBAN_GIVE_UP_MARKERS = ('chat not found', 'not a member', 'kicked', 'user not found', 'bot was blocked')


def _terminal_api_error(e: Exception) -> bool:
    """An error that no retry will fix: the bot is gone from the chat, the chat is gone, the
    user doesn't exist."""
    text = str(e).lower()
    return any(marker in text for marker in _UNBAN_GIVE_UP_MARKERS)


async def _try_unban(bot: Bot, chat_id: int, user_id: int, attempts: int) -> bool:
    """Lift a kick's ban. On failure, defer with exponential backoff and give up after
    _UNBAN_MAX_ATTEMPTS or on an error that can't be retried (bot removed, chat gone)."""
    try:
        await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
        db_man.remove_pending_unban(chat_id, user_id)
        return True
    except Exception as e:
        retry_after = int(getattr(e, 'retry_after', 0) or 0)
        if _terminal_api_error(e) or attempts + 1 >= _UNBAN_MAX_ATTEMPTS:
            db_man.remove_pending_unban(chat_id, user_id)
            return False
        delay = retry_after or min(60 * 2 ** attempts, 3600)
        db_man.defer_pending_unban(chat_id, user_id, db_man.unix_time() + delay)
        return False


async def _kick_expired(bot: Bot, chat_id: int, user_id: int) -> None:
    """Kick one timed-out user: record the unban obligation, then ban. The unban itself is done
    later by pending_unban_retry_task, once the ban has settled (avoids the ban/unban race).
    If the ban can't be done at all (no rights, bot gone, target became an admin), the pending
    row is dropped rather than retried every sweep forever."""
    db_man.add_pending_unban(chat_id, user_id, db_man.unix_time() + _UNBAN_AFTER)
    try:
        await _flood_safe(lambda: bot.ban_chat_member(chat_id, user_id))
    except Exception:
        db_man.remove_pending_unban(chat_id, user_id)
    db_man.remove_pending_chat(user_id, chat_id)
    await delete_welcome_msg(bot, user_id, chat_id)


async def captcha_timeout_task(bot: Bot) -> None:
    """Kick users who never passed the captcha within _CAPTCHA_KICK_AFTER seconds.
    They are unbanned shortly after by the retry task, so they can rejoin. Throttled and capped
    per sweep so a large backlog drains steadily instead of tripping the rate limit."""
    while True:
        await asyncio.sleep(600)
        done = 0
        for user_id, chat_id in db_man.get_expired_pending(_CAPTCHA_KICK_AFTER):
            if done >= _KICK_BATCH:
                break
            if db_man.is_user_allowed(user_id):
                db_man.remove_pending_chat(user_id, chat_id)   # verified meanwhile; stale row
                continue
            if (not db_man.is_captcha_enabled(chat_id) or not db_man.is_kick_enabled(chat_id)
                    or db_man.is_chat_stopped(chat_id)):
                continue
            await _kick_expired(bot, chat_id, user_id)
            done += 1
            await asyncio.sleep(_KICK_THROTTLE)


async def pending_unban_retry_task(bot: Bot) -> None:
    """Perform every kick's unban once its ban has settled, retrying with backoff until it
    succeeds or is given up on, so a kick never leaves someone stuck banned."""
    while True:
        await asyncio.sleep(_UNBAN_RETRY_INTERVAL)
        for chat_id, user_id, attempts in db_man.get_due_unbans(db_man.unix_time()):
            await _try_unban(bot, chat_id, user_id, attempts)
            await asyncio.sleep(_KICK_THROTTLE)
