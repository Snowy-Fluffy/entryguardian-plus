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

from aiogram import types, Bot
from aiogram.filters import Command, CommandObject
import permissions
from .common import (
    router, db_man, translator, _GROUP_TYPES,
    _delete_silently, _ianswer, _ianswer_html, _isend_html, _esc,
    _require, _require_global, _cooldown_guard, _cooldown_mark,
    _reply_channel, _channel_mention, _actor_role_word, _actor_mention, _user_label,
    _record_log, _log_action, _log_global,
    _clear_captcha_state, _clear_captcha_state_everywhere,
    _ban_target_or_reply, _punish_labels, _hierarchy_ok, _get_target_or_reply,
    _dm_text, _dm_target,
)


async def _global_ban(bot: Bot, target_id: int) -> None:
    """Blocklist the user (so they're banned on any future join) and ban them in every known chat."""
    db_man.add_to_blocklist(target_id)
    db_man.clear_ban_exceptions(target_id)
    for chat_id in db_man.get_bot_chats():
        try:
            await bot.ban_chat_member(chat_id, target_id)
        except Exception:
            pass
    _clear_captcha_state_everywhere(target_id)


def _local_ban_text(message: types.Message, banned: str, reason: str) -> str:
    """Announcement for the chat where the command was issued. `banned` is HTML (a mention)."""
    role_word = _actor_role_word(message.chat.id, message.from_user.id)
    actor = _actor_mention(message.from_user)
    if reason:
        return translator.get_string('ban_announce').format(banned, role_word, actor, _esc(reason))
    return translator.get_string('ban_announce_no_reason').format(banned, role_word, actor)


def _remote_ban_text(banned: str, source_title: str, reason: str) -> str:
    """Announcement for the other chats, naming the chat the ban originated from. `banned` is HTML."""
    if reason:
        return translator.get_string('ban_announce_remote').format(banned, _esc(source_title), _esc(reason))
    return translator.get_string('ban_announce_remote_no_reason').format(banned, _esc(source_title))


def _global_ban_text(banned: str, reason: str) -> str:
    if reason:
        return translator.get_string('ban_global').format(banned, _esc(reason))
    return translator.get_string('ban_global_no_reason').format(banned)


def _global_unban_text(name: str) -> str:
    return translator.get_string('unban_global').format(name)


async def _announce_ban(message: types.Message, banned: str, reason: str) -> None:
    await _ianswer_html(message, _local_ban_text(message, banned, reason))


async def _maybe_channel_ban(message: types.Message, command: CommandObject, bot: Bot,
                             *, glob: bool, silent: bool, log_key: str) -> bool:
    """If the command replies to a channel's message, ban that channel sender. Returns True if handled.

    Local bans hit only this chat; global bans also blocklist the channel and ban it everywhere.
    """
    channel = _reply_channel(message)
    if channel is None:
        return False
    reason = (command.args or '').strip()
    mention = _channel_mention(channel)
    title = channel.title or str(channel.id)

    if glob:
        db_man.add_channel_to_blocklist(channel.id)
        db_man.clear_channel_ban_exceptions(channel.id)
        chat_ids = set(db_man.get_bot_chats())
        chat_ids.add(message.chat.id)
    else:
        chat_ids = {message.chat.id}

    source_title = message.chat.title or str(message.chat.id)
    local_text = _local_ban_text(message, mention, reason)
    remote_text = _remote_ban_text(mention, source_title, reason)
    for chat_id in chat_ids:
        try:
            await bot.ban_chat_sender_chat(chat_id, channel.id)
        except Exception:
            pass
        if not silent:
            try:
                await _isend_html(bot, chat_id, local_text if chat_id == message.chat.id else remote_text)
            except Exception:
                pass
    _record_log(message.chat.id, message.from_user, log_key, title, reason, channel.id)
    return True


async def _maybe_channel_unban(message: types.Message, bot: Bot,
                               *, glob: bool, silent: bool, log_key: str) -> bool:
    """If the command replies to a channel's message, unban that channel sender. Returns True if handled."""
    channel = _reply_channel(message)
    if channel is None:
        return False
    mention = _channel_mention(channel)
    title = channel.title or str(channel.id)

    if glob:
        db_man.remove_channel_from_blocklist(channel.id)
        db_man.clear_channel_ban_exceptions(channel.id)
        chat_ids = set(db_man.get_bot_chats())
        chat_ids.add(message.chat.id)
    else:
        db_man.add_channel_ban_exception(message.chat.id, channel.id)
        chat_ids = {message.chat.id}

    for chat_id in chat_ids:
        try:
            await bot.unban_chat_sender_chat(chat_id, channel.id)
        except Exception:
            pass
    _record_log(message.chat.id, message.from_user, log_key, title, '', channel.id)

    if not silent:
        role_word = _actor_role_word(message.chat.id, message.from_user.id)
        actor = _actor_mention(message.from_user)
        if glob:
            source_title = message.chat.title or str(message.chat.id)
            local_text = translator.get_string('ungban_announce').format(mention, role_word, actor)
            remote_text = translator.get_string('ungban_announce_remote').format(mention, _esc(source_title))
            for chat_id in chat_ids:
                try:
                    await _isend_html(bot, chat_id, local_text if chat_id == message.chat.id else remote_text)
                except Exception:
                    pass
        else:
            await _ianswer_html(message, translator.get_string('unban_announce').format(mention, role_word, actor))
    return True


