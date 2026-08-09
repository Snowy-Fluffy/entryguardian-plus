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

import asyncio
import re
from aiogram import types, Bot
from aiogram.filters import Command, CommandObject
import permissions
from .common import (
    router, db_man, translator,
    _delete_silently, _ianswer, _ianswer_html,
    _require, _cooldown_guard, _cooldown_mark,
    _user_label, _log_action, _record_log,
    _hierarchy_ok, _is_bot_target, _parse_ban, _punish_labels,
    _reply_channel, _channel_mention,
    _parse_duration, _human_duration_words,
)
from .middleware import _MESSAGE_RETENTION


@router.message(Command('delete'))
async def delete_message(message: types.Message, bot: Bot) -> None:
    """Delete the message this command replies to (and the command itself)."""
    await _delete_silently(message)
    if not await _require(message, permissions.is_staff(db_man, message.chat.id, message.from_user.id)):
        return
    if not await _cooldown_guard(message, 'delete'):
        return
    target = message.reply_to_message
    if target is None:
        await _ianswer(message, translator.get_string('delete_need_reply'))
        return
    if target.from_user and not await _hierarchy_ok(message, target.from_user.id):
        return
    try:
        await bot.delete_message(message.chat.id, target.message_id)
    except Exception:
        await _ianswer(message, translator.get_string('delete_failed'))
        return
    label = _user_label(target.from_user) if target.from_user else translator.get_string('staff_none')
    _log_action(message, 'log_delete', label, '', target.from_user.id if target.from_user else None)
    _cooldown_mark(message, 'delete')


_COUNT_RE = re.compile(r'^c(\d+)$', re.IGNORECASE)
_DELETE_ALL_CHUNK = 100
_DELETE_ALL_CHUNK_DELAY = 1.0


def _parse_delete_modifier(token: str):
    """Return (since_ts | None, limit | None, dur_token | None, error_key | None) for a single
    count (`c200`) or period (`1h`, `1d`, ...) modifier token. No token means 'everything stored'
    (bounded by the 2-day retention window anyway). `dur_token` carries the raw duration token
    when `since` is set, so the confirmation can spell it out.
    """
    if not token:
        return None, None, None, None
    count_match = _COUNT_RE.match(token)
    if count_match:
        return None, int(count_match.group(1)), None, None
    seconds = _parse_duration(token)
    if seconds is not None:
        if seconds > _MESSAGE_RETENTION:
            return None, None, None, 'delmsg_period_too_long'
        return db_man.unix_time() - seconds, None, token, None
    return None, None, None, 'delmsg_bad_arg'


async def _parse_delete_user(message: types.Message, command: CommandObject, bot: Bot):
    """Return (target_id, since_ts | None, limit | None, dur_token | None, error_key | None)."""
    target_id, rest = await _parse_ban(message, command, bot)
    if target_id is None:
        return None, None, None, None, None
    token = rest.strip().split()[0] if rest.strip() else ''
    since, limit, dur_token, error_key = _parse_delete_modifier(token)
    return target_id, since, limit, dur_token, error_key


async def _delete_chunk(bot: Bot, chat_id: int, message_ids: list[int]) -> bool:
    """Delete up to 100 messages in one call; retries once on a flood-control Retry-After."""
    try:
        await bot.delete_messages(chat_id, message_ids)
        return True
    except Exception as e:
        retry_after = int(getattr(e, 'retry_after', 0) or 0)
        if not retry_after:
            return False
        await asyncio.sleep(retry_after + 1)
        try:
            await bot.delete_messages(chat_id, message_ids)
            return True
        except Exception:
            return False


async def _delete_message_batch(bot: Bot, chat_id: int, message_ids: list[int], remove) -> int:
    """Delete tracked messages in chunks of 100, throttled; `remove(chunk)` drops the chunk's
    rows once successfully deleted. Returns how many were processed."""
    deleted = 0
    for i in range(0, len(message_ids), _DELETE_ALL_CHUNK):
        chunk = message_ids[i:i + _DELETE_ALL_CHUNK]
        if await _delete_chunk(bot, chat_id, chunk):
            remove(chunk)
            deleted += len(chunk)
        if i + _DELETE_ALL_CHUNK < len(message_ids):
            await asyncio.sleep(_DELETE_ALL_CHUNK_DELAY)
    return deleted


def _delete_done_text(kind: str, deleted: int, limit, since, dur_token, target_html: str | None) -> str:
    """Build the confirmation text for a finished bulk deletion. `kind` is 'user' or 'chat'."""
    if limit is not None:
        key = f'delete{kind}_done_count'
        return translator.get_string(key).format(deleted, target_html) if target_html else translator.get_string(key).format(deleted)
    if since is not None:
        dur_words = _human_duration_words(dur_token)
        key = f'delete{kind}_done_period'
        return (translator.get_string(key).format(deleted, dur_words, target_html) if target_html
                else translator.get_string(key).format(deleted, dur_words))
    key = f'delete{kind}_done_all'
    return translator.get_string(key).format(deleted, target_html) if target_html else translator.get_string(key).format(deleted)


