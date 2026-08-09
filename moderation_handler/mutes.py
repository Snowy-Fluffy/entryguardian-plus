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

from datetime import datetime
from aiogram import types, Bot
from aiogram.filters import Command, CommandObject
import permissions
from .common import (
    router, db_man, translator, _GROUP_TYPES, _esc,
    _delete_silently, _ianswer, _ianswer_html, _isend_html,
    _require, _require_global, _cooldown_guard, _cooldown_mark,
    _reply_channel, _actor_role_word, _actor_mention,
    _parse_ban, _parse_duration, _human_duration_words,
    _is_bot_target, _hierarchy_ok, _punish_labels,
    _dm_text, _dm_target, _log_global,
    _MUTED_PERMS, build_chat_permissions,
)


async def _parse_mute(message: types.Message, command: CommandObject, bot: Bot) -> tuple[int | None, int | None, str, str]:
    """Return (target_id, duration_seconds | None, duration_text, reason)."""
    target_id, reason = await _parse_ban(message, command, bot)
    dur_seconds, dur_text = None, ''
    if reason:
        head, _, tail = reason.partition(' ')
        seconds = _parse_duration(head)
        if seconds is not None:
            dur_seconds, dur_text, reason = seconds, head, tail.strip()
    return target_id, dur_seconds, dur_text, reason


async def _apply_mute(bot: Bot, chat_id: int, user_id: int, dur_seconds: int | None) -> None:
    until = int(datetime.now().timestamp()) + dur_seconds if dur_seconds else 0
    await bot.restrict_chat_member(chat_id, user_id, permissions=_MUTED_PERMS, until_date=until or None)
    db_man.add_mute(chat_id, user_id, until)


async def _apply_unmute(bot: Bot, chat_id: int, user_id: int) -> None:
    db_man.remove_mute(chat_id, user_id)
    await bot.restrict_chat_member(chat_id, user_id, permissions=build_chat_permissions(chat_id))


def _with_extras(text: str, dur_words: str, reason: str) -> str:
    if dur_words:
        text += translator.get_string('mute_for').format(dur_words)
    if reason:
        text += ', ' + translator.get_string('log_reason').format(_esc(reason))
    return text


def _mute_text(message: types.Message, muted: str, dur_words: str, reason: str) -> str:
    """Local mute announcement. `muted` is HTML (a mention)."""
    base = translator.get_string('mute_announce').format(
        muted, _actor_role_word(message.chat.id, message.from_user.id), _actor_mention(message.from_user))
    return _with_extras(base, dur_words, reason)


def _mute_remote_text(muted: str, source_title: str, dur_words: str, reason: str) -> str:
    return _with_extras(translator.get_string('mute_announce_remote').format(muted, _esc(source_title)), dur_words, reason)


def _global_mute_text(muted: str, dur_words: str, reason: str) -> str:
    return _with_extras(translator.get_string('mute_global').format(muted), dur_words, reason)


def _global_unmute_text(target: str, reason: str) -> str:
    return _with_extras(translator.get_string('unmute_global').format(target), '', reason)


def _unmute_text(message: types.Message, target: str, reason: str) -> str:
    """Local unmute announcement. `target` is HTML (a mention)."""
    base = translator.get_string('unmute_announce').format(
        target, _actor_role_word(message.chat.id, message.from_user.id), _actor_mention(message.from_user))
    return _with_extras(base, '', reason)


def _unmute_remote_text(target: str, source_title: str, reason: str) -> str:
    return _with_extras(translator.get_string('unmute_announce_remote').format(target, _esc(source_title)), '', reason)


async def _run_mute(message: types.Message, command: CommandObject, bot: Bot, *, glob: bool, silent: bool, log_key: str) -> bool:
    if _reply_channel(message) is not None:
        await _ianswer(message, translator.get_string('cannot_mute_channel'))
        return False
    target_id, dur_seconds, dur_text, reason = await _parse_mute(message, command, bot)
    if target_id is None:
        provided = bool(message.reply_to_message) or bool((command.args or '').strip())
        await _ianswer(message, translator.get_string('mod_user_not_found' if provided else 'mod_specify_user'))
        return False
    if dur_text.endswith('s'):
        await _ianswer(message, translator.get_string('mute_failed'))
        return False
    if await _is_bot_target(message, bot, target_id):
        return False
    if permissions.is_owner(target_id):
        await _ianswer(message, translator.get_string('mod_cannot_target_owner'))
        return False
    if not await _hierarchy_ok(message, target_id):
        return False

    muted_html, muted = await _punish_labels(bot, message, command, target_id)
    label = f'{muted} [{dur_text}]' if dur_text else muted
    dur_words = _human_duration_words(dur_text) if dur_text else ''
    is_dm = message.chat.type not in _GROUP_TYPES

    if glob:
        db_man.set_global_mute(target_id, int(datetime.now().timestamp()) + dur_seconds if dur_seconds else 0)
        local_text = _mute_text(message, muted_html, dur_words, reason)
        remote_text = _mute_remote_text(muted_html, message.chat.title or str(message.chat.id), dur_words, reason)
        global_text = _global_mute_text(muted_html, dur_words, reason)
        chat_ids = set(db_man.get_bot_chats())
        if not is_dm:
            chat_ids.add(message.chat.id)
        for chat_id in chat_ids:
            try:
                await _apply_mute(bot, chat_id, target_id, dur_seconds)
            except Exception:
                pass
            if not silent:
                try:
                    await _isend_html(bot, chat_id, global_text if is_dm else (local_text if chat_id == message.chat.id else remote_text))
                except Exception:
                    pass
        if not silent and is_dm:
            await _isend_html(bot, message.chat.id, global_text)
    else:
        try:
            await _apply_mute(bot, message.chat.id, target_id, dur_seconds)
        except Exception:
            await _ianswer(message, translator.get_string('mute_failed'))
            return False
        if not silent:
            await _ianswer_html(message, _mute_text(message, muted_html, dur_words, reason))

    if not silent:
        if glob and is_dm:
            dm_key = 'dm_muted_global_nosrc'
        elif glob:
            dm_key = 'dm_muted_global'
        else:
            dm_key = 'dm_muted'
        await _dm_target(bot, target_id, _dm_text(dm_key, message.chat.title or str(message.chat.id), message, reason, dur_words))
    _log_global(message, log_key, label, reason, target_id)
    return True


