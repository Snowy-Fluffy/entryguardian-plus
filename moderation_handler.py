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
import asyncio
import html
import re
from aiogram import Router, types, Bot, BaseMiddleware, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
from aiogram.filters import Command, CommandObject
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter
from aiogram.types.chat_member_updated import ChatMemberUpdated
from aiogram.filters import IS_NOT_MEMBER, IS_MEMBER, MEMBER, ADMINISTRATOR
from dbmanager import DBManager
from translator import Translator
import permissions
import config

router = Router()
db_man = DBManager()
translator = Translator(config.LOCALE)

_GROUP_TYPES = ('group', 'supergroup')

# Strong refs to detached background tasks so they aren't garbage-collected mid-run.
_bg_tasks: set[asyncio.Task] = set()


def _spawn(coro: Awaitable[Any]) -> None:
    """Run a coroutine detached, keeping a reference until it finishes."""
    task = asyncio.ensure_future(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


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
            # remember_chat returns True the first time a chat is seen. A bot that was already
            # in a chat before this version emits no join event, so the first message is our
            # cue to enforce the global blocklist there. Run it detached so the message flows on.
            if db_man.remember_chat(event.chat.id):
                _spawn(_sweep_blocklist_chat(event.bot, event.chat.id))
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

            # Same enforcement for messages posted on behalf of a blocklisted channel.
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

            # "Channels forbidden" mode: ban any channel that posts here and announce it.
            # Linked-channel auto-forwards are left alone so the discussion link keeps working.
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

            # A "stopped" chat ignores all commands, except an owner re-enabling it.
            text = event.text or ''
            if text.startswith('/') and db_man.is_chat_stopped(event.chat.id):
                cmd = text[1:].split(maxsplit=1)[0].split('@')[0].lower()
                if not (cmd in ('startchat', 'stopchat') and user and permissions.is_owner(user.id)):
                    return
        return await handler(event, data)


@router.my_chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def handle_bot_added(event: ChatMemberUpdated, bot: Bot) -> None:
    """When the bot is added to a group, remember the chat, make the chat creator an admin,
    and immediately enforce the global blocklist there (in case banned users are already in)."""
    if event.chat.type not in _GROUP_TYPES:
        return
    db_man.remember_chat(event.chat.id)
    # Best-effort: ban any already-present blocklisted users. Silent; needs ban rights to take effect.
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
        await _ianswer(message, translator.get_string('mod_group_only'))
        return False
    if not allowed:
        await _deny(message)
        return False
    return True


async def _check_preconditions(message: types.Message) -> bool:
    """Group-only + 'can manage roles' check shared by every role command."""
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return False
    if _reply_channel(message) is not None:  # a channel can't hold a staff role
        await _ianswer(message, translator.get_string('cannot_target_channel'))
        return False
    return True


async def _delete_silently(message: types.Message) -> None:
    """Remove the command message; ignore failures (no rights, private chat, already gone)."""
    try:
        await message.delete()
    except Exception:
        pass


# Every message the bot posts in a chat is styled italic, except the /help and /staff listings
# and the join/verification prompt. These helpers centralise that styling.
def _italic(text: str) -> str:
    """Wrap plain text as an italic HTML body. Only & < > need escaping in a text body;
    quote=False keeps apostrophes/quotes literal (Telegram doesn't decode &#x27;)."""
    return f'<i>{html.escape(text, quote=False)}</i>'


async def _ianswer(message: types.Message, text: str) -> types.Message:
    """Reply in the chat using the bot's standard italic styling."""
    return await message.answer(_italic(text), parse_mode='HTML')


async def _isend(bot: Bot, chat_id: int, text: str) -> types.Message:
    """Send an italic message to a chat."""
    return await bot.send_message(chat_id, _italic(text), parse_mode='HTML')


async def _delete_after(bot: Bot, chat_id: int, message_id: int, delay: int) -> None:
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def _deny(message: types.Message) -> None:
    """Post the 'no permission' notice (italic) and auto-remove it after 10 seconds."""
    sent = await _ianswer(message, translator.get_string('mod_no_permission'))
    _spawn(_delete_after(message.bot, message.chat.id, sent.message_id, 10))


# Punishment announcements carry clickable user mentions, so they are built as HTML and sent
# without the blanket escaping that _italic does. Free-text parts must be escaped with _esc.
def _esc(text: str) -> str:
    """Escape & < > for safe inclusion in an HTML message body (quotes stay literal)."""
    return html.escape(text, quote=False)


def _user_mention(user_id: int, name: str) -> str:
    """Clickable mention that also spells out the id: `<a>Name</a> (id N)`."""
    return f'<a href="tg://user?id={user_id}">{_esc(name)}</a> (id {user_id})'


def _reply_channel(message: types.Message) -> types.Chat | None:
    """If the command replies to a message posted on behalf of a channel, return that channel.

    Anonymous group admins also post with a sender_chat (the group itself); only real channels
    (type 'channel') can be acted on as sender chats, so we filter to those. A regular reply's
    `from_user` is the anonymous @Channel_Bot, which is why those bans must go through here.
    """
    reply = message.reply_to_message
    if reply and reply.sender_chat and reply.sender_chat.type == 'channel':
        return reply.sender_chat
    return None


def _channel_mention(chat: types.Chat) -> str:
    """Label for a channel: a link for a public one, plus its id."""
    title = chat.title or str(chat.id)
    if chat.username:
        return f'<a href="https://t.me/{chat.username}">{_esc(title)}</a> (id {chat.id})'
    return f'{_esc(title)} (id {chat.id})'


def _chat_info(chat: types.Chat) -> str:
    """`Title (@username, id N)` for a channel/chat, for the plain-text report DM."""
    details = [f'@{chat.username}'] if chat.username else []
    details.append(f'id {chat.id}')
    return f'{chat.title} ({", ".join(details)})'


def _actor_mention(user: types.User) -> str:
    """Clickable mention for the staff member who issued the command."""
    return _user_mention(user.id, _user_label(user))


async def _target_mention(bot: Bot, chat_id: int, user_id: int) -> str:
    """Clickable mention for the punished user (name resolved via the chat)."""
    return _user_mention(user_id, await _simple_name(bot, chat_id, user_id))


async def _ianswer_html(message: types.Message, body: str) -> types.Message:
    """Reply in the chat with pre-built HTML, wrapped in the standard italic styling."""
    return await message.answer(f'<i>{body}</i>', parse_mode='HTML')


async def _isend_html(bot: Bot, chat_id: int, body: str) -> types.Message:
    """Send pre-built HTML to a chat, wrapped in the standard italic styling."""
    return await bot.send_message(chat_id, f'<i>{body}</i>', parse_mode='HTML')


async def _get_target_or_reply(message: types.Message, command: CommandObject, bot: Bot) -> int | None:
    """Resolve the target user and reply with an error string when it cannot be determined."""
    target_provided = bool(message.reply_to_message) or bool((command.args or '').strip())
    target_id = await _resolve_target(message, command, bot)
    if target_id is None:
        key = 'mod_user_not_found' if target_provided else 'mod_specify_user'
        await _ianswer(message, translator.get_string(key))
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
        await _ianswer(message, translator.get_string('already_admin').format(name))
        return
    db_man.set_role(message.chat.id, target_id, 'admin')
    _log_action(message, 'log_add_adm', name)
    await _ianswer(message, translator.get_string('admin_added').format(name))


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
            await _ianswer(message, translator.get_string('mod_cannot_remove_creator'))
            return
    except Exception:
        pass
    name = await _display_name(bot, message.chat.id, target_id)
    if not db_man.is_admin(message.chat.id, target_id):
        await _ianswer(message, translator.get_string('not_admin').format(name))
        return
    db_man.remove_role(message.chat.id, target_id)
    _log_action(message, 'log_del_adm', name)
    await _ianswer(message, translator.get_string('admin_removed').format(name))


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
        await _ianswer(message, translator.get_string('already_mod').format(name))
        return
    db_man.set_role(message.chat.id, target_id, 'moderator')
    _log_action(message, 'log_add_mod', name)
    await _ianswer(message, translator.get_string('mod_added').format(name))


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
        await _ianswer(message, translator.get_string('not_mod').format(name))
        return
    db_man.remove_role(message.chat.id, target_id)
    _log_action(message, 'log_del_mod', name)
    await _ianswer(message, translator.get_string('mod_removed').format(name))


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
    # Available to everyone, in groups and in DM.
    await _delete_silently(message)
    await message.answer(translator.get_string('help_text'))


def _full_user_info(user: types.User) -> str:
    """`Full Name (@username, id N)` built straight from a User object, no API call."""
    details = [f'@{user.username}'] if user.username else []
    details.append(f'id {user.id}')
    return f'{user.full_name} ({", ".join(details)})'


def _message_link(chat: types.Chat, message_id: int) -> str | None:
    """A t.me link to a message, when the chat type supports one (supergroups only)."""
    if chat.type != 'supergroup':
        return None
    if chat.username:
        return f'https://t.me/{chat.username}/{message_id}'
    # Private supergroup id looks like -100<internal>; the link uses just <internal>.
    return f'https://t.me/c/{str(chat.id)[4:]}/{message_id}'


def _report_recipients(chat_id: int, exclude_id: int) -> set[int]:
    """Who should receive a report from this chat: all owners + the chat's admins and moderators."""
    recipients = set(config.OWNERS)
    for uid, _role in db_man.list_roles(chat_id):
        recipients.add(uid)
    recipients.discard(exclude_id)  # don't DM the reporter their own report
    return recipients


@router.message(Command('report'))
async def report_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Report a message to the chat's staff (and every owner) in DM. Anyone may use it.

    Used as a reply, it reports (and forwards) that message. Used on its own, it sends a
    general report with no specific message indicated.
    """
    if message.chat.type not in _GROUP_TYPES:
        await _ianswer(message, translator.get_string('mod_group_only'))
        return

    reported = message.reply_to_message
    reporter = message.from_user
    reason = (command.args or '').strip()
    chat_title = message.chat.title or str(message.chat.id)

    lines = [
        translator.get_string('report_header'),
        translator.get_string('report_chat').format(chat_title),
        translator.get_string('report_from').format(_full_user_info(reporter)),
    ]
    if reported is not None:
        if reported.sender_chat and reported.sender_chat.type == 'channel':
            lines.append(translator.get_string('report_on').format(_chat_info(reported.sender_chat)))
        elif reported.from_user:
            lines.append(translator.get_string('report_on').format(_full_user_info(reported.from_user)))
        link = _message_link(message.chat, reported.message_id)
        if link:
            lines.append(translator.get_string('report_link').format(link))
    else:
        lines.append(translator.get_string('report_no_message'))
    if reason:
        lines.append(translator.get_string('report_reason').format(reason))
    notice = '\n'.join(lines)

    for uid in _report_recipients(message.chat.id, reporter.id):
        try:
            await bot.send_message(uid, notice)
            if reported is not None:
                await bot.forward_message(uid, message.chat.id, reported.message_id)
        except Exception:
            pass  # recipient hasn't started the bot, blocked it, or can't be messaged

    await _delete_silently(message)
    await _ianswer(message, translator.get_string('report_sent'))


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
        await _ianswer(message, translator.get_string('cannot_target_bot'))
        return True
    return False


async def _hierarchy_ok(message: types.Message, target_id: int) -> bool:
    """Moderators may only moderate non-staff users; admins/owners are unrestricted here."""
    if permissions.effective_role(db_man, message.chat.id, message.from_user.id) == 'moderator':
        target_is_staff = permissions.is_owner(target_id) or \
            db_man.get_role(message.chat.id, target_id) in ('admin', 'moderator')
        if target_is_staff:
            await _ianswer(message, translator.get_string('cannot_target_staff'))
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
        await _ianswer(message, translator.get_string('cannot_target_bot'))
        return None, reason
    if target_id is None:
        provided = bool(message.reply_to_message) or bool((command.args or '').strip())
        key = 'mod_user_not_found' if provided else 'mod_specify_user'
        await _ianswer(message, translator.get_string(key))
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
        db_man.add_channel_ban_exception(message.chat.id, channel.id)  # stays globally blocklisted
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
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    if await _maybe_channel_ban(message, command, bot, glob=True, silent=False, log_key='log_ban'):
        return
    target_id, reason = await _ban_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if permissions.is_owner(target_id):
        await _ianswer(message, translator.get_string('mod_cannot_target_owner'))
        return

    banned = await _simple_name(bot, message.chat.id, target_id)  # plain name, for the log
    banned_html = _user_mention(target_id, banned)
    source_title = message.chat.title or str(message.chat.id)
    local_text = _local_ban_text(message, banned_html, reason)
    remote_text = _remote_ban_text(banned_html, source_title, reason)

    db_man.add_to_blocklist(target_id)
    chat_ids = set(db_man.get_bot_chats())
    chat_ids.add(message.chat.id)
    for chat_id in chat_ids:
        try:
            await bot.ban_chat_member(chat_id, target_id)
        except Exception:
            pass
        try:
            await _isend_html(bot, chat_id, local_text if chat_id == message.chat.id else remote_text)
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

    banned = await _simple_name(bot, message.chat.id, target_id)
    try:
        await bot.ban_chat_member(message.chat.id, target_id)
    except Exception:
        await _ianswer(message, translator.get_string('ban_failed'))
        return
    _log_action(message, 'log_lban', banned, reason, target_id)
    await _announce_ban(message, _user_mention(target_id, banned), reason)
    _cooldown_mark(message, 'ban')


@router.message(Command('sgban'))
async def sgban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Silent global ban: same reach as /gban but nothing is posted to the chat. Admins and up."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    if await _maybe_channel_ban(message, command, bot, glob=True, silent=True, log_key='log_sban'):
        return
    target_id, reason = await _ban_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if permissions.is_owner(target_id):
        await _ianswer(message, translator.get_string('mod_cannot_target_owner'))
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
    if await _maybe_channel_ban(message, command, bot, glob=False, silent=True, log_key='log_slban'):
        return
    target_id, reason = await _ban_target_or_reply(message, command, bot)
    if target_id is None:
        return
    if permissions.is_owner(target_id):
        await _ianswer(message, translator.get_string('mod_cannot_target_owner'))
        return

    banned = await _simple_name(bot, message.chat.id, target_id)
    try:
        await bot.ban_chat_member(message.chat.id, target_id)
    except Exception:
        pass
    _log_action(message, 'log_slban', banned, reason, target_id)
    # Silent by design: no announcement is posted to the chat.


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
        except Exception:
            pass
    # Blocklisted channels get the same preemptive treatment via the sender-chat ban.
    for channel_id in db_man.get_channel_blocklist():
        if db_man.is_channel_ban_exception(chat_id, channel_id):
            continue
        try:
            await bot.ban_chat_sender_chat(chat_id, channel_id)
            banned_count += 1
        except Exception:
            pass
    return banned_count


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

    name = await _simple_name(bot, message.chat.id, target_id)
    try:
        await bot.unban_chat_member(message.chat.id, target_id, only_if_banned=True)
    except Exception:
        pass
    db_man.add_ban_exception(message.chat.id, target_id)
    _log_action(message, 'log_unban', name, '', target_id)

    role_word = _actor_role_word(message.chat.id, message.from_user.id)
    actor = _actor_mention(message.from_user)
    await _ianswer_html(message, translator.get_string('unban_announce').format(
        _user_mention(target_id, name), role_word, actor))
    _cooldown_mark(message, 'unban')


@router.message(Command('ungban'))
async def ungban(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Global unban: remove the user from the blocklist and lift the ban in every known chat."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    if await _maybe_channel_unban(message, bot, glob=True, silent=False, log_key='log_ungban'):
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
    actor = _actor_mention(message.from_user)
    name_html = _user_mention(target_id, name)
    local_text = translator.get_string('ungban_announce').format(name_html, role_word, actor)
    remote_text = translator.get_string('ungban_announce_remote').format(name_html, _esc(source_title))

    chat_ids = set(db_man.get_bot_chats())
    chat_ids.add(message.chat.id)
    for chat_id in chat_ids:
        try:
            await bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
        except Exception:
            pass
        try:
            await _isend_html(bot, chat_id, local_text if chat_id == message.chat.id else remote_text)
        except Exception:
            pass


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
    if await _maybe_channel_unban(message, bot, glob=True, silent=True, log_key='log_unsgban'):
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


@router.message(Command('raid_on'))
async def raid_on(message: types.Message, bot: Bot) -> None:
    """Enable anti-raid: every newcomer is locally banned, silently, with no captcha."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    db_man.set_raid_mode(message.chat.id, True)
    _log_action(message, 'log_raid', translator.get_string('raid_state_on'))
    await _ianswer(message, translator.get_string('raid_on_msg'))


@router.message(Command('raid_off', 'raid_of'))
async def raid_off(message: types.Message, bot: Bot) -> None:
    """Disable anti-raid."""
    await _delete_silently(message)
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return
    db_man.set_raid_mode(message.chat.id, False)
    _log_action(message, 'log_raid', translator.get_string('raid_state_off'))
    await _ianswer(message, translator.get_string('raid_off_msg'))


@router.message(Command('rules'))
async def rules_command(message: types.Message, bot: Bot) -> None:
    """Show this chat's custom rules (available to everyone)."""
    await _delete_silently(message)
    text = db_man.get_rules(message.chat.id)
    if text:
        text = translator.get_string('rules_header') + '\n\n' + text
    else:
        text = translator.get_string('rules_not_set')
    await _ianswer(message, text)


@router.message(Command('stopchat'))
async def stopchat_cmd(message: types.Message, bot: Bot) -> None:
    # Hidden owner-only command: make the bot ignore all commands from this chat.
    if message.chat.type not in _GROUP_TYPES or not permissions.is_owner(message.from_user.id):
        return
    await _delete_silently(message)
    db_man.set_chat_stopped(message.chat.id, True)
    await _ianswer(message, translator.get_string('stopchat_msg'))


@router.message(Command('startchat'))
async def startchat_cmd(message: types.Message, bot: Bot) -> None:
    # Hidden owner-only command: resume accepting commands from this chat.
    if message.chat.type not in _GROUP_TYPES or not permissions.is_owner(message.from_user.id):
        return
    await _delete_silently(message)
    db_man.set_chat_stopped(message.chat.id, False)
    await _ianswer(message, translator.get_string('startchat_msg'))


@router.message(Command('leavechat'))
async def leavechat_cmd(message: types.Message, bot: Bot) -> None:
    # Hidden owner-only command: make the bot leave this chat.
    if message.chat.type not in _GROUP_TYPES or not permissions.is_owner(message.from_user.id):
        return
    await _delete_silently(message)
    await _leave_chat(bot, message.chat.id)


async def _leave_chat(bot: Bot, chat_id: int) -> None:
    """Leave a chat and forget it (forget_chat also runs via the my_chat_member update, but be sure)."""
    try:
        await bot.leave_chat(chat_id)
    except Exception:
        pass
    db_man.forget_chat(chat_id)


# ----------------------------------------------------------------------------
# Mute / unmute commands (local + global, loud + silent).
# ----------------------------------------------------------------------------

_DURATION_RE = re.compile(r'^(\d+)(mo|[smhdw])$')  # 'mo' = months (before 'm' so it wins)
_UNIT_SECONDS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800, 'mo': 2592000}  # month = 30 days

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

