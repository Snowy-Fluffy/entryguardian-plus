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

from typing import Any, Awaitable, Callable
from datetime import datetime
import re
from aiogram import Router, types, Bot, BaseMiddleware, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
from aiogram.filters import Command, CommandObject
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter
from aiogram.types.chat_member_updated import ChatMemberUpdated
from aiogram.filters import IS_NOT_MEMBER, IS_MEMBER
from dbmanager import DBManager
from translator import Translator
import permissions
import config

router = Router()
db_man = DBManager()
translator = Translator(config.LOCALE)

_GROUP_TYPES = ('group', 'supergroup')


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
        if user and user.username:
            db_man.remember_user(user.id, user.username)

        # Globally command-banned users may not use any command, anywhere (owners are exempt).
        # /start stays available so they can still pass the captcha.
        text = event.text or ''
        if text.startswith('/') and user and not permissions.is_owner(user.id) and db_man.is_command_banned(user.id):
            cmd = text[1:].split(maxsplit=1)[0].split('@')[0].lower()
            if cmd != 'start':
                return

        if event.chat and event.chat.type in _GROUP_TYPES:
            db_man.remember_chat(event.chat.id)
            # A globally blocklisted user may already be a member of a chat the bot just joined.
            # No join event fires for them, so ban them the moment they're active here.
            if (
                user
                and not permissions.is_owner(user.id)
                and db_man.is_blocklisted(user.id)
                and not db_man.is_ban_exception(event.chat.id, user.id)
            ):
                try:
                    await event.bot.ban_chat_member(event.chat.id, user.id)
                except Exception:
                    pass
                try:
                    await event.delete()
                except Exception:
                    pass
                return  # drop the update: the sender is banned, don't process further

            # A "stopped" chat ignores all commands, except an owner re-enabling it.
            text = event.text or ''
            if text.startswith('/') and db_man.is_chat_stopped(event.chat.id):
                cmd = text[1:].split(maxsplit=1)[0].split('@')[0].lower()
                if not (cmd in ('startchat', 'stopchat') and user and permissions.is_owner(user.id)):
                    return
        return await handler(event, data)


@router.my_chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def handle_bot_added(event: ChatMemberUpdated, bot: Bot) -> None:
    """When the bot is added to a group, remember the chat and make the chat creator an admin."""
    if event.chat.type not in _GROUP_TYPES:
        return
    db_man.remember_chat(event.chat.id)
    try:
        admins = await bot.get_chat_administrators(event.chat.id)
    except Exception:
        return
    for member in admins:
        if member.status == 'creator':
            db_man.set_role(event.chat.id, member.user.id, 'admin')
            break


@router.my_chat_member(ChatMemberUpdatedFilter(IS_MEMBER >> IS_NOT_MEMBER))
async def handle_bot_removed(event: ChatMemberUpdated, bot: Bot) -> None:
    """When the bot is removed from a chat, stop tracking it for global bans."""
    db_man.forget_chat(event.chat.id)


async def _resolve_target(message: types.Message, command: CommandObject, bot: Bot) -> int | None:
    """Resolve the target user id from a text_mention, a reply, a numeric id, or an @username."""
    for entity in message.entities or []:
        if entity.type == 'text_mention' and entity.user:
            return entity.user.id

    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id

    arg = (command.args or '').strip().split()[0] if command.args else ''
    if not arg:
        return None

    if arg.lstrip('-').isdigit():
        return int(arg)

    if arg.startswith('@'):
        cached = db_man.find_user_by_username(arg)
        if cached is not None:
            return cached
        try:
            chat = await bot.get_chat(arg)
            return chat.id
        except Exception:
            return None

    return None


async def _require(message: types.Message, allowed: bool) -> bool:
    """Shared gate: command must be used in a group and the caller must be allowed."""
    if message.chat.type not in _GROUP_TYPES:
        await message.answer(translator.get_string('mod_group_only'))
        return False
    if not allowed:
        await message.answer(translator.get_string('mod_no_permission'))
        return False
    return True


async def _check_preconditions(message: types.Message) -> bool:
    """Group-only + 'can manage roles' check shared by every role command."""
    return await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id))


async def _delete_silently(message: types.Message) -> None:
    """Remove the command message; ignore failures (no rights, private chat, already gone)."""
    try:
        await message.delete()
    except Exception:
        pass


async def _get_target_or_reply(message: types.Message, command: CommandObject, bot: Bot) -> int | None:
    """Resolve the target user and reply with an error string when it cannot be determined."""
    target_provided = bool(message.reply_to_message) or bool((command.args or '').strip())
    target_id = await _resolve_target(message, command, bot)
    if target_id is None:
        key = 'mod_user_not_found' if target_provided else 'mod_specify_user'
        await message.answer(translator.get_string(key))
    return target_id