async def _run_unmute(message: types.Message, command: CommandObject, bot: Bot, *, glob: bool, silent: bool, log_key: str) -> bool:
    if _reply_channel(message) is not None:
        await _ianswer(message, translator.get_string('cannot_mute_channel'))
        return False
    target_id, reason = await _parse_ban(message, command, bot)
    if target_id is None:
        provided = bool(message.reply_to_message) or bool((command.args or '').strip())
        await _ianswer(message, translator.get_string('mod_user_not_found' if provided else 'mod_specify_user'))
        return False
    if not await _hierarchy_ok(message, target_id):
        return False

    target_html, target = await _punish_labels(bot, message, command, target_id)
    is_dm = message.chat.type not in _GROUP_TYPES

    if glob:
        db_man.remove_global_mute(target_id)
        local_text = _unmute_text(message, target_html, reason)
        remote_text = _unmute_remote_text(target_html, message.chat.title or str(message.chat.id), reason)
        global_text = _global_unmute_text(target_html, reason)
        chat_ids = set(db_man.get_bot_chats())
        if not is_dm:
            chat_ids.add(message.chat.id)
        for chat_id in chat_ids:
            try:
                await _apply_unmute(bot, chat_id, target_id)
            except Exception:
                pass
            if not silent:
                try:
                    await _isend_html(bot, chat_id, global_text if is_dm else (local_text if chat_id == message.chat.id else remote_text))
                except Exception:
                    pass
        if not silent and is_dm:
            await _isend_html(bot, message.chat.id, global_text)
    else:
        try:
            await _apply_unmute(bot, message.chat.id, target_id)
        except Exception:
            pass
        if not silent:
            await _ianswer_html(message, _unmute_text(message, target_html, reason))

    if not silent:
        if glob and is_dm:
            dm_key = 'dm_unmuted_global_nosrc'
        elif glob:
            dm_key = 'dm_unmuted_global'
        else:
            dm_key = 'dm_unmuted'
        await _dm_target(bot, target_id, _dm_text(dm_key, message.chat.title or str(message.chat.id), message, reason))
    _log_global(message, log_key, target, reason, target_id)
    return True


@router.message(Command('mute'))
async def mute_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if not await _require(message, permissions.is_staff(db_man, message.chat.id, message.from_user.id)):
        return
    if not await _cooldown_guard(message, 'mute'):
        return
    if await _run_mute(message, command, bot, glob=False, silent=False, log_key='log_mute'):
        _cooldown_mark(message, 'mute')


@router.message(Command('gmute'))
async def gmute_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if await _require_global(message):
        await _run_mute(message, command, bot, glob=True, silent=False, log_key='log_gmute')


@router.message(Command('smute'))
async def smute_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        await _run_mute(message, command, bot, glob=False, silent=True, log_key='log_smute')


@router.message(Command('gsmute'))
async def gsmute_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if await _require_global(message):
        await _run_mute(message, command, bot, glob=True, silent=True, log_key='log_gsmute')


@router.message(Command('unmute'))
async def unmute_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if not await _require(message, permissions.is_staff(db_man, message.chat.id, message.from_user.id)):
        return
    if not await _cooldown_guard(message, 'unmute'):
        return
    if await _run_unmute(message, command, bot, glob=False, silent=False, log_key='log_unmute'):
        _cooldown_mark(message, 'unmute')


@router.message(Command('unsmute'))
async def unsmute_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        await _run_unmute(message, command, bot, glob=False, silent=True, log_key='log_unsmute')


@router.message(Command('ungmute'))
async def ungmute_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if await _require_global(message):
        await _run_unmute(message, command, bot, glob=True, silent=False, log_key='log_ungmute')


@router.message(Command('ungsmute'))
async def ungsmute_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if await _require_global(message):
        await _run_unmute(message, command, bot, glob=True, silent=True, log_key='log_ungsmute')
