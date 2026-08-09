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

from typing import Any, Awaitable, Callable
from datetime import datetime
import asyncio
import unicodedata
from aiogram import types, Bot, BaseMiddleware
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter
from aiogram.types.chat_member_updated import ChatMemberUpdated
from aiogram.filters import IS_NOT_MEMBER, IS_MEMBER, MEMBER, ADMINISTRATOR
import permissions
import config
from .common import (
    router, db_man, translator, _GROUP_TYPES, _spawn,
    _seen_cache, _isend, _isend_html, _identity_html, _full_user_info, _chat_info,
    _channel_mention, _message_link, _report_recipients, _human_elapsed, _seconds_to_words,
    _clear_captcha_state, _MUTED_PERMS,
)

_MESSAGE_RETENTION = 2 * 24 * 3600
_MESSAGE_FLUSH_INTERVAL = 10
_MESSAGE_PURGE_INTERVAL = 1800


async def flush_messages_task() -> None:
    while True:
        await asyncio.sleep(_MESSAGE_FLUSH_INTERVAL)
        db_man.flush_messages()
        db_man.flush_channel_messages()


async def purge_old_messages_task() -> None:
    while True:
        await asyncio.sleep(_MESSAGE_PURGE_INTERVAL)
        cutoff = db_man.unix_time() - _MESSAGE_RETENTION
        db_man.purge_old_messages(cutoff)
        db_man.purge_old_channel_messages(cutoff)


async def _sweep_blocklist_chat(bot: Bot, chat_id: int) -> int:
    """Ban every blocklisted user in one chat (e.g. after the bot joins a new chat).

    Skips owners and users locally unbanned here (ban exceptions), mirroring the join checks.
    A preemptive ban works by user id even for members the Bot API can't enumerate, so this
    catches blocklisted users who were already in the chat before the bot arrived — provided
    the bot has ban rights there. Returns the number of users actually banned.
    """
    banned_count = 0
    for user_id in db_man.get_blocklist():
        if permissions.is_owner(user_id):
            continue
        if db_man.is_ban_exception(chat_id, user_id):
            continue
        try:
            await bot.ban_chat_member(chat_id, user_id)
            banned_count += 1
            _clear_captcha_state(chat_id, user_id)
        except Exception:
            pass
    for channel_id in db_man.get_channel_blocklist():
        if db_man.is_channel_ban_exception(chat_id, channel_id):
            continue
        try:
            await bot.ban_chat_sender_chat(chat_id, channel_id)
            banned_count += 1
        except Exception:
            pass
    return banned_count