# Permissions a verified (post-captcha / unmuted) user gets, configurable per chat in the panel.
# Each panel key maps to one or more ChatPermissions fields it switches together. Only ordinary
# member rights are offered — admin-ish rights (invite/pin/change info) are never granted here.
# 'tag' = can_edit_tag (Bot API 9.5+): the user may edit their own chat tag.
_PERM_GROUPS: dict[str, tuple[str, ...]] = {
    'messages': ('can_send_messages',),
    'media': ('can_send_audios', 'can_send_documents', 'can_send_photos',
              'can_send_videos', 'can_send_video_notes', 'can_send_voice_notes'),
    'stickers': ('can_send_other_messages',),
    'polls': ('can_send_polls',),
    'links': ('can_add_web_page_previews',),
    'tag': ('can_edit_tag',),
}
_PERM_ORDER = ['messages', 'media', 'stickers', 'polls', 'links', 'tag']
# Default: everything a normal member can send ('tag' stays off until an admin enables it).
_DEFAULT_PERMS = frozenset({'messages', 'media', 'stickers', 'polls', 'links'})

# ChatPermissions fields the installed aiogram actually knows — newer fields like can_edit_tag
# only exist on recent versions, so we filter to these to avoid breaking on older installs.
try:
    _VALID_PERM_FIELDS = set(ChatPermissions.model_fields)            # pydantic v2 (aiogram 3)