@router.message(Command('add_adm'))
async def add_admin(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if not await _check_preconditions(message):
        return
    target_id = await _get_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if await _is_bot_target(message, bot, target_id):
        return
    name = await _display_name(bot, message.chat.id, target_id)
    if db_man.is_admin(message.chat.id, target_id):
        await message.answer(translator.get_string('already_admin').format(name))
        return
    db_man.set_role(message.chat.id, target_id, 'admin')
    _log_action(message, 'log_add_adm', name)
    await message.answer(translator.get_string('admin_added').format(name))


@router.message(Command('del_adm'))
async def del_admin(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if not await _check_preconditions(message):
        return
    target_id = await _get_target_or_reply(message, command, bot)
    if target_id is None:
        return
    try:
        member = await bot.get_chat_member(message.chat.id, target_id)
        if member.status == 'creator':
            await message.answer(translator.get_string('mod_cannot_remove_creator'))
            return
    except Exception:
        pass
    name = await _display_name(bot, message.chat.id, target_id)
    if not db_man.is_admin(message.chat.id, target_id):
        await message.answer(translator.get_string('not_admin').format(name))
        return
    db_man.remove_role(message.chat.id, target_id)
    _log_action(message, 'log_del_adm', name)
    await message.answer(translator.get_string('admin_removed').format(name))


@router.message(Command('add_mod'))
async def add_moderator(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if not await _check_preconditions(message):
        return
    target_id = await _get_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if await _is_bot_target(message, bot, target_id):
        return
    name = await _display_name(bot, message.chat.id, target_id)
    if db_man.is_moderator(message.chat.id, target_id):
        await message.answer(translator.get_string('already_mod').format(name))
        return
    db_man.set_role(message.chat.id, target_id, 'moderator')
    _log_action(message, 'log_add_mod', name)
    await message.answer(translator.get_string('mod_added').format(name))


@router.message(Command('del_mod'))
async def del_moderator(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if not await _check_preconditions(message):
        return
    target_id = await _get_target_or_reply(message, command, bot)
    if target_id is None:
        return
    name = await _display_name(bot, message.chat.id, target_id)
    if not db_man.is_moderator(message.chat.id, target_id):
        await message.answer(translator.get_string('not_mod').format(name))
        return
    db_man.remove_role(message.chat.id, target_id)
    _log_action(message, 'log_del_mod', name)
    await message.answer(translator.get_string('mod_removed').format(name))


async def _display_name(bot: Bot, chat_id: int, user_id: int) -> str:
    try:
        user = (await bot.get_chat_member(chat_id, user_id)).user
        details = [f'@{user.username}'] if user.username else []
        details.append(f'id {user.id}')
        return f'{user.full_name} ({", ".join(details)})'
    except Exception:
        return f'id {user_id}'


@router.message(Command('staff'))
async def staff(message: types.Message, bot: Bot) -> None:
    await _delete_silently(message)
    if message.chat.type not in _GROUP_TYPES:
        await message.answer(translator.get_string('mod_group_only'))
        return

    rows = db_man.list_roles(message.chat.id)
    admins = [uid for uid, role in rows if role == 'admin']
    mods = [uid for uid, role in rows if role == 'moderator']

    if not admins and not mods:
        await message.answer(translator.get_string('staff_empty'))
        return

    admin_names = [await _display_name(bot, message.chat.id, uid) for uid in admins]
    mod_names = [await _display_name(bot, message.chat.id, uid) for uid in mods]

    none = translator.get_string('staff_none')
    lines = [
        translator.get_string('staff_header'),
        '',
        translator.get_string('staff_admins'),
        '\n'.join(f'• {n}' for n in admin_names) if admin_names else none,
        '',
        translator.get_string('staff_mods'),
        '\n'.join(f'• {n}' for n in mod_names) if mod_names else none,
    ]
    await message.answer('\n'.join(lines))


@router.message(Command('help'))
async def help_command(message: types.Message) -> None:
    await _delete_silently(message)
    if not permissions.is_staff(db_man, message.chat.id, message.from_user.id):
        await message.answer(translator.get_string('mod_no_permission'))
        return
    await message.answer(translator.get_string('help_text'))


def _user_label(user: types.User) -> str:
    """Short, human-readable label: full name, else @username, else id."""
    if user.full_name:
        return user.full_name
    if user.username:
        return f'@{user.username}'
    return str(user.id)


async def _simple_name(bot: Bot, chat_id: int, user_id: int) -> str:
    try:
        return _user_label((await bot.get_chat_member(chat_id, user_id)).user)
    except Exception:
        return str(user_id)


def _actor_role_word(chat_id: int, user_id: int) -> str:
    """The role noun shown in the ban announcement. Owners are announced as administrators."""
    if permissions.effective_role(db_man, chat_id, user_id) == 'moderator':
        return translator.get_string('ban_role_mod')
    return translator.get_string('ban_role_admin')


async def _is_bot_target(message: types.Message, bot: Bot, target_id: int) -> bool:
    """True (and warns) if the target is the bot itself — it must never punish or be given a role."""
    if target_id == bot.id:
        await message.answer(translator.get_string('cannot_target_bot'))
        return True
    return False


async def _hierarchy_ok(message: types.Message, target_id: int) -> bool:
    """Moderators may only moderate non-staff users; admins/owners are unrestricted here."""
    if permissions.effective_role(db_man, message.chat.id, message.from_user.id) == 'moderator':
        target_is_staff = permissions.is_owner(target_id) or \
            db_man.get_role(message.chat.id, target_id) in ('admin', 'moderator')
        if target_is_staff:
            await message.answer(translator.get_string('cannot_target_staff'))
            return False
    return True


def _record_log(chat_id: int, actor: types.User, action_key: str, target_label: str,
                reason: str = '', target_id: int | None = None) -> None:
    """Append a staff action to the per-chat history. Owner actions are not logged.

    Both the actor and (when target_id is given) the target are recorded with their id.
    """
    if permissions.is_owner(actor.id):
        return
    if target_id is not None and f'id {target_id}' not in target_label:
        target_label = f'{target_label} (id {target_id})'
    actor_label = f'{_user_label(actor)} (id {actor.id})'
    text = f'{actor_label} → {translator.get_string(action_key)} → {target_label}'
    if reason:
        text += ' | ' + translator.get_string('log_reason').format(reason)
    db_man.add_log(chat_id, text)


def _log_action(message: types.Message, action_key: str, target_label: str,
                reason: str = '', target_id: int | None = None) -> None:
    _record_log(message.chat.id, message.from_user, action_key, target_label, reason, target_id)


async def _parse_ban(message: types.Message, command: CommandObject, bot: Bot) -> tuple[int | None, str]:
    """Resolve the ban target and the optional reason from a reply or `<target> [reason]`."""
    args = (command.args or '').strip()

    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id, args

    for entity in message.entities or []:
        if entity.type == 'text_mention' and entity.user:
            text = message.text or ''
            reason = text[entity.offset + entity.length:].strip()
            return entity.user.id, reason

    if not args:
        return None, ''

    parts = args.split(maxsplit=1)
    token = parts[0]
    reason = parts[1].strip() if len(parts) > 1 else ''

    if token.lstrip('-').isdigit():
        return int(token), reason

    if token.startswith('@'):
        cached = db_man.find_user_by_username(token)
        if cached is not None:
            return cached, reason
        try:
            chat = await bot.get_chat(token)
            return chat.id, reason
        except Exception:
            return None, reason

    return None, reason


async def _ban_target_or_reply(message: types.Message, command: CommandObject, bot: Bot) -> tuple[int | None, str]:
    target_id, reason = await _parse_ban(message, command, bot)
    if target_id == bot.id:  # the bot must never punish itself
        await message.answer(translator.get_string('cannot_target_bot'))
        return None, reason
    if target_id is None:
        provided = bool(message.reply_to_message) or bool((command.args or '').strip())
        key = 'mod_user_not_found' if provided else 'mod_specify_user'
        await message.answer(translator.get_string(key))
    return target_id, reason


async def _global_ban(bot: Bot, target_id: int) -> None:
    """Blocklist the user (so they're banned on any future join) and ban them in every known chat."""
    db_man.add_to_blocklist(target_id)
    db_man.clear_ban_exceptions(target_id)  # a fresh global ban overrides any local unban exception
    for chat_id in db_man.get_bot_chats():
        try:
            await bot.ban_chat_member(chat_id, target_id)
        except Exception:
            pass


def _local_ban_text(message: types.Message, banned: str, reason: str) -> str:
    """Announcement for the chat where the command was issued."""
    role_word = _actor_role_word(message.chat.id, message.from_user.id)
    actor = _user_label(message.from_user)
    if reason:
        return translator.get_string('ban_announce').format(banned, role_word, actor, reason)
    return translator.get_string('ban_announce_no_reason').format(banned, role_word, actor)


def _remote_ban_text(banned: str, source_title: str, reason: str) -> str:
    """Announcement for the other chats, naming the chat the ban originated from."""
    if reason:
        return translator.get_string('ban_announce_remote').format(banned, source_title, reason)
    return translator.get_string('ban_announce_remote_no_reason').format(banned, source_title)


async def _announce_ban(message: types.Message, banned: str, reason: str) -> None:
    await message.answer(_local_ban_text(message, banned, reason))


@router.message(Command('gban'))
async def gban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Global ban across every chat the bot is in, announced in all of them. Admins and up."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    target_id, reason = await _ban_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if permissions.is_owner(target_id):
        await message.answer(translator.get_string('mod_cannot_target_owner'))
        return

    banned = await _simple_name(bot, message.chat.id, target_id)
    source_title = message.chat.title or str(message.chat.id)
    local_text = _local_ban_text(message, banned, reason)
    remote_text = _remote_ban_text(banned, source_title, reason)

    db_man.add_to_blocklist(target_id)
    chat_ids = set(db_man.get_bot_chats())
    chat_ids.add(message.chat.id)
    for chat_id in chat_ids:
        try:
            await bot.ban_chat_member(chat_id, target_id)
        except Exception:
            pass
        try:
            await bot.send_message(chat_id, local_text if chat_id == message.chat.id else remote_text)
        except Exception:
            pass
    _log_action(message, 'log_ban', banned, reason, target_id)


@router.message(Command('ban'))
async def ban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Local ban: ban only in the current chat, announced there. Moderators and up."""
    await _delete_silently(message)
    if not await _require(message, permissions.is_staff(db_man, message.chat.id, message.from_user.id)):
        return
    if not await _cooldown_guard(message, 'ban'):
        return
    target_id, reason = await _ban_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if permissions.is_owner(target_id):
        await message.answer(translator.get_string('mod_cannot_target_owner'))
        return
    if not await _hierarchy_ok(message, target_id):
        return

    banned = await _simple_name(bot, message.chat.id, target_id)
    try:
        await bot.ban_chat_member(message.chat.id, target_id)
    except Exception:
        await message.answer(translator.get_string('ban_failed'))
        return
    _log_action(message, 'log_lban', banned, reason, target_id)
    await _announce_ban(message, banned, reason)
    _cooldown_mark(message, 'ban')


@router.message(Command('sgban'))
async def sgban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Silent global ban: same reach as /gban but nothing is posted to the chat. Admins and up."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    target_id, reason = await _ban_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if permissions.is_owner(target_id):
        await message.answer(translator.get_string('mod_cannot_target_owner'))
        return

    banned = await _simple_name(bot, message.chat.id, target_id)
    await _global_ban(bot, target_id)
    _log_action(message, 'log_sban', banned, reason, target_id)
    # Silent by design: no announcement is posted to the chat.


@router.message(Command('sban'))
async def sban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Silent local ban: ban only in the current chat, with nothing posted to the chat. Admins and up."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    target_id, reason = await _ban_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if permissions.is_owner(target_id):
        await message.answer(translator.get_string('mod_cannot_target_owner'))
        return

    banned = await _simple_name(bot, message.chat.id, target_id)
    try:
        await bot.ban_chat_member(message.chat.id, target_id)
    except Exception:
        pass
    _log_action(message, 'log_slban', banned, reason, target_id)
    # Silent by design: no announcement is posted to the chat.


@router.message(Command('unban'))
async def unban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Local unban: lift the ban in this chat as an exception. The user stays globally blocklisted."""
    await _delete_silently(message)
    if not await _require(message, permissions.is_staff(db_man, message.chat.id, message.from_user.id)):
        return
    if not await _cooldown_guard(message, 'unban'):
        return
    target_id = await _get_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if not await _hierarchy_ok(message, target_id):
        return

    name = await _simple_name(bot, message.chat.id, target_id)
    try:
        await bot.unban_chat_member(message.chat.id, target_id, only_if_banned=True)
    except Exception:
        pass
    db_man.add_ban_exception(message.chat.id, target_id)
    _log_action(message, 'log_unban', name, '', target_id)

    role_word = _actor_role_word(message.chat.id, message.from_user.id)
    actor = _user_label(message.from_user)
    await message.answer(translator.get_string('unban_announce').format(name, role_word, actor))
    _cooldown_mark(message, 'unban')


@router.message(Command('ungban'))
async def ungban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Global unban: remove the user from the blocklist and lift the ban in every known chat."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    target_id = await _get_target_or_reply(message, command, bot)
    if target_id is None:
        return

    name = await _simple_name(bot, message.chat.id, target_id)
    db_man.remove_from_blocklist(target_id)
    db_man.clear_ban_exceptions(target_id)
    _log_action(message, 'log_ungban', name)

    source_title = message.chat.title or str(message.chat.id)
    role_word = _actor_role_word(message.chat.id, message.from_user.id)
    actor = _user_label(message.from_user)
    local_text = translator.get_string('ungban_announce').format(name, role_word, actor)
    remote_text = translator.get_string('ungban_announce_remote').format(name, source_title)

    chat_ids = set(db_man.get_bot_chats())
    chat_ids.add(message.chat.id)
    for chat_id in chat_ids:
        try:
            await bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
        except Exception:
            pass
        try:
            await bot.send_message(chat_id, local_text if chat_id == message.chat.id else remote_text)
        except Exception:
            pass


@router.message(Command('unsban'))
async def unsban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Silent local unban: same as /unban but nothing is posted to the chat."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    target_id = await _get_target_or_reply(message, command, bot)
    if target_id is None:
        return

    name = await _simple_name(bot, message.chat.id, target_id)
    try:
        await bot.unban_chat_member(message.chat.id, target_id, only_if_banned=True)
    except Exception:
        pass
    db_man.add_ban_exception(message.chat.id, target_id)
    _log_action(message, 'log_unsban', name)
    # Silent by design: no announcement is posted to the chat.


@router.message(Command('unsgban'))
async def unsgban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Silent global unban: same as /ungban but nothing is posted to the chat."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    target_id = await _get_target_or_reply(message, command, bot)
    if target_id is None:
        return

    name = await _simple_name(bot, message.chat.id, target_id)
    db_man.remove_from_blocklist(target_id)
    db_man.clear_ban_exceptions(target_id)
    chat_ids = set(db_man.get_bot_chats())
    chat_ids.add(message.chat.id)
    for chat_id in chat_ids:
        try:
            await bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
        except Exception:
            pass
    _log_action(message, 'log_unsgban', name)
    # Silent by design: no announcement is posted to the chat.


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
        await message.answer(translator.get_string('delete_need_reply'))
        return
    if target.from_user and not await _hierarchy_ok(message, target.from_user.id):
        return
    try:
        await bot.delete_message(message.chat.id, target.message_id)
    except Exception:
        await message.answer(translator.get_string('delete_failed'))
        return
    label = _user_label(target.from_user) if target.from_user else translator.get_string('staff_none')
    _log_action(message, 'log_delete', label, '', target.from_user.id if target.from_user else None)
    _cooldown_mark(message, 'delete')


@router.message(Command('raid_on'))
async def raid_on(message: types.Message, bot: Bot) -> None:
    """Enable anti-raid: every newcomer is locally banned, silently, with no captcha."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    db_man.set_raid_mode(message.chat.id, True)
    _log_action(message, 'log_raid', translator.get_string('raid_state_on'))
    await message.answer(translator.get_string('raid_on_msg'))


@router.message(Command('raid_off', 'raid_of'))
async def raid_off(message: types.Message, bot: Bot) -> None:
    """Disable anti-raid."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    db_man.set_raid_mode(message.chat.id, False)
    _log_action(message, 'log_raid', translator.get_string('raid_state_off'))
    await message.answer(translator.get_string('raid_off_msg'))


@router.message(Command('rules'))
async def rules_command(message: types.Message, bot: Bot) -> None:
    """Show this chat's custom rules (available to everyone)."""
    await _delete_silently(message)
    text = db_man.get_rules(message.chat.id)
    await message.answer(text if text else translator.get_string('rules_not_set'))


@router.message(Command('stopchat'))
async def stopchat_cmd(message: types.Message, bot: Bot) -> None:
    # Hidden owner-only command: make the bot ignore all commands from this chat.
    if message.chat.type not in _GROUP_TYPES or not permissions.is_owner(message.from_user.id):
        return
    await _delete_silently(message)
    db_man.set_chat_stopped(message.chat.id, True)
    await message.answer(translator.get_string('stopchat_msg'))


@router.message(Command('startchat'))
async def startchat_cmd(message: types.Message, bot: Bot) -> None:
    # Hidden owner-only command: resume accepting commands from this chat.
    if message.chat.type not in _GROUP_TYPES or not permissions.is_owner(message.from_user.id):
        return
    await _delete_silently(message)
    db_man.set_chat_stopped(message.chat.id, False)
    await message.answer(translator.get_string('startchat_msg'))


# ----------------------------------------------------------------------------
# Mute / unmute commands (local + global, loud + silent).
# ----------------------------------------------------------------------------

_DURATION_RE = re.compile(r'^(\d+)([smhdw])$')
_UNIT_SECONDS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}

_MUTED_PERMS = ChatPermissions(
    can_send_messages=False, can_send_audios=False, can_send_documents=False,
    can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
    can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
    can_add_web_page_previews=False,
)
_UNMUTED_PERMS = ChatPermissions(
    can_send_messages=True, can_send_audios=True, can_send_documents=True,
    can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
    can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
    can_add_web_page_previews=True,
)


def _parse_duration(token: str) -> int | None:
    match = _DURATION_RE.match(token.lower())
    return int(match.group(1)) * _UNIT_SECONDS[match.group(2)] if match else None


# Moderator commands that support a per-chat, per-command cooldown.
_COOLDOWN_COMMANDS = ['ban', 'mute', 'unmute', 'unban', 'delete']


def _human_time(seconds: int) -> str:
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f'{hours}{translator.get_string("cd_unit_h")}')
    if minutes:
        parts.append(f'{minutes}{translator.get_string("cd_unit_m")}')
    if secs or not parts:
        parts.append(f'{secs}{translator.get_string("cd_unit_s")}')
    return ' '.join(parts)


async def _cooldown_guard(message: types.Message, cmd: str) -> bool:
    """Return True if the command may run. Only moderators are limited; admins/owners bypass."""
    chat_id, user_id = message.chat.id, message.from_user.id
    if permissions.effective_role(db_man, chat_id, user_id) != 'moderator':
        return True
    cd = db_man.get_cooldown(chat_id, cmd)
    if cd <= 0:
        return True
    remaining = db_man.cooldown_remaining(chat_id, user_id, cmd, cd)
    if remaining > 0:
        await message.answer(translator.get_string('cooldown_wait').format(_human_time(remaining)))
        return False
    return True


def _cooldown_mark(message: types.Message, cmd: str) -> None:
    """Record a successful use so the cooldown starts (moderators only)."""
    if permissions.effective_role(db_man, message.chat.id, message.from_user.id) == 'moderator':
        db_man.record_cooldown_use(message.chat.id, message.from_user.id, cmd)


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
    # Persist so the mute survives captcha verification / rejoin and can be looked up later.
    db_man.add_mute(chat_id, user_id, until)


async def _apply_unmute(bot: Bot, chat_id: int, user_id: int) -> None:
    db_man.remove_mute(chat_id, user_id)  # drop the record first so it's gone even if the API call fails
    await bot.restrict_chat_member(chat_id, user_id, permissions=_UNMUTED_PERMS)


def _with_extras(text: str, dur_text: str, reason: str) -> str:
    if dur_text:
        text += translator.get_string('mute_for').format(dur_text)
    if reason:
        text += ', ' + translator.get_string('log_reason').format(reason)
    return text


def _mute_text(message: types.Message, muted: str, dur_text: str, reason: str) -> str:
    base = translator.get_string('mute_announce').format(
        muted, _actor_role_word(message.chat.id, message.from_user.id), _user_label(message.from_user))
    return _with_extras(base, dur_text, reason)


def _mute_remote_text(muted: str, source_title: str, dur_text: str, reason: str) -> str:
    return _with_extras(translator.get_string('mute_announce_remote').format(muted, source_title), dur_text, reason)


def _unmute_text(message: types.Message, target: str, reason: str) -> str:
    base = translator.get_string('unmute_announce').format(
        target, _actor_role_word(message.chat.id, message.from_user.id), _user_label(message.from_user))
    return _with_extras(base, '', reason)


def _unmute_remote_text(target: str, source_title: str, reason: str) -> str:
    return _with_extras(translator.get_string('unmute_announce_remote').format(target, source_title), '', reason)


async def _run_mute(message: types.Message, command: CommandObject, bot: Bot, *, glob: bool, silent: bool, log_key: str) -> bool:
    target_id, dur_seconds, dur_text, reason = await _parse_mute(message, command, bot)
    if target_id is None:
        provided = bool(message.reply_to_message) or bool((command.args or '').strip())
        await message.answer(translator.get_string('mod_user_not_found' if provided else 'mod_specify_user'))
        return False
    if await _is_bot_target(message, bot, target_id):
        return False
    if permissions.is_owner(target_id):
        await message.answer(translator.get_string('mod_cannot_target_owner'))
        return False
    if not await _hierarchy_ok(message, target_id):
        return False

    muted = await _simple_name(bot, message.chat.id, target_id)
    label = f'{muted} [{dur_text}]' if dur_text else muted

    if glob:
        # Remember globally so the mute also catches the user in chats they join later.
        db_man.set_global_mute(target_id, int(datetime.now().timestamp()) + dur_seconds if dur_seconds else 0)
        local_text = _mute_text(message, muted, dur_text, reason)
        remote_text = _mute_remote_text(muted, message.chat.title or str(message.chat.id), dur_text, reason)
        chat_ids = set(db_man.get_bot_chats())
        chat_ids.add(message.chat.id)
        for chat_id in chat_ids:
            try:
                await _apply_mute(bot, chat_id, target_id, dur_seconds)
            except Exception:
                pass
            if not silent:
                try:
                    await bot.send_message(chat_id, local_text if chat_id == message.chat.id else remote_text)
                except Exception:
                    pass
    else:
        try:
            await _apply_mute(bot, message.chat.id, target_id, dur_seconds)
        except Exception:
            await message.answer(translator.get_string('mute_failed'))
            return False
        if not silent:
            await message.answer(_mute_text(message, muted, dur_text, reason))

    _record_log(message.chat.id, message.from_user, log_key, label, reason, target_id)
    return True


async def _run_unmute(message: types.Message, command: CommandObject, bot: Bot, *, glob: bool, silent: bool, log_key: str) -> bool:
    target_id, reason = await _parse_ban(message, command, bot)
    if target_id is None:
        provided = bool(message.reply_to_message) or bool((command.args or '').strip())
        await message.answer(translator.get_string('mod_user_not_found' if provided else 'mod_specify_user'))
        return False
    if not await _hierarchy_ok(message, target_id):
        return False

    target = await _simple_name(bot, message.chat.id, target_id)

    if glob:
        db_man.remove_global_mute(target_id)
        local_text = _unmute_text(message, target, reason)
        remote_text = _unmute_remote_text(target, message.chat.title or str(message.chat.id), reason)
        chat_ids = set(db_man.get_bot_chats())
        chat_ids.add(message.chat.id)
        for chat_id in chat_ids:
            try:
                await _apply_unmute(bot, chat_id, target_id)
            except Exception:
                pass
            if not silent:
                try:
                    await bot.send_message(chat_id, local_text if chat_id == message.chat.id else remote_text)
                except Exception:
                    pass
    else:
        try:
            await _apply_unmute(bot, message.chat.id, target_id)
        except Exception:
            pass
        if not silent:
            await message.answer(_unmute_text(message, target, reason))

    _record_log(message.chat.id, message.from_user, log_key, target, reason, target_id)
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
    if await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        await _run_mute(message, command, bot, glob=True, silent=False, log_key='log_gmute')


@router.message(Command('smute'))
async def smute_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        await _run_mute(message, command, bot, glob=False, silent=True, log_key='log_smute')


@router.message(Command('gsmute'))
async def gsmute_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
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
    if await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        await _run_unmute(message, command, bot, glob=True, silent=False, log_key='log_ungmute')


@router.message(Command('ungsmute'))
async def ungsmute_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        await _run_unmute(message, command, bot, glob=True, silent=True, log_key='log_ungsmute')


# ----------------------------------------------------------------------------
# Admin panel — private-chat inline UI for managing roles and viewing logs.
# Access: owners (every chat) and admins (their chats), only after captcha.
# ----------------------------------------------------------------------------

# user_id → {'action': 'aa'|'ra'|'am'|'rm', 'chat_id': int} while awaiting a username/id.
_panel_state: dict[int, dict[str, Any]] = {}

_ROLE_ACTIONS = {
    'aa': ('admin', 'is_admin', 'already_admin', 'admin_added', 'log_add_adm'),
    'am': ('moderator', 'is_moderator', 'already_mod', 'mod_added', 'log_add_mod'),
}


def _accessible_chats(user_id: int) -> list[int]:
    if permissions.is_owner(user_id):
        return db_man.get_bot_chats()
    return db_man.get_admin_chats(user_id)


def _can_access_chat(user_id: int, chat_id: int) -> bool:
    return permissions.is_owner(user_id) or db_man.is_admin(chat_id, user_id)


async def _chat_title(bot: Bot, chat_id: int) -> str:
    try:
        return (await bot.get_chat(chat_id)).title or str(chat_id)
    except Exception:
        return str(chat_id)


async def _build_chat_list(bot: Bot, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    buttons = []
    for chat_id in _accessible_chats(user_id):
        title = await _chat_title(bot, chat_id)
        buttons.append([InlineKeyboardButton(text=title, callback_data=f'adm:c:{chat_id}')])
    return translator.get_string('admin_pick_chat'), InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_chat_menu(bot: Bot, chat_id: int, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    title = await _chat_title(bot, chat_id)
    rows = db_man.list_roles(chat_id)
    admin_names = [await _display_name(bot, chat_id, uid) for uid, role in rows if role == 'admin']
    mod_names = [await _display_name(bot, chat_id, uid) for uid, role in rows if role == 'moderator']
    none = translator.get_string('staff_none')
    text = '\n'.join([
        f'🛠 {title}',
        '',
        translator.get_string('staff_admins'),
        '\n'.join(f'• {n}' for n in admin_names) if admin_names else none,
        '',
        translator.get_string('staff_mods'),
        '\n'.join(f'• {n}' for n in mod_names) if mod_names else none,
    ])
    keyboard = [
        [
            InlineKeyboardButton(text=translator.get_string('admin_btn_add_adm'), callback_data=f'adm:aa:{chat_id}'),
            InlineKeyboardButton(text=translator.get_string('admin_btn_del_adm'), callback_data=f'adm:ra:{chat_id}'),
        ],
        [
            InlineKeyboardButton(text=translator.get_string('admin_btn_add_mod'), callback_data=f'adm:am:{chat_id}'),
            InlineKeyboardButton(text=translator.get_string('admin_btn_del_mod'), callback_data=f'adm:rm:{chat_id}'),
        ],
        [
            InlineKeyboardButton(text=translator.get_string('admin_btn_logs'), callback_data=f'adm:lg:{chat_id}'),
            InlineKeyboardButton(text=translator.get_string('admin_btn_rules'), callback_data=f'adm:rl:{chat_id}'),
            InlineKeyboardButton(text=translator.get_string('admin_btn_cooldowns'), callback_data=f'adm:cd:{chat_id}'),
        ],
        [InlineKeyboardButton(
            text=translator.get_string(
                'admin_btn_captcha_on' if db_man.is_captcha_enabled(chat_id) else 'admin_btn_captcha_off'
            ),
            callback_data=f'adm:cap:{chat_id}',
        )],
        [InlineKeyboardButton(
            text=translator.get_string(
                'admin_btn_raid_on' if db_man.is_raid_mode(chat_id) else 'admin_btn_raid_off'
            ),
            callback_data=f'adm:raid:{chat_id}',
        )],
    ]
    if permissions.is_owner(user_id):
        keyboard.append([InlineKeyboardButton(
            text=translator.get_string('admin_btn_start' if db_man.is_chat_stopped(chat_id) else 'admin_btn_stop'),
            callback_data=f'adm:stop:{chat_id}',
        )])
        keyboard.append([InlineKeyboardButton(
            text=translator.get_string('admin_btn_cmdban'), callback_data=f'adm:cb:{chat_id}',
        )])
    if len(_accessible_chats(user_id)) > 1:
        keyboard.append([InlineKeyboardButton(text=translator.get_string('admin_btn_back'), callback_data='adm:home')])
    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _global_name(bot: Bot, user_id: int) -> str:
    try:
        chat = await bot.get_chat(user_id)
        label = chat.full_name or (f'@{chat.username}' if chat.username else str(user_id))
        return f'{label} (id {user_id})'
    except Exception:
        return f'id {user_id}'


async def _build_command_ban_menu(bot: Bot, chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    banned = db_man.get_command_banned()
    buttons = []
    for uid in banned:
        buttons.append([InlineKeyboardButton(text=f'❌ {await _global_name(bot, uid)}', callback_data=f'adm:cbdel:{chat_id}:{uid}')])
    buttons.append([InlineKeyboardButton(text=translator.get_string('cmdban_add_btn'), callback_data=f'adm:cbadd:{chat_id}')])
    buttons.append([InlineKeyboardButton(text=translator.get_string('admin_btn_back'), callback_data=f'adm:c:{chat_id}')])
    title = translator.get_string('cmdban_title' if banned else 'cmdban_empty')
    return title, InlineKeyboardMarkup(inline_keyboard=buttons)


def _build_cooldown_menu(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    buttons = []
    for cmd in _COOLDOWN_COMMANDS:
        cd = db_man.get_cooldown(chat_id, cmd)
        value = _human_time(cd) if cd else translator.get_string('cooldown_off')
        buttons.append([InlineKeyboardButton(text=f'/{cmd} — {value}', callback_data=f'adm:cdset:{chat_id}:{cmd}')])
    buttons.append([InlineKeyboardButton(text=translator.get_string('admin_btn_back'), callback_data=f'adm:c:{chat_id}')])
    return translator.get_string('cooldown_title'), InlineKeyboardMarkup(inline_keyboard=buttons)


def _render_logs(chat_id: int) -> str:
    rows = db_man.get_logs(chat_id)
    if not rows:
        return translator.get_string('log_empty')
    lines = [translator.get_string('log_header')]
    for ts, text in rows:
        lines.append(f'{datetime.fromtimestamp(ts).strftime("%d.%m %H:%M:%S")} | {text}')
    out = '\n'.join(lines)
    return out if len(out) <= 4000 else out[:3999] + '…'


async def _edit(message: types.Message, text: str, markup: InlineKeyboardMarkup | None) -> None:
    try:
        await message.edit_text(text, reply_markup=markup)
    except Exception:
        pass


@router.message(Command('admin'))
async def admin_panel(message: types.Message, bot: Bot) -> None:
    """Open the admin panel. Private chat only, for verified admins and owners."""
    user_id = message.from_user.id
    _panel_state.pop(user_id, None)

    if message.chat.type != 'private':
        await message.answer(translator.get_string('admin_private_only'))
        return
    if not (permissions.is_owner(user_id) or db_man.get_admin_chats(user_id)):
        await message.answer(translator.get_string('admin_no_access'))
        return
    if not db_man.is_user_allowed(user_id):
        await message.answer(translator.get_string('admin_need_captcha'))
        return

    chats = _accessible_chats(user_id)
    if not chats:
        await message.answer(translator.get_string('admin_no_chats'))
        return
    if len(chats) == 1:
        text, markup = await _build_chat_menu(bot, chats[0], user_id)
    else:
        text, markup = await _build_chat_list(bot, user_id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith('adm:'))
async def admin_callback(callback: types.CallbackQuery, bot: Bot) -> None:
    user_id = callback.from_user.id

    if db_man.is_command_banned(user_id) and not permissions.is_owner(user_id):
        await callback.answer(translator.get_string('admin_no_access'), show_alert=True)
        return
    if not (permissions.is_owner(user_id) or db_man.get_admin_chats(user_id)) or not db_man.is_user_allowed(user_id):
        await callback.answer(translator.get_string('admin_no_access'), show_alert=True)
        return

    parts = callback.data.split(':')
    action = parts[1]

    if action == 'home':
        _panel_state.pop(user_id, None)
        text, markup = await _build_chat_list(bot, user_id)
        await _edit(callback.message, text, markup)
        await callback.answer()
        return

    chat_id = int(parts[2])
    if not _can_access_chat(user_id, chat_id):
        await callback.answer(translator.get_string('admin_access_lost'), show_alert=True)
        return

    if action == 'c':  # open / return to the chat menu (also used as cancel)
        _panel_state.pop(user_id, None)
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await _edit(callback.message, text, markup)
    elif action == 'lg':  # view logs
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=translator.get_string('admin_btn_back'), callback_data=f'adm:c:{chat_id}')
        ]])
        await _edit(callback.message, _render_logs(chat_id), markup)
    elif action == 'cap':  # toggle captcha for this chat
        new_state = not db_man.is_captcha_enabled(chat_id)
        db_man.set_captcha_enabled(chat_id, new_state)
        _record_log(chat_id, callback.from_user, 'log_captcha',
                    translator.get_string('captcha_state_on' if new_state else 'captcha_state_off'))
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await _edit(callback.message, text, markup)
    elif action == 'raid':  # toggle anti-raid for this chat
        new_state = not db_man.is_raid_mode(chat_id)
        db_man.set_raid_mode(chat_id, new_state)
        _record_log(chat_id, callback.from_user, 'log_raid',
                    translator.get_string('raid_state_on' if new_state else 'raid_state_off'))
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await _edit(callback.message, text, markup)
    elif action == 'stop':  # owner-only: toggle whether the bot accepts commands from this chat
        if not permissions.is_owner(user_id):
            await callback.answer(translator.get_string('admin_no_access'), show_alert=True)
            return
        db_man.set_chat_stopped(chat_id, not db_man.is_chat_stopped(chat_id))
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await _edit(callback.message, text, markup)
    elif action == 'rl':  # set / clear chat rules
        _panel_state[user_id] = {'action': 'rules', 'chat_id': chat_id}
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=translator.get_string('admin_btn_cancel'), callback_data=f'adm:c:{chat_id}')
        ]])
        await _edit(callback.message, translator.get_string('admin_prompt_rules'), markup)
    elif action == 'cd':  # moderator-command cooldowns sub-menu
        text, markup = _build_cooldown_menu(chat_id)
        await _edit(callback.message, text, markup)
    elif action == 'cdset':  # ask for the cooldown value of a specific command
        cmd = parts[3]
        _panel_state[user_id] = {'action': 'cooldown', 'chat_id': chat_id, 'cmd': cmd}
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=translator.get_string('admin_btn_cancel'), callback_data=f'adm:cd:{chat_id}')
        ]])
        await _edit(callback.message, translator.get_string('admin_prompt_cooldown').format(cmd), markup)
    elif action in ('cb', 'cbadd', 'cbdel'):  # owner-only: global command-ban management
        if not permissions.is_owner(user_id):
            await callback.answer(translator.get_string('admin_no_access'), show_alert=True)
            return
        if action == 'cbdel':
            db_man.remove_command_ban(int(parts[3]))
            text, markup = await _build_command_ban_menu(bot, chat_id)
            await _edit(callback.message, text, markup)
        elif action == 'cbadd':
            _panel_state[user_id] = {'action': 'cmdban', 'chat_id': chat_id}
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=translator.get_string('admin_btn_cancel'), callback_data=f'adm:cb:{chat_id}')
            ]])
            await _edit(callback.message, translator.get_string('admin_prompt_cmdban'), markup)
        else:  # 'cb' — open the menu
            text, markup = await _build_command_ban_menu(bot, chat_id)
            await _edit(callback.message, text, markup)
    elif action in ('aa', 'ra', 'am', 'rm'):  # ask for a target user
        _panel_state[user_id] = {'action': action, 'chat_id': chat_id}
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=translator.get_string('admin_btn_cancel'), callback_data=f'adm:c:{chat_id}')
        ]])
        await _edit(callback.message, translator.get_string('admin_prompt_user'), markup)
    await callback.answer()