class UserTrackingMiddleware(BaseMiddleware):
    """Records username → user_id for everyone the bot sees, so @username can be resolved later,
    and enforces the global blocklist on existing members who were never seen joining.

    The Bot API cannot turn a plain @username of a regular user into an id, nor can it enumerate
    a group's members when the bot is added. Both are solved by observing messages (privacy mode
    must be disabled): we learn usernames, and we can ban a blocklisted user as soon as they speak.
    """

    async def __call__(
        self,
        handler: Callable[[types.Message, dict[str, Any]], Awaitable[Any]],
        event: types.Message,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        if user:
            entry = (user.username, user.full_name)
            if _seen_cache.get(user.id) != entry:
                db_man.remember_user(user.id, user.username, user.full_name)
                _seen_cache[user.id] = entry

        text = event.text or ''
        if text.startswith('/') and user and not permissions.is_owner(user.id) and db_man.is_command_banned(user.id):
            cmd = text[1:].split(maxsplit=1)[0].split('@')[0].lower()
            if cmd != 'start':
                return

        if (
            text.startswith('/')
            and user
            and event.chat
            and event.chat.type in _GROUP_TYPES
            and not permissions.is_staff(db_man, event.chat.id, user.id)
        ):
            remaining = db_man.cooldown_remaining(event.chat.id, user.id, _PLAIN_USER_CMD_KEY, _PLAIN_USER_CMD_COOLDOWN)
            if remaining > 0:
                return
            db_man.record_cooldown_use(event.chat.id, user.id, _PLAIN_USER_CMD_KEY)

        if event.chat and event.chat.type in _GROUP_TYPES:
            if (event.new_chat_members or event.left_chat_member) and db_man.is_delete_system_messages(event.chat.id):
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if db_man.remember_chat(event.chat.id):
                _spawn(_sweep_blocklist_chat(event.bot, event.chat.id))
            if event.sender_chat and event.sender_chat.type == 'channel':
                db_man.log_channel_message(event.chat.id, event.sender_chat.id, event.message_id, db_man.unix_time())
            elif user:
                db_man.log_message(event.chat.id, user.id, event.message_id, db_man.unix_time())
            if (
                user
                and not permissions.is_owner(user.id)
                and db_man.is_blocklisted(user.id)
                and not db_man.is_ban_exception(event.chat.id, user.id)
            ):
                try:
                    await event.bot.ban_chat_member(event.chat.id, user.id)
                    _clear_captcha_state(event.chat.id, user.id)
                except Exception:
                    pass
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if (
                user
                and not permissions.is_owner(user.id)
                and db_man.is_locally_banned(event.chat.id, user.id)
            ):
                try:
                    await event.bot.ban_chat_member(event.chat.id, user.id)
                    _clear_captcha_state(event.chat.id, user.id)
                except Exception:
                    pass
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if user and not permissions.is_owner(user.id):
                muted, until = db_man.effective_mute(event.chat.id, user.id)
                if muted:
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    try:
                        await event.bot.restrict_chat_member(
                            event.chat.id, user.id,
                            permissions=_MUTED_PERMS,
                            until_date=until or None,
                        )
                    except Exception:
                        pass
                    return

            await _check_antispam(event, user)

            sender_chat = event.sender_chat
            if (
                sender_chat
                and sender_chat.type == 'channel'
                and db_man.is_channel_blocklisted(sender_chat.id)
                and not db_man.is_channel_ban_exception(event.chat.id, sender_chat.id)
            ):
                try:
                    await event.bot.ban_chat_sender_chat(event.chat.id, sender_chat.id)
                except Exception:
                    pass
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if (
                sender_chat
                and sender_chat.type == 'channel'
                and not event.is_automatic_forward
                and db_man.is_channels_banned(event.chat.id)
            ):
                try:
                    await event.bot.ban_chat_sender_chat(event.chat.id, sender_chat.id)
                except Exception:
                    pass
                try:
                    await event.delete()
                except Exception:
                    pass
                try:
                    await _isend(event.bot, event.chat.id, translator.get_string('channels_forbidden'))
                except Exception:
                    pass
                return

            text = event.text or ''
            if text.startswith('/') and db_man.is_chat_stopped(event.chat.id):
                cmd = text[1:].split(maxsplit=1)[0].split('@')[0].lower()
                if not (cmd in ('startchat', 'stopchat') and user and permissions.is_owner(user.id)):
                    return
        return await handler(event, data)


_PLAIN_USER_CMD_COOLDOWN = 10
"""Flat per-user command cooldown (seconds) for chat members with no role — moderators,
admins and owners are exempt (permissions.is_staff). Separate from the per-command,
admin-configurable cooldowns table (set_cooldown/get_cooldown), which only ever applied to
moderators; this one is hardcoded and applies to everyone else instead."""

_PLAIN_USER_CMD_KEY = '__any__'
"""Pseudo-command name reusing the existing cooldown_use table for the check above — picked
to never collide with a real command name."""


_ZERO_WIDTH_CHARS = frozenset('​‌‍⁠﻿᠎')
_BIDI_CONTROL_CHARS = frozenset('‪‫‬‭‮⁦⁧⁨⁩')
_ZALGO_MAX_STACK = 4


def _has_suspicious_unicode(text: str) -> bool:
    """True if text carries invisible/zero-width characters, bidi override/embedding/isolate
    control characters (direction-spoofing, e.g. hiding a malicious file extension or link), or
    zalgo (an abnormal stack of combining diacritical marks on one base character). Ordinary text
    in any language — Arabic/Hebrew/Vietnamese etc. with 1-2 diacritics per letter — never
    triggers this: LRM/RLM (U+200E/U+200F, common and legitimate in mixed bidi text) are
    deliberately excluded from _BIDI_CONTROL_CHARS; only the actual direction-override/embedding/
    isolate characters are treated as suspicious. 'Fancy font' unicode blocks (bold/gothic/etc.)
    are intentionally not covered — too easy to false-positive on normal stylized text."""
    if any(ch in _ZERO_WIDTH_CHARS for ch in text):
        return True
    if any(ch in _BIDI_CONTROL_CHARS for ch in text):
        return True
    stack = 0
    for ch in text:
        if unicodedata.combining(ch):
            stack += 1
            if stack > _ZALGO_MAX_STACK:
                return True
        else:
            stack = 0
    return False


def _antispam_signature(message: types.Message) -> str | None:
    """A comparable signature for repeat-spam detection, or None for unsupported content types
    (polls, contacts, locations, service messages, ...) — those never count as a duplicate and
    always break a streak. Media is compared by file_unique_id (the same underlying file), text
    by literal content; a caption is folded into a media signature so the same file with a
    different caption isn't silently treated as identical."""
    if message.text is not None:
        text = message.text.strip()
        return f'text:{text}' if text else None
    for attr in ('sticker', 'animation', 'video', 'video_note', 'voice', 'document', 'audio'):
        obj = getattr(message, attr, None)
        if obj is not None:
            caption = (message.caption or '').strip()
            return f'{attr}:{obj.file_unique_id}:{caption}'
    if message.photo:
        caption = (message.caption or '').strip()
        return f'photo:{message.photo[-1].file_unique_id}:{caption}'
    return None


async def _fire_antispam(event: types.Message, kind: str, target: types.User | types.Chat,
                         message_ids: list[int], settings: dict, elapsed_seconds: int) -> None:
    """A repeat-spam streak just crossed the threshold: delete every message but the last,
    punish the sender, announce it in the chat, log it, and (if enabled) alert staff/owners in
    DM with the last message forwarded — same broadcast mechanism as /report. `kind` is 'user'
    (target is a types.User, muted) or 'channel' (target is the sender_chat, banned instead —
    Telegram has no way to mute a channel). elapsed_seconds is the actual time from the first of
    these messages to the last, for the DM's "За период" line — not the configured window, which
    is just the cap allowed between them."""
    bot = event.bot
    chat = event.chat
    for mid in message_ids[:-1]:
        try:
            await bot.delete_message(chat.id, mid)
        except Exception:
            pass

    if kind == 'channel':
        try:
            await bot.ban_chat_sender_chat(chat.id, target.id)
        except Exception:
            pass
        announce = translator.get_string('antispam_ban_announce').format(_channel_mention(target))
        label = _chat_info(target)
        target_id = target.id
    else:
        until = int(datetime.now().timestamp()) + settings['mute_seconds']
        try:
            await bot.restrict_chat_member(chat.id, target.id, permissions=_MUTED_PERMS, until_date=until)
            db_man.add_mute(chat.id, target.id, until)
        except Exception:
            pass
        mention = _identity_html(target.full_name or '', target.username, target.id)
        dur_words = _seconds_to_words(settings['mute_seconds'])
        announce = translator.get_string('antispam_mute_announce').format(mention, dur_words)
        label = _full_user_info(target)
        target_id = target.id

    try:
        await _isend_html(bot, chat.id, announce)
    except Exception:
        pass

    db_man.add_log(chat.id, f'🚨 {translator.get_string("log_antispam")} → {label}', target_id, 'log_antispam')

    if not settings['notify']:
        return
    lines = [
        translator.get_string('antispam_alert_header'),
        translator.get_string('report_time').format(datetime.now().strftime('%d.%m.%Y %H:%M:%S')),
        translator.get_string('report_chat').format(chat.title or str(chat.id)),
        translator.get_string('report_from').format(label),
        translator.get_string('antispam_alert_count').format(len(message_ids)),
        translator.get_string('antispam_alert_period').format(_human_elapsed(elapsed_seconds)),
    ]
    link = _message_link(chat, message_ids[-1])
    if link:
        lines.append(translator.get_string('antispam_alert_link').format(link))
    notice = '\n'.join(lines)
    for uid in _report_recipients(chat.id, 0):
        try:
            await bot.send_message(uid, notice)
            await bot.forward_message(uid, chat.id, message_ids[-1])
        except Exception:
            pass


async def _check_antispam(event: types.Message, user: types.User | None) -> None:
    """Repeat-spam detection: strictly consecutive identical messages within a configurable
    window trigger _fire_antispam once the configurable threshold is reached.

    Three sender shapes to tell apart (see CLAUDE.md 'Channels vs anonymous admins vs users'):
    a real channel post (sender_chat.type == 'channel') is tracked/punished by channel id — it
    can flood a chat without being a group admin, and from_user is always the same generic
    @Channel_Bot regardless of which channel posted, so tracking by user id would both miss it
    and wrongly merge unrelated channels' streaks together. An anonymous group admin (sender_chat
    set, but not a channel) and a linked channel's own auto-forwarded post are both exempt —
    staff and non-spam respectively. Otherwise it's a plain user, tracked/punished by user id,
    staff/owners exempt.

    Also runs the (separately toggleable) suspicious-unicode filter first — invisible characters,
    bidi direction-spoofing, zalgo — which deletes the message (no mute/ban, just a staff-log
    entry) and breaks any in-progress repeat streak, since it never reaches the signature check
    below.
    """
    if not config.ANTISPAM_ENABLED:
        return
    chat_id = event.chat.id
    settings = db_man.get_antispam_settings(chat_id)
    if not settings['enabled']:
        return

    sender_chat = event.sender_chat
    if sender_chat and sender_chat.type != 'channel':
        return
    if sender_chat and sender_chat.type == 'channel':
        if event.is_automatic_forward:
            return
        kind, target, target_key = 'channel', sender_chat, sender_chat.id
    elif user:
        if permissions.is_staff(db_man, chat_id, user.id):
            return
        kind, target, target_key = 'user', user, user.id
    else:
        return

    if settings['unicode_guard'] and _has_suspicious_unicode((event.text or '') + (event.caption or '')):
        try:
            await event.delete()
        except Exception:
            pass
        label = _chat_info(target) if kind == 'channel' else _full_user_info(target)
        db_man.add_log(chat_id, f'🔤 {translator.get_string("log_unicode_delete")} → {label}',
                       target.id, 'log_unicode_delete')
        db_man.clear_antispam_streak(chat_id, target_key)
        return

    sig = _antispam_signature(event)
    if sig is None:
        db_man.clear_antispam_streak(chat_id, target_key)
        return

    now = db_man.unix_time()
    streak = db_man.get_antispam_streak(chat_id, target_key)
    if streak and streak['signature'] == sig and now - streak['first_ts'] <= settings['window']:
        count = streak['count'] + 1
        first_ts = streak['first_ts']
        message_ids = streak['message_ids'] + [event.message_id]
    else:
        count = 1
        first_ts = now
        message_ids = [event.message_id]

    if count >= settings['count']:
        db_man.clear_antispam_streak(chat_id, target_key)
        await _fire_antispam(event, kind, target, message_ids, settings, now - first_ts)
    else:
        db_man.set_antispam_streak(chat_id, target_key, sig, count, first_ts, message_ids)


@router.my_chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def handle_bot_added(event: ChatMemberUpdated, bot: Bot) -> None:
    """When the bot is added to a group, remember the chat, make the chat creator an admin,
    and immediately enforce the global blocklist there (in case banned users are already in)."""
    if event.chat.type not in _GROUP_TYPES:
        return
    db_man.remember_chat(event.chat.id)
    await _sweep_blocklist_chat(bot, event.chat.id)
    try:
        admins = await bot.get_chat_administrators(event.chat.id)
    except Exception:
        return
    for member in admins:
        if member.status == 'creator':
            db_man.set_role(event.chat.id, member.user.id, 'admin')
            break


@router.my_chat_member(ChatMemberUpdatedFilter(MEMBER >> ADMINISTRATOR))
async def handle_bot_promoted(event: ChatMemberUpdated, bot: Bot) -> None:
    """When the bot is promoted to administrator, it finally has ban rights — re-run the
    blocklist sweep, since the sweep at add-time was a no-op without those rights."""
    if event.chat.type not in _GROUP_TYPES:
        return
    db_man.remember_chat(event.chat.id)
    await _sweep_blocklist_chat(bot, event.chat.id)


@router.my_chat_member(ChatMemberUpdatedFilter(IS_MEMBER >> IS_NOT_MEMBER))
async def handle_bot_removed(event: ChatMemberUpdated, bot: Bot) -> None:
    """When the bot is removed from a chat, stop tracking it for global bans."""
    db_man.forget_chat(event.chat.id)