except AttributeError:  # pragma: no cover - fallback for older pydantic
    _VALID_PERM_FIELDS = set(getattr(ChatPermissions, '__fields__', {}) or {})


def _chat_perm_set(chat_id: int) -> set[str]:
    """The set of enabled permission keys for a chat (falls back to the default set)."""
    stored = db_man.get_chat_perms(chat_id)
    return set(stored) if stored is not None else set(_DEFAULT_PERMS)


def build_chat_permissions(chat_id: int) -> ChatPermissions:
    """ChatPermissions granted to a verified/unmuted user, per the chat's configuration."""
    enabled = _chat_perm_set(chat_id)
    fields: dict[str, bool] = {}
    for key, fnames in _PERM_GROUPS.items():
        for field in fnames:
            fields[field] = key in enabled
    if _VALID_PERM_FIELDS:  # silently drop fields this aiogram version doesn't support
        fields = {f: v for f, v in fields.items() if f in _VALID_PERM_FIELDS}
    return ChatPermissions(**fields)


def _parse_duration(token: str) -> int | None:
    match = _DURATION_RE.match(token.lower())
    return int(match.group(1)) * _UNIT_SECONDS[match.group(2)] if match else None


_DUR_UNIT_KEY = {'s': 'dur_unit_s', 'm': 'dur_unit_m', 'h': 'dur_unit_h', 'd': 'dur_unit_d',
                 'w': 'dur_unit_w', 'mo': 'dur_unit_mo'}