@router.message(Command('gban'))
async def gban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Global ban across every chat the bot is in, announced in all of them. Admins and up."""
    await _delete_silently(message)
    if not await _require_global(message):
        return
    is_dm = message.chat.type not in _GROUP_TYPES
    if not is_dm and await _maybe_channel_ban(message, command, bot, glob=True, silent=False, log_key='log_ban'):
        return
    target_id, reason = await _ban_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if permissions.is_owner(target_id):
        await _ianswer(message, translator.get_string('mod_cannot_target_owner'))
        return

    banned_html, banned = await _punish_labels(bot, message, command, target_id)
    source_title = message.chat.title or str(message.chat.id)
    local_text = _local_ban_text(message, banned_html, reason)
    remote_text = _remote_ban_text(banned_html, source_title, reason)
    global_text = _global_ban_text(banned_html, reason)

    db_man.add_to_blocklist(target_id)
    chat_ids = set(db_man.get_bot_chats())
    if not is_dm:
        chat_ids.add(message.chat.id)
    for chat_id in chat_ids:
        try:
            await bot.ban_chat_member(chat_id, target_id)
        except Exception:
            pass
        try:
            await _isend_html(bot, chat_id, global_text if is_dm else (local_text if chat_id == message.chat.id else remote_text))
        except Exception:
            pass
    _clear_captcha_state_everywhere(target_id)
    _log_global(message, 'log_ban', banned, reason, target_id)
    if is_dm:
        await _isend_html(bot, message.chat.id, global_text)
        await _dm_target(bot, target_id, _dm_text('dm_banned_global_nosrc', '', message, reason))
    else:
        await _dm_target(bot, target_id, _dm_text('dm_banned_global', source_title, message, reason))


@router.message(Command('ban'))
async def ban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Local ban: ban only in the current chat, announced there. Moderators and up."""
    await _delete_silently(message)
    if not await _require(message, permissions.is_staff(db_man, message.chat.id, message.from_user.id)):
        return
    if not await _cooldown_guard(message, 'ban'):
        return
    if await _maybe_channel_ban(message, command, bot, glob=False, silent=False, log_key='log_lban'):
        _cooldown_mark(message, 'ban')
        return
    target_id, reason = await _ban_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if permissions.is_owner(target_id):
        await _ianswer(message, translator.get_string('mod_cannot_target_owner'))
        return
    if not await _hierarchy_ok(message, target_id):
        return

    banned_html, banned = await _punish_labels(bot, message, command, target_id)
    try:
        await bot.ban_chat_member(message.chat.id, target_id)
    except Exception:
        await _ianswer(message, translator.get_string('ban_failed'))
        return
    db_man.add_local_ban(message.chat.id, target_id)
    _clear_captcha_state(message.chat.id, target_id)
    _log_action(message, 'log_lban', banned, reason, target_id)
    await _announce_ban(message, banned_html, reason)
    await _dm_target(bot, target_id, _dm_text('dm_banned', message.chat.title or str(message.chat.id), message, reason))
    _cooldown_mark(message, 'ban')


@router.message(Command('sgban'))
async def sgban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Silent global ban: same reach as /gban but nothing is posted to the chat. Admins and up."""
    await _delete_silently(message)
    if not await _require_global(message):
        return
    is_dm = message.chat.type not in _GROUP_TYPES
    if not is_dm and await _maybe_channel_ban(message, command, bot, glob=True, silent=True, log_key='log_sban'):
        return
    target_id, reason = await _ban_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if permissions.is_owner(target_id):
        await _ianswer(message, translator.get_string('mod_cannot_target_owner'))
        return

    banned_html, banned = await _punish_labels(bot, message, command, target_id)
    await _global_ban(bot, target_id)
    _log_global(message, 'log_sban', banned, reason, target_id)
    if is_dm:
        await _isend_html(bot, message.chat.id, _global_ban_text(banned_html, reason))


@router.message(Command('sban'))
async def sban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Silent local ban: ban only in the current chat, with nothing posted to the chat. Admins and up."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    if await _maybe_channel_ban(message, command, bot, glob=False, silent=True, log_key='log_slban'):
        return
    target_id, reason = await _ban_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if permissions.is_owner(target_id):
        await _ianswer(message, translator.get_string('mod_cannot_target_owner'))
        return

    _, banned = await _punish_labels(bot, message, command, target_id)
    try:
        await bot.ban_chat_member(message.chat.id, target_id)
    except Exception:
        pass
    db_man.add_local_ban(message.chat.id, target_id)
    _clear_captcha_state(message.chat.id, target_id)
    _log_action(message, 'log_slban', banned, reason, target_id)


@router.message(Command('unban'))
async def unban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Local unban: lift the ban in this chat as an exception. The user stays globally blocklisted."""
    await _delete_silently(message)
    if not await _require(message, permissions.is_staff(db_man, message.chat.id, message.from_user.id)):
        return
    if not await _cooldown_guard(message, 'unban'):
        return
    if await _maybe_channel_unban(message, bot, glob=False, silent=False, log_key='log_unban'):
        _cooldown_mark(message, 'unban')
        return
    target_id = await _get_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if not await _hierarchy_ok(message, target_id):
        return

    name_html, name = await _punish_labels(bot, message, command, target_id)
    try:
        await bot.unban_chat_member(message.chat.id, target_id, only_if_banned=True)
    except Exception:
        pass
    db_man.add_ban_exception(message.chat.id, target_id)
    db_man.remove_local_ban(message.chat.id, target_id)
    _log_action(message, 'log_unban', name, '', target_id)

    role_word = _actor_role_word(message.chat.id, message.from_user.id)
    actor = _actor_mention(message.from_user)
    await _ianswer_html(message, translator.get_string('unban_announce').format(
        name_html, role_word, actor))
    await _dm_target(bot, target_id, _dm_text('dm_unbanned', message.chat.title or str(message.chat.id), message))
    _cooldown_mark(message, 'unban')