async def _run_delete_user_channel(message: types.Message, command: CommandObject, bot: Bot,
                                    channel: types.Chat, *, silent: bool) -> bool:
    """Bulk-delete a channel's tracked messages in this chat (reply-only, like channel bans)."""
    token = (command.args or '').strip().split()[0] if (command.args or '').strip() else ''
    since, limit, dur_token, error_key = _parse_delete_modifier(token)
    if error_key:
        await _ianswer(message, translator.get_string(error_key))
        return False

    db_man.flush_channel_messages()
    message_ids = db_man.get_recent_channel_messages(message.chat.id, channel.id, since=since, limit=limit)
    if not message_ids:
        if not silent:
            await _ianswer(message, translator.get_string('deleteuser_none'))
        return False

    deleted = await _delete_message_batch(
        bot, message.chat.id, message_ids,
        lambda chunk: db_man.remove_channel_messages(message.chat.id, channel.id, chunk))

    channel_html = _channel_mention(channel)
    suffix = translator.get_string('delmsg_log_suffix').format(deleted)
    log_key = 'log_sdelete_user' if silent else 'log_delete_user'
    _record_log(message.chat.id, message.from_user, log_key, (channel.title or str(channel.id)) + suffix, '', channel.id)

    if not silent:
        await _ianswer_html(message, _delete_done_text('user', deleted, limit, since, dur_token, channel_html))
    return True


async def _run_delete_user(message: types.Message, command: CommandObject, bot: Bot, *, silent: bool) -> bool:
    """Shared implementation for /delete_user and /sdelete_user. Returns True if a deletion
    attempt was actually made (for cooldown bookkeeping)."""
    channel = _reply_channel(message)
    if channel is not None:
        return await _run_delete_user_channel(message, command, bot, channel, silent=silent)

    target_id, since, limit, dur_token, error_key = await _parse_delete_user(message, command, bot)
    if target_id is None:
        provided = bool(message.reply_to_message) or bool((command.args or '').strip())
        await _ianswer(message, translator.get_string('mod_user_not_found' if provided else 'mod_specify_user'))
        return False
    if error_key:
        await _ianswer(message, translator.get_string(error_key))
        return False
    if await _is_bot_target(message, bot, target_id):
        return False
    if not await _hierarchy_ok(message, target_id):
        return False

    db_man.flush_messages()
    message_ids = db_man.get_recent_messages(message.chat.id, target_id, since=since, limit=limit)
    if not message_ids:
        if not silent:
            await _ianswer(message, translator.get_string('deleteuser_none'))
        return False

    deleted = await _delete_message_batch(
        bot, message.chat.id, message_ids,
        lambda chunk: db_man.remove_messages(message.chat.id, target_id, chunk))

    target_html, target_label = await _punish_labels(bot, message, command, target_id)
    suffix = translator.get_string('delmsg_log_suffix').format(deleted)
    log_key = 'log_sdelete_user' if silent else 'log_delete_user'
    _log_action(message, log_key, target_label + suffix, '', target_id)

    if not silent:
        await _ianswer_html(message, _delete_done_text('user', deleted, limit, since, dur_token, target_html))
    return True


async def _run_delete_chat(message: types.Message, bot: Bot, command: CommandObject, *, silent: bool) -> bool:
    """Shared implementation for /delete_chat and /sdelete_chat: bulk-delete tracked messages
    in this chat regardless of who sent them (users and channels alike)."""
    token = (command.args or '').strip().split()[0] if (command.args or '').strip() else ''
    since, limit, dur_token, error_key = _parse_delete_modifier(token)
    if error_key:
        await _ianswer(message, translator.get_string(error_key))
        return False

    db_man.flush_messages()
    db_man.flush_channel_messages()
    message_ids = db_man.get_recent_chat_messages(message.chat.id, since=since, limit=limit)
    if not message_ids:
        if not silent:
            await _ianswer(message, translator.get_string('deletechat_none'))
        return False

    deleted = await _delete_message_batch(
        bot, message.chat.id, message_ids,
        lambda chunk: db_man.remove_chat_messages(message.chat.id, chunk))

    suffix = translator.get_string('delmsg_log_suffix').format(deleted)
    log_key = 'log_sdelete_chat' if silent else 'log_delete_chat'
    _record_log(message.chat.id, message.from_user, log_key, (message.chat.title or str(message.chat.id)) + suffix, '')

    if not silent:
        await _ianswer_html(message, _delete_done_text('chat', deleted, limit, since, dur_token, None))
    return True


@router.message(Command('delete_user'))
async def delete_user_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Bulk-delete a user's (or, by reply to a channel post, a channel's) tracked messages in
    this chat: all of them, the last N (`c200`), or everything from the last period (`1h`, `1d`,
    ...). Moderators and up."""
    await _delete_silently(message)
    if not await _require(message, permissions.is_staff(db_man, message.chat.id, message.from_user.id)):
        return
    if not await _cooldown_guard(message, 'delete_user'):
        return
    if await _run_delete_user(message, command, bot, silent=False):
        _cooldown_mark(message, 'delete_user')


@router.message(Command('sdelete_user'))
async def sdelete_user_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Silent variant of /delete_user: same bulk deletion, nothing posted to the chat. Admins and up."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    await _run_delete_user(message, command, bot, silent=True)


@router.message(Command('delete_chat'))
async def delete_chat_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Bulk-delete tracked messages in this chat, from anyone: all of them, the last N (`c200`),
    or everything from the last period (`1h`, `1d`, ...). Admins and up."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    await _run_delete_chat(message, bot, command, silent=False)


@router.message(Command('sdelete_chat'))
async def sdelete_chat_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Silent variant of /delete_chat: same bulk deletion, nothing posted to the chat. Admins and up."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    await _run_delete_chat(message, bot, command, silent=True)