def _plural_index(count: int) -> int:
    """Pick the plural form index for the active locale's `plural_category`."""
    if translator.get_string('plural_category') == 'ru':
        n10, n100 = count % 10, count % 100
        if n10 == 1 and n100 != 11:
            return 0
        if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
            return 1
        return 2
    return 0 if count == 1 else 1  # English-style: one / other


def _human_duration_words(token: str) -> str:
    """Turn a duration token like `5h` into spelled-out words, e.g. `5 часов` / `5 hours`."""
    match = _DURATION_RE.match(token.lower())
    if not match:
        return token
    count, unit = int(match.group(1)), match.group(2)
    forms = translator.get_string(_DUR_UNIT_KEY[unit]).split('|')
    word = forms[min(_plural_index(count), len(forms) - 1)]
    return f'{count} {word}'


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
        await _ianswer(message, translator.get_string('cooldown_wait').format(_human_time(remaining)))
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
    # Restore the chat's configured baseline rights (same set a user gets after the captcha).
    await bot.restrict_chat_member(chat_id, user_id, permissions=build_chat_permissions(chat_id))


def _with_extras(text: str, dur_words: str, reason: str) -> str:
    # dur_words is already spelled-out and safe; reason is free text and must be escaped.
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


def _unmute_text(message: types.Message, target: str, reason: str) -> str:
    """Local unmute announcement. `target` is HTML (a mention)."""
    base = translator.get_string('unmute_announce').format(
        target, _actor_role_word(message.chat.id, message.from_user.id), _actor_mention(message.from_user))
    return _with_extras(base, '', reason)