async def _resolve_panel_target(message: types.Message, bot: Bot) -> int | None:
    text = (message.text or '').strip()
    if not text:
        return None
    token = text.split()[0]
    if token.lstrip('-').isdigit():
        return int(token)
    name = token.lstrip('@')
    cached = db_man.find_user_by_username(name)
    if cached is not None:
        return cached
    try:
        return (await bot.get_chat('@' + name)).id
    except Exception:
        return None


async def _apply_panel_role(bot: Bot, actor: types.User, chat_id: int, action: str, target_id: int) -> str:
    if target_id == bot.id:
        return translator.get_string('cannot_target_bot')
    name = await _display_name(bot, chat_id, target_id)

    if action in _ROLE_ACTIONS:  # add admin / add moderator
        role, checker, already_key, done_key, log_key = _ROLE_ACTIONS[action]
        if getattr(db_man, checker)(chat_id, target_id):
            return translator.get_string(already_key).format(name)
        db_man.set_role(chat_id, target_id, role)
        _record_log(chat_id, actor, log_key, name)
        return translator.get_string(done_key).format(name)

    if action == 'ra':  # remove admin
        try:
            if (await bot.get_chat_member(chat_id, target_id)).status == 'creator':
                return translator.get_string('mod_cannot_remove_creator')
        except Exception:
            pass
        if not db_man.is_admin(chat_id, target_id):
            return translator.get_string('not_admin').format(name)
        db_man.remove_role(chat_id, target_id)
        _record_log(chat_id, actor, 'log_del_adm', name)
        return translator.get_string('admin_removed').format(name)

    # action == 'rm': remove moderator
    if not db_man.is_moderator(chat_id, target_id):
        return translator.get_string('not_mod').format(name)
    db_man.remove_role(chat_id, target_id)
    _record_log(chat_id, actor, 'log_del_mod', name)
    return translator.get_string('mod_removed').format(name)