@router.message(Command('ungban'))
async def ungban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Global unban: remove the user from the blocklist and lift the ban in every known chat."""
    await _delete_silently(message)
    if not await _require_global(message):
        return
    is_dm = message.chat.type not in _GROUP_TYPES
    if not is_dm and await _maybe_channel_unban(message, bot, glob=True, silent=False, log_key='log_ungban'):
        return
    target_id = await _get_target_or_reply(message, command, bot)
    if target_id is None:
        return

    name_html, name = await _punish_labels(bot, message, command, target_id)
    db_man.remove_from_blocklist(target_id)
    db_man.clear_ban_exceptions(target_id)
    db_man.clear_local_bans(target_id)
    _log_global(message, 'log_ungban', name, '', target_id)

    source_title = message.chat.title or str(message.chat.id)
    role_word = _actor_role_word(message.chat.id, message.from_user.id)
    actor = _actor_mention(message.from_user)
    local_text = translator.get_string('ungban_announce').format(name_html, role_word, actor)
    remote_text = translator.get_string('ungban_announce_remote').format(name_html, _esc(source_title))
    global_text = _global_unban_text(name_html)

    chat_ids = set(db_man.get_bot_chats())
    if not is_dm:
        chat_ids.add(message.chat.id)
    for chat_id in chat_ids:
        try:
            await bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
        except Exception:
            pass
        try:
            await _isend_html(bot, chat_id, global_text if is_dm else (local_text if chat_id == message.chat.id else remote_text))
        except Exception:
            pass
    if is_dm:
        await _isend_html(bot, message.chat.id, global_text)
        await _dm_target(bot, target_id, _dm_text('dm_unbanned_global_nosrc', '', message))
    else:
        await _dm_target(bot, target_id, _dm_text('dm_unbanned_global', source_title, message))


@router.message(Command('unsban'))
async def unsban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Silent local unban: same as /unban but nothing is posted to the chat."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    if await _maybe_channel_unban(message, bot, glob=False, silent=True, log_key='log_unsban'):
        return
    target_id = await _get_target_or_reply(message, command, bot)
    if target_id is None:
        return

    _, name = await _punish_labels(bot, message, command, target_id)
    try:
        await bot.unban_chat_member(message.chat.id, target_id, only_if_banned=True)
    except Exception:
        pass
    db_man.add_ban_exception(message.chat.id, target_id)
    db_man.remove_local_ban(message.chat.id, target_id)
    _log_action(message, 'log_unsban', name)


@router.message(Command('unsgban'))
async def unsgban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Silent global unban: same as /ungban but nothing is posted to the chat."""
    await _delete_silently(message)
    if not await _require_global(message):
        return
    is_dm = message.chat.type not in _GROUP_TYPES
    if not is_dm and await _maybe_channel_unban(message, bot, glob=True, silent=True, log_key='log_unsgban'):
        return
    target_id = await _get_target_or_reply(message, command, bot)
    if target_id is None:
        return

    name_html, name = await _punish_labels(bot, message, command, target_id)
    db_man.remove_from_blocklist(target_id)
    db_man.clear_ban_exceptions(target_id)
    db_man.clear_local_bans(target_id)
    chat_ids = set(db_man.get_bot_chats())
    if not is_dm:
        chat_ids.add(message.chat.id)
    for chat_id in chat_ids:
        try:
            await bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
        except Exception:
            pass
    _log_global(message, 'log_unsgban', name, '', target_id)
    if is_dm:
        await _isend_html(bot, message.chat.id, _global_unban_text(name_html))