def _unmute_remote_text(target: str, source_title: str, reason: str) -> str:
    return _with_extras(translator.get_string('unmute_announce_remote').format(target, _esc(source_title)), '', reason)


async def _run_mute(message: types.Message, command: CommandObject, bot: Bot, *, glob: bool, silent: bool, log_key: str) -> bool:
    if _reply_channel(message) is not None:  # Telegram can't restrict a channel sender, only ban it
        await _ianswer(message, translator.get_string('cannot_mute_channel'))
        return False
    target_id, dur_seconds, dur_text, reason = await _parse_mute(message, command, bot)
    if target_id is None:
        provided = bool(message.reply_to_message) or bool((command.args or '').strip())
        await _ianswer(message, translator.get_string('mod_user_not_found' if provided else 'mod_specify_user'))
        return False
    if await _is_bot_target(message, bot, target_id):
        return False
    if permissions.is_owner(target_id):
        await _ianswer(message, translator.get_string('mod_cannot_target_owner'))
        return False
    if not await _hierarchy_ok(message, target_id):
        return False

    muted = await _simple_name(bot, message.chat.id, target_id)  # plain name, for the log
    label = f'{muted} [{dur_text}]' if dur_text else muted
    muted_html = _user_mention(target_id, muted)
    dur_words = _human_duration_words(dur_text) if dur_text else ''

    if glob:
        # Remember globally so the mute also catches the user in chats they join later.
        db_man.set_global_mute(target_id, int(datetime.now().timestamp()) + dur_seconds if dur_seconds else 0)
        local_text = _mute_text(message, muted_html, dur_words, reason)
        remote_text = _mute_remote_text(muted_html, message.chat.title or str(message.chat.id), dur_words, reason)
        chat_ids = set(db_man.get_bot_chats())
        chat_ids.add(message.chat.id)
        for chat_id in chat_ids:
            try:
                await _apply_mute(bot, chat_id, target_id, dur_seconds)
            except Exception:
                pass
            if not silent:
                try:
                    await _isend_html(bot, chat_id, local_text if chat_id == message.chat.id else remote_text)
                except Exception:
                    pass
    else:
        try:
            await _apply_mute(bot, message.chat.id, target_id, dur_seconds)
        except Exception:
            await _ianswer(message, translator.get_string('mute_failed'))
            return False
        if not silent:
            await _ianswer_html(message, _mute_text(message, muted_html, dur_words, reason))

    _record_log(message.chat.id, message.from_user, log_key, label, reason, target_id)
    return True