def _awaiting_panel_input(message: types.Message) -> bool:
    return (
        message.chat.type == 'private'
        and message.from_user is not None
        and message.from_user.id in _panel_state
        and not (message.text or '').startswith('/')
    )


@router.message(_awaiting_panel_input)
async def admin_panel_input(message: types.Message, bot: Bot) -> None:
    user_id = message.from_user.id
    state = _panel_state.get(user_id)
    if not state:
        return
    chat_id = state['chat_id']

    if not _can_access_chat(user_id, chat_id):
        _panel_state.pop(user_id, None)
        await message.answer(translator.get_string('admin_access_lost'))
        return

    if state['action'] == 'cmdban':
        if not permissions.is_owner(user_id):
            _panel_state.pop(user_id, None)
            await message.answer(translator.get_string('admin_no_access'))
            return
        target_id = await _resolve_panel_target(message, bot)
        if target_id is None:
            await message.answer(translator.get_string('admin_bad_user'))  # keep state to retry
            return
        _panel_state.pop(user_id, None)
        if permissions.is_owner(target_id):
            await message.answer(translator.get_string('cmdban_cant_owner'))
        elif target_id == bot.id:
            await message.answer(translator.get_string('cannot_target_bot'))
        else:
            db_man.add_command_ban(target_id)
            await message.answer(translator.get_string('cmdban_added'))
        text, markup = await _build_command_ban_menu(bot, chat_id)
        await message.answer(text, reply_markup=markup)
        return

    if state['action'] == 'rules':
        _panel_state.pop(user_id, None)
        if (message.text or '').strip() == '-':
            db_man.set_rules(chat_id, '')
            _record_log(chat_id, message.from_user, 'log_rules', translator.get_string('rules_state_cleared'))
            result = translator.get_string('rules_cleared')
        else:
            db_man.set_rules(chat_id, message.text or '')
            _record_log(chat_id, message.from_user, 'log_rules', translator.get_string('rules_state_set'))
            result = translator.get_string('rules_saved')
        await message.answer(result)
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await message.answer(text, reply_markup=markup)
        return

    if state['action'] == 'cooldown':
        cmd = state['cmd']
        raw = (message.text or '').strip().lower()
        if raw in ('0', 'off', '-'):
            seconds = 0
        else:
            seconds = _parse_duration(raw)
            if seconds is None:
                await message.answer(translator.get_string('cooldown_bad_value'))  # keep state to retry
                return
        _panel_state.pop(user_id, None)
        db_man.set_cooldown(chat_id, cmd, seconds)
        value = _human_time(seconds) if seconds else translator.get_string('cooldown_off')
        _record_log(chat_id, message.from_user, 'log_cooldown', f'/{cmd} = {value}')
        await message.answer(translator.get_string('cooldown_set').format(cmd))
        text, markup = _build_cooldown_menu(chat_id)
        await message.answer(text, reply_markup=markup)
        return

    target_id = await _resolve_panel_target(message, bot)
    if target_id is None:
        await message.answer(translator.get_string('admin_bad_user'))  # keep state so the user can retry
        return

    _panel_state.pop(user_id, None)
    result = await _apply_panel_role(bot, message.from_user, chat_id, state['action'], target_id)
    await message.answer(result)
    text, markup = await _build_chat_menu(bot, chat_id, user_id)
    await message.answer(text, reply_markup=markup)