async def _run_unmute(message: types.Message, command: CommandObject, bot: Bot, *, glob: bool, silent: bool, log_key: str) -> bool:
    if _reply_channel(message) is not None:  # channels are never muted; nothing to lift
        await _ianswer(message, translator.get_string('cannot_mute_channel'))
        return False
    target_id, reason = await _parse_ban(message, command, bot)
    if target_id is None:
        provided = bool(message.reply_to_message) or bool((command.args or '').strip())
        await _ianswer(message, translator.get_string('mod_user_not_found' if provided else 'mod_specify_user'))
        return False
    if not await _hierarchy_ok(message, target_id):
        return False

    target = await _simple_name(bot, message.chat.id, target_id)  # plain name, for the log
    target_html = _user_mention(target_id, target)

    if glob:
        db_man.remove_global_mute(target_id)
        local_text = _unmute_text(message, target_html, reason)
        remote_text = _unmute_remote_text(target_html, message.chat.title or str(message.chat.id), reason)
        chat_ids = set(db_man.get_bot_chats())
        chat_ids.add(message.chat.id)
        for chat_id in chat_ids:
            try:
                await _apply_unmute(bot, chat_id, target_id)
            except Exception:
                pass
            if not silent:
                try:
                    await _isend_html(bot, chat_id, local_text if chat_id == message.chat.id else remote_text)
                except Exception:
                    pass
    else:
        try:
            await _apply_unmute(bot, message.chat.id, target_id)
        except Exception:
            pass
        if not silent:
            await _ianswer_html(message, _unmute_text(message, target_html, reason))

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

# user_id → current log search query (persisted across page navigation; '' = no filter).
_log_search: dict[int, str] = {}

_LOG_PAGE_SIZE = 10  # log entries shown per page in the admin panel

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
        [InlineKeyboardButton(
            text=translator.get_string(
                'admin_btn_channels_on' if db_man.is_channels_banned(chat_id) else 'admin_btn_channels_off'
            ),
            callback_data=f'adm:chan:{chat_id}',
        )],
        [InlineKeyboardButton(text=translator.get_string('admin_btn_perms'), callback_data=f'adm:perm:{chat_id}')],
    ]
    if permissions.is_owner(user_id):
        keyboard.append([InlineKeyboardButton(
            text=translator.get_string('admin_btn_start' if db_man.is_chat_stopped(chat_id) else 'admin_btn_stop'),
            callback_data=f'adm:stop:{chat_id}',
        )])
        keyboard.append([InlineKeyboardButton(
            text=translator.get_string('admin_btn_cmdban'), callback_data=f'adm:cb:{chat_id}',
        )])
        keyboard.append([InlineKeyboardButton(
            text=translator.get_string('admin_btn_leave'), callback_data=f'adm:leave:{chat_id}',
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


def _perm_supported(key: str) -> bool:
    """Whether this aiogram build knows the permission's field(s) (else the toggle is hidden)."""
    if not _VALID_PERM_FIELDS:
        return True
    return any(field in _VALID_PERM_FIELDS for field in _PERM_GROUPS[key])


def _build_perms_menu(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Sub-menu to toggle each permission verified users receive after the captcha."""
    enabled = _chat_perm_set(chat_id)
    buttons = []
    for key in _PERM_ORDER:
        if not _perm_supported(key):
            continue
        mark = '✅' if key in enabled else '❌'
        label = translator.get_string(f'perm_{key}')
        buttons.append([InlineKeyboardButton(text=f'{mark} {label}', callback_data=f'adm:permt:{chat_id}:{key}')])
    buttons.append([InlineKeyboardButton(text=translator.get_string('admin_btn_back'), callback_data=f'adm:c:{chat_id}')])
    return translator.get_string('perms_title'), InlineKeyboardMarkup(inline_keyboard=buttons)


def _log_action_row(chat_id: int, query: str) -> list[InlineKeyboardButton]:
    """The search / clear-search button row shown under the log view."""
    row = [InlineKeyboardButton(text=translator.get_string('log_search_btn'), callback_data=f'adm:lgs:{chat_id}')]
    if query:
        row.append(InlineKeyboardButton(text=translator.get_string('log_clear_btn'), callback_data=f'adm:lgclr:{chat_id}'))
    return row


def _render_logs_page(chat_id: int, query: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    """Render one page of the (optionally filtered) action log, with navigation buttons."""
    rows = db_man.get_logs(chat_id)  # whole history, newest first
    if query:
        needle = query.lower()
        rows = [(ts, text) for ts, text in rows if needle in text.lower()]

    back = [InlineKeyboardButton(text=translator.get_string('admin_btn_back'), callback_data=f'adm:c:{chat_id}')]
    total = len(rows)
    if total == 0:
        empty = translator.get_string('log_search_empty').format(query) if query else translator.get_string('log_empty')
        return empty, InlineKeyboardMarkup(inline_keyboard=[_log_action_row(chat_id, query), back])

    pages = (total + _LOG_PAGE_SIZE - 1) // _LOG_PAGE_SIZE
    page = max(0, min(page, pages - 1))
    start = page * _LOG_PAGE_SIZE
    chunk = rows[start:start + _LOG_PAGE_SIZE]

    header = translator.get_string('log_search_header').format(query) if query else translator.get_string('log_header')
    lines = [header, translator.get_string('log_page_info').format(page + 1, pages, total), '']
    for ts, text in chunk:
        lines.append(f'{datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M:%S")} | {text}')
    out = '\n'.join(lines)
    if len(out) > 4000:
        out = out[:3999] + '…'

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text=translator.get_string('log_prev'), callback_data=f'adm:lgpg:{chat_id}:{page - 1}'))
    nav.append(InlineKeyboardButton(text=f'{page + 1}/{pages}', callback_data='adm:noop'))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text=translator.get_string('log_next'), callback_data=f'adm:lgpg:{chat_id}:{page + 1}'))

    keyboard = [nav, _log_action_row(chat_id, query), back]
    return out, InlineKeyboardMarkup(inline_keyboard=keyboard)


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

    if action == 'noop':  # inert button (e.g. the page indicator)
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
    elif action == 'lg':  # open logs (first page, clears any active search)
        _panel_state.pop(user_id, None)
        _log_search[user_id] = ''
        text, markup = _render_logs_page(chat_id, '', 0)
        await _edit(callback.message, text, markup)
    elif action == 'lgpg':  # navigate to a specific log page (keeps the current search)
        text, markup = _render_logs_page(chat_id, _log_search.get(user_id, ''), int(parts[3]))
        await _edit(callback.message, text, markup)
    elif action == 'lgs':  # ask for a search query
        _panel_state[user_id] = {'action': 'logsearch', 'chat_id': chat_id}
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=translator.get_string('admin_btn_cancel'), callback_data=f'adm:lg:{chat_id}')
        ]])
        await _edit(callback.message, translator.get_string('log_search_prompt'), markup)
    elif action == 'lgclr':  # clear the active search filter
        _panel_state.pop(user_id, None)
        _log_search[user_id] = ''
        text, markup = _render_logs_page(chat_id, '', 0)
        await _edit(callback.message, text, markup)
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
    elif action == 'chan':  # toggle "all channels forbidden" mode for this chat
        new_state = not db_man.is_channels_banned(chat_id)
        db_man.set_channels_banned(chat_id, new_state)
        _record_log(chat_id, callback.from_user, 'log_channels',
                    translator.get_string('channels_state_on' if new_state else 'channels_state_off'))
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await _edit(callback.message, text, markup)
    elif action == 'perm':  # open the post-captcha permissions sub-menu
        text, markup = _build_perms_menu(chat_id)
        await _edit(callback.message, text, markup)
    elif action == 'permt':  # toggle one permission, then redraw the sub-menu
        key = parts[3]
        if key in _PERM_GROUPS:
            enabled = _chat_perm_set(chat_id)
            if key in enabled:
                enabled.discard(key)
            else:
                enabled.add(key)
            db_man.set_chat_perms(chat_id, sorted(enabled))
            _record_log(chat_id, callback.from_user, 'log_perms',
                        f'{translator.get_string(f"perm_{key}")} — '
                        f'{translator.get_string("perm_on" if key in enabled else "perm_off")}')
        text, markup = _build_perms_menu(chat_id)
        await _edit(callback.message, text, markup)
    elif action == 'stop':  # owner-only: toggle whether the bot accepts commands from this chat
        if not permissions.is_owner(user_id):
            await callback.answer(translator.get_string('admin_no_access'), show_alert=True)
            return
        db_man.set_chat_stopped(chat_id, not db_man.is_chat_stopped(chat_id))
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await _edit(callback.message, text, markup)
    elif action in ('leave', 'leavego'):  # owner-only: make the bot leave the chat (with confirm)
        if not permissions.is_owner(user_id):
            await callback.answer(translator.get_string('admin_no_access'), show_alert=True)
            return
        if action == 'leave':  # show the confirmation screen
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=translator.get_string('leave_confirm_btn'), callback_data=f'adm:leavego:{chat_id}')],
                [InlineKeyboardButton(text=translator.get_string('admin_btn_cancel'), callback_data=f'adm:c:{chat_id}')],
            ])
            await _edit(callback.message, translator.get_string('leave_confirm_text'), markup)
        else:  # 'leavego' — actually leave, then drop back to the chat list
            await _leave_chat(bot, chat_id)
            _panel_state.pop(user_id, None)
            text, markup = await _build_chat_list(bot, user_id)
            await _edit(callback.message, text, markup)
            await callback.answer(translator.get_string('leave_done'), show_alert=True)
            return
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

    if state['action'] == 'logsearch':
        _panel_state.pop(user_id, None)
        query = (message.text or '').strip()
        _log_search[user_id] = query
        text, markup = _render_logs_page(chat_id, query, 0)
        await message.answer(text, reply_markup=markup)
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
