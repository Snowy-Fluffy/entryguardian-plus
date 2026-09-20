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

"""Shared infrastructure and helpers used across the moderation_handler package: the router/db_man/
translator singletons, and every helper used by more than one submodule. No other submodule may be
imported from here — this keeps the package's import graph a strict star with no cycles."""

from typing import Any, Awaitable, Callable
import asyncio
import html
import logging
import re
import time
from aiogram import Router, types, Bot
from aiogram.types import ChatPermissions
from aiogram.filters import CommandObject
from dbmanager import DBManager
from translator import Translator
import permissions
import config

router = Router()
db_man = DBManager()
translator = Translator(config.LOCALE)

_GROUP_TYPES = ('group', 'supergroup')

_bg_tasks: set[asyncio.Task] = set()

_seen_cache: dict[int, tuple] = {}


log = logging.getLogger('entryguardian')


def _task_done(task: asyncio.Task) -> None:
    _bg_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        log.error('background task %s failed', task.get_name(), exc_info=task.exception())


def _spawn(coro: Awaitable[Any]) -> None:
    """Run a coroutine detached, keeping a reference until it finishes; a crash is logged
    instead of vanishing into "Task exception was never retrieved"."""
    task = asyncio.ensure_future(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_task_done)


async def _flood_safe(call: Callable[[], Awaitable[Any]]) -> Any:
    """Await a Bot API call; if Telegram answers with Retry-After, wait it out once and retry.
    Any other error propagates — the caller decides whether it's fatal."""
    try:
        return await call()
    except Exception as e:
        retry_after = float(getattr(e, 'retry_after', 0) or 0)
        if retry_after > 0:
            await asyncio.sleep(retry_after + 1)
            return await call()
        raise


_PSEUDO_IDS = frozenset({136817688, 1087968824})
"""Telegram's shared pseudo-accounts: @Channel_Bot (every post made *as a channel*) and
GroupAnonymousBot (every anonymous admin post). A punishment or role written for one of these
ids would apply to every channel post / every anonymous admin everywhere — never a real target.
Channels are banned via _reply_channel (sender_chat); anonymous admins can't be targeted at all."""


class _TargetRefused(Exception):
    """A target was resolved but must not be acted on; `key` is the locale string to answer with.
    Raised by the low-level resolvers, turned into a reply by the *_or_reply wrappers."""
    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


_NUMERIC_ID_RE = re.compile(r'-?\d+')


def _parse_id_token(token: str) -> int | None:
    """A bare numeric user id, or None if the token isn't one. ASCII digits only — str.isdigit()
    accepts '²'/'①' and lstrip('-') lets '--5' through, both of which then crash int().
    A negative id is a chat/channel, not a user: refused (channels are targeted by reply)."""
    if not _NUMERIC_ID_RE.fullmatch(token):
        return None
    value = int(token)
    if value < 0:
        raise _TargetRefused('use_reply_for_channel')
    if value in _PSEUDO_IDS:
        raise _TargetRefused('cannot_target_pseudo')
    return value


def _check_user_target(user: types.User | None) -> int | None:
    """A User taken from a reply/mention as a target id — refusing the pseudo-accounts."""
    if user is None:
        return None
    if user.id in _PSEUDO_IDS:
        raise _TargetRefused('cannot_target_pseudo')
    return user.id


def _entity_tail(text: str, entity: types.MessageEntity) -> str:
    """Text after an entity. Entity offsets are UTF-16 code units, not Python characters —
    slicing the str directly is off by one per astral character (emoji) before/inside it."""
    raw = text.encode('utf-16-le')
    return raw[(entity.offset + entity.length) * 2:].decode('utf-16-le', 'ignore').strip()


async def _refuse(message: types.Message, exc: _TargetRefused) -> None:
    text = translator.get_string(exc.key)
    if message.chat.type in _GROUP_TYPES:
        await _ianswer(message, text)
    else:
        await message.answer(text)


def _cache_from_chat(chat) -> None:
    """Best-effort: cache a resolved chat's id/username/name. Must never break resolution, so it
    swallows everything (e.g. a Chat type that has no `full_name` attribute on this aiogram build)."""
    try:
        name = getattr(chat, 'full_name', None) or getattr(chat, 'title', None) or getattr(chat, 'first_name', None)
        db_man.remember_user(chat.id, chat.username, name)
    except Exception:
        pass


async def _resolve_username_token(bot: Bot, username: str) -> int | None:
    """Resolve an @username (leading '@' optional) to a user id: a live Telegram lookup first
    (only works if the bot already has an established peer with that user — see _identity_for
    for why a bare numeric id resolves far more reliably), falling back to the local seen_users
    cache. No external third-party resolver — deliberately removed as unreliable/out of our
    control; a miss here means asking the caller for the numeric id or a reply instead."""
    token = username if username.startswith('@') else f'@{username}'
    try:
        chat = await bot.get_chat(token)
    except Exception:
        chat = None
    if chat is not None:
        if chat.type != 'private':
            # getChat resolves channel/group usernames too — those are never a *user* target
            # (a channel is banned by replying to its post, which goes to the channel blocklist).
            raise _TargetRefused('use_reply_for_channel')
        _cache_from_chat(chat)
        return chat.id
    return db_man.find_user_by_username(token)


async def _resolve_target(message: types.Message, command: CommandObject, bot: Bot) -> int | None:
    """Resolve the target user id — same precedence as _parse_ban: the reply, else a
    text_mention, else a numeric id / @username argument. Raises _TargetRefused for the
    pseudo-accounts and for chats/channels (see _parse_id_token)."""
    if message.reply_to_message and message.reply_to_message.from_user:
        return _check_user_target(message.reply_to_message.from_user)

    for entity in message.entities or []:
        if entity.type == 'text_mention' and entity.user:
            return _check_user_target(entity.user)

    arg = (command.args or '').strip().split()[0] if command.args else ''
    if not arg:
        return None

    numeric = _parse_id_token(arg)
    if numeric is not None:
        return numeric

    if arg.startswith('@'):
        return await _resolve_username_token(bot, arg)

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


async def _require_global(message: types.Message) -> bool:
    """Gate for the global punishment commands (gban/gmute and their reversals). In a group:
    the chat's admins and owners. From DM: **owners only** — a chat admin's reach is their own
    chats, and a global action from DM has no chat to scope it to, so it's reserved for owners
    (a chat admin can still /gban from inside a group they administer)."""
    user_id = message.from_user.id
    if message.chat.type in _GROUP_TYPES:
        if permissions.can_manage_roles(db_man, message.chat.id, user_id):
            return True
        await _deny(message)
        return False
    if permissions.is_owner(user_id):
        return True
    await message.answer(translator.get_string('mod_no_permission'))
    return False


async def _delete_silently(message: types.Message) -> None:
    """Remove the command message; ignore failures (no rights, private chat, already gone)."""
    try:
        await message.delete()
    except Exception:
        pass


def _italic(text: str) -> str:
    """Wrap plain text as an italic HTML body. Only & < > need escaping in a text body;
    quote=False keeps apostrophes/quotes literal (Telegram doesn't decode &#x27;)."""
    return f'<i>{html.escape(text, quote=False)}</i>'


def _schedule_delete(chat_id: int, message_id: int, delay: int) -> None:
    """Queue one of the bot's own messages for deletion `delay` seconds from now. Goes through
    the persistent `scheduled_deletes` table (drained by scheduled_delete_task) rather than an
    in-process sleep, so a pending auto-delete survives a bot restart."""
    db_man.schedule_delete(chat_id, message_id, db_man.unix_time() + max(0, int(delay)))


def _service_ttl(chat: types.Chat, ttl: int | None) -> int:
    """Seconds after which a *service* reply (plain italic: errors, confirmations, /rules ...)
    should auto-delete: an explicit ttl wins, otherwise config.SERVICE_REPLY_TTL. Never in DM
    (nothing to declutter there), and 0 means keep."""
    if chat.type not in _GROUP_TYPES:
        return 0
    return config.SERVICE_REPLY_TTL if ttl is None else max(0, ttl)


async def _ianswer(message: types.Message, text: str, disable_preview: bool = False,
                   ttl: int | None = None) -> types.Message:
    """Reply in the chat using the bot's standard italic styling. This is the *service reply*
    helper (errors, confirmations, /rules, ...): in a group the message is auto-deleted after
    `ttl` seconds (default config.SERVICE_REPLY_TTL; 0 keeps it). Punishment announcements go
    through _ianswer_html/_isend_html instead and are never auto-deleted."""
    sent = await message.answer(
        _italic(text),
        parse_mode='HTML',
        link_preview_options=types.LinkPreviewOptions(is_disabled=True) if disable_preview else None,
    )
    delay = _service_ttl(message.chat, ttl)
    if delay:
        _schedule_delete(message.chat.id, sent.message_id, delay)
    return sent


async def _isend(bot: Bot, chat_id: int, text: str, ttl: int | None = None) -> types.Message:
    """Send an italic *service* message to a chat (see _ianswer for the auto-delete rule; here
    the chat type isn't known, so the TTL applies to any non-private chat id — i.e. negative)."""
    sent = await bot.send_message(chat_id, _italic(text), parse_mode='HTML')
    delay = 0 if chat_id > 0 else (config.SERVICE_REPLY_TTL if ttl is None else max(0, ttl))
    if delay:
        _schedule_delete(chat_id, sent.message_id, delay)
    return sent


_SCHEDULED_DELETE_INTERVAL = 5
_SCHEDULED_DELETE_THROTTLE = 0.1


async def scheduled_delete_task(bot: Bot) -> None:
    """Drain the persistent auto-delete queue: every few seconds delete every bot message whose
    deadline has passed, paced so a backlog (e.g. right after a restart) stays well under
    Telegram's request limit. If Telegram still answers with Retry-After, wait it out and leave
    the row — and the rest of the batch — for the next tick instead of dropping it. Any other
    failure (already gone, no rights, >48h old) drops the row: nothing more can be done with it."""
    while True:
        await asyncio.sleep(_SCHEDULED_DELETE_INTERVAL)
        try:
            due = db_man.get_due_deletes(db_man.unix_time())
        except Exception:
            continue
        for chat_id, message_id in due:
            try:
                await bot.delete_message(chat_id, message_id)
            except Exception as e:
                retry_after = int(getattr(e, 'retry_after', 0) or 0)
                if retry_after:
                    await asyncio.sleep(retry_after + 1)
                    break
            db_man.remove_scheduled_delete(chat_id, message_id)
            await asyncio.sleep(_SCHEDULED_DELETE_THROTTLE)


_raid_reminder_msg: dict[int, int] = {}
"""chat_id -> message id of the last anti-raid reminder posted there (chat_member_handler's
raid_reminder_task replaces it every 5 min; /raid_off and the panel toggle delete it)."""


async def _clear_raid_reminder(bot: Bot, chat_id: int) -> None:
    mid = _raid_reminder_msg.pop(chat_id, None)
    if mid is not None:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass


_NATIVE_ADMIN_TTL = 300
"""How long (seconds) a chat's fetched list of Telegram-native administrators is trusted before
it's re-fetched. Promotions/demotions also invalidate it directly (chat_member_handler), so the
TTL is only a safety net."""

_NATIVE_ADMIN_FAIL_TTL = 30

_native_admins: dict[int, tuple[float, frozenset[int]]] = {}
"""chat_id -> (fetched_at (monotonic), ids of creator + administrators)."""


async def _is_native_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Whether the user is a Telegram-native admin (creator/administrator, appointed through
    Telegram itself) of this chat — cheap enough to call on every message, since the admin list
    is fetched once per chat and cached (see _NATIVE_ADMIN_TTL). Distinct from the bot's own
    role table (permissions.is_staff) and from _native_admin_ok(), which does a precise one-off
    get_chat_member for a punishment command's target. On an API failure the previous list (or
    an empty one) is kept until the next TTL expiry, so a hiccup never throws here."""
    entry = _native_admins.get(chat_id)
    if entry is None or time.monotonic() - entry[0] > _NATIVE_ADMIN_TTL:
        try:
            ids = frozenset(m.user.id for m in await bot.get_chat_administrators(chat_id))
            entry = (time.monotonic(), ids)
        except Exception:
            # Keep stale data if we have it; otherwise cache "nobody" only briefly, so one API
            # hiccup doesn't leave the chat's real admins unprotected for the whole TTL.
            ids = entry[1] if entry else frozenset()
            entry = (time.monotonic() - _NATIVE_ADMIN_TTL + _NATIVE_ADMIN_FAIL_TTL, ids)
        _native_admins[chat_id] = entry
    return user_id in entry[1]


def invalidate_native_admins(chat_id: int) -> None:
    """Forget a chat's cached admin list — called on any member status change there, so a
    promotion/demotion takes effect on the next message rather than after the TTL."""
    _native_admins.pop(chat_id, None)


async def _deny(message: types.Message) -> None:
    """Post the 'no permission' notice (italic) and auto-remove it after 10 seconds."""
    await _ianswer(message, translator.get_string('mod_no_permission'), ttl=10)


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


def _user_label(user: types.User) -> str:
    """Short, human-readable label: full name, else @username, else id."""
    if user.full_name:
        return user.full_name
    if user.username:
        return f'@{user.username}'
    return str(user.id)


def _actor_mention(user: types.User) -> str:
    """Clickable mention for the staff member who issued the command."""
    return _user_mention(user.id, _user_label(user))


def _identity_html(display_name: str, username: str | None, target_id: int) -> str:
    """Announcement label for a punished user: a public-profile link if they have a @username,
    otherwise the display name, otherwise a bare id — always followed by the id."""
    if username:
        name = display_name or f'@{username}'
        return f'<a href="https://t.me/{username}">{_esc(name)}</a> (id {target_id})'
    if display_name:
        return f'{_esc(display_name)} (id {target_id})'
    return f'id {target_id}'


def _identity_plain(display_name: str, username: str | None, target_id: int) -> str:
    """Plain label for the log (the id is appended separately by _record_log)."""
    if display_name:
        return display_name
    if username:
        return f'@{username}'
    return f'id {target_id}'


def _probe_chats(message: types.Message) -> list[int]:
    """Chats to ask about a user: the current group first, then every other known chat."""
    if message.chat.type in _GROUP_TYPES:
        return [message.chat.id] + [c for c in db_man.get_bot_chats() if c != message.chat.id]
    return list(db_man.get_bot_chats())


async def _probe_user(bot: Bot, chat_ids: list[int], target_id: int) -> types.User | None:
    """Fetch a user's card by bare id through getChatMember on any chat the bot is in. Telegram
    answers for *any* valid user id here — status 'left' for someone who was never in that
    chat — so this resolves even accounts the bot has never seen. A hit is cached in seen_users,
    which is also what makes a later @username lookup for the same person succeed. Returns
    None only when no chat could answer (invalid id, or the bot is in no chat at all)."""
    for chat_id in chat_ids:
        try:
            user = (await bot.get_chat_member(chat_id, target_id)).user
        except Exception:
            continue
        db_man.remember_user(target_id, user.username, user.full_name or None)
        return user
    return None


async def _identity_for(bot: Bot, message: types.Message, command: CommandObject,
                        target_id: int) -> tuple[str, str | None]:
    """Resolve (display_name, username) for a punishment target.

    A reply / text-mention carries the identity directly; an @username was fetched live and
    cached during resolution, so it is read back from the cache; a *bare id* triggers a search
    across every chat the bot is in (current chat first, caching a hit for future @username
    lookups of the same person), then the cache, else unknown.
    """
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == target_id:
        return reply.from_user.full_name or '', reply.from_user.username
    for entity in message.entities or []:
        if entity.type == 'text_mention' and entity.user and entity.user.id == target_id:
            return entity.user.full_name or '', entity.user.username

    arg = (command.args or '').strip().split()[0] if command.args else ''
    if not arg.startswith('@'):
        user = await _probe_user(bot, _probe_chats(message), target_id)
        if user is not None:
            return user.full_name or '', user.username

    cached = db_man.find_user_by_id(target_id)
    if cached:
        return (cached[1] or ''), cached[0]
    return '', None


async def _punish_labels(bot: Bot, message: types.Message, command: CommandObject,
                         target_id: int) -> tuple[str, str]:
    """(html_label_for_announcement, plain_label_for_log) for a punishment target."""
    display_name, username = await _identity_for(bot, message, command, target_id)
    return (_identity_html(display_name, username, target_id),
            _identity_plain(display_name, username, target_id))


async def _ianswer_html(message: types.Message, body: str) -> types.Message:
    """Reply in the chat with pre-built HTML, wrapped in the standard italic styling."""
    return await message.answer(f'<i>{body}</i>', parse_mode='HTML')


async def _isend_html(bot: Bot, chat_id: int, body: str) -> types.Message:
    """Send pre-built HTML to a chat, wrapped in the standard italic styling."""
    return await bot.send_message(chat_id, f'<i>{body}</i>', parse_mode='HTML')


async def _get_target_or_reply(message: types.Message, command: CommandObject, bot: Bot) -> int | None:
    """Resolve the target user and reply with an error string when it cannot be determined."""
    target_provided = bool(message.reply_to_message) or bool((command.args or '').strip())
    try:
        target_id = await _resolve_target(message, command, bot)
    except _TargetRefused as e:
        await _refuse(message, e)
        return None
    if target_id is None:
        key = 'mod_user_not_found' if target_provided else 'mod_specify_user'
        await _ianswer(message, translator.get_string(key))
    return target_id


def _user_identity_labels(user: types.User) -> tuple[str, str]:
    """(html, plain) 'Full Name (@username, id N)' pair for a fetched chat member.
    The html version links @username to t.me/username instead of leaving it as a bare
    @mention, so announcing someone in a group notification doesn't ping them."""
    plain_details = [f'@{user.username}'] if user.username else []
    plain_details.append(f'id {user.id}')
    plain = f'{user.full_name} ({", ".join(plain_details)})'

    html_details = []
    if user.username:
        uname = _esc(user.username)
        html_details.append(f'<a href="https://t.me/{uname}">@{uname}</a>')
    html_details.append(f'id {user.id}')
    html_label = f'{_esc(user.full_name)} ({", ".join(html_details)})'
    return html_label, plain


async def _display_name_both(bot: Bot, chat_id: int, user_id: int) -> tuple[str, str]:
    """(html, plain) display labels for a chat member, one API call for both."""
    try:
        user = (await bot.get_chat_member(chat_id, user_id)).user
    except Exception:
        return f'id {user_id}', f'id {user_id}'
    return _user_identity_labels(user)


async def _display_name(bot: Bot, chat_id: int, user_id: int) -> str:
    return (await _display_name_both(bot, chat_id, user_id))[1]


async def _display_name_html(bot: Bot, chat_id: int, user_id: int) -> str:
    return (await _display_name_both(bot, chat_id, user_id))[0]


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
    return f'https://t.me/c/{str(chat.id)[4:]}/{message_id}'


def _report_recipients(chat_id: int, exclude_id: int) -> set[int]:
    """Who should receive a report from this chat: all owners + the chat's admins and moderators."""
    recipients = set(config.OWNERS)
    for uid, _role in db_man.list_roles(chat_id):
        recipients.add(uid)
    recipients.discard(exclude_id)
    return recipients


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


async def _native_admin_ok(message: types.Message, bot: Bot, target_id: int, *, everywhere: bool = False) -> bool:
    """Refuse to punish a Telegram-native admin (creator/administrator, appointed through
    Telegram itself, regardless of any bot role). Telegram would reject the ban/mute anyway;
    checking up front gives a clear answer instead of a silent `ban_failed` or a blocklist/mute
    row the chat can't actually enforce — and, for a *global* punishment (everywhere=True), stops
    a middleware that would otherwise delete that admin's every message in the chat where the
    API ban failed. Local commands check the command's own chat (one precise get_chat_member);
    global ones check every known chat through the cached admin lists (_is_native_admin). An
    unknown target (never in the chat) passes: the command then behaves exactly as before."""
    if everywhere:
        chat_ids = set(db_man.get_bot_chats())
        if message.chat.type in _GROUP_TYPES:
            chat_ids.add(message.chat.id)
        for chat_id in chat_ids:
            if await _is_native_admin(bot, chat_id, target_id):
                title = await _chat_title(bot, chat_id)
                text = translator.get_string('cannot_target_native_admin_in').format(title)
                await (_ianswer(message, text) if message.chat.type in _GROUP_TYPES else message.answer(text))
                return False
        return True
    if message.chat.type not in _GROUP_TYPES:
        return True
    try:
        member = await bot.get_chat_member(message.chat.id, target_id)
    except Exception:
        return True
    if member.status in ('creator', 'administrator'):
        await _ianswer(message, translator.get_string('cannot_target_native_admin'))
        return False
    return True


def _record_log(chat_id: int, actor: types.User, action_key: str, target_label: str,
                reason: str = '', target_id: int | None = None) -> None:
    """Append a staff action to the per-chat history.

    Owner actions are logged too, but the log only records the actor's name and id (never a role),
    so a bot owner appears there indistinguishable from a regular administrator.
    Both the actor and (when target_id is given) the target are recorded with their id.
    """
    if target_id is not None and f'id {target_id}' not in target_label:
        target_label = f'{target_label} (id {target_id})'
    actor_label = f'{_user_label(actor)} (id {actor.id})'
    text = f'{actor_label} → {translator.get_string(action_key)} → {target_label}'
    if reason:
        text += ' | ' + translator.get_string('log_reason').format(reason)
    db_man.add_log(chat_id, text, target_id, action_key)


def _log_action(message: types.Message, action_key: str, target_label: str,
                reason: str = '', target_id: int | None = None) -> None:
    _record_log(message.chat.id, message.from_user, action_key, target_label, reason, target_id)


def _log_global(message: types.Message, action_key: str, target_label: str,
                reason: str = '', target_id: int | None = None, *, everywhere: bool = False) -> None:
    """Log an action issued by a command that may also be sent from DM.

    everywhere=True is for *global* actions (gban/gmute and their reversals): the entry goes
    into the log of every chat the bot knows (bot_chats, plus the current group), so it shows
    up in each chat's staff log and /punl — including when an owner issues it from DM.

    everywhere=False (local actions that share a code path with global ones, e.g. _run_mute
    with glob=False): in a group, log to that chat only; from DM, to every chat the issuer
    administers — owners have no "own" chats, so nothing is logged for them.
    """
    if everywhere:
        chat_ids = set(db_man.get_bot_chats())
        if message.chat.type in _GROUP_TYPES:
            chat_ids.add(message.chat.id)
        for chat_id in chat_ids:
            _record_log(chat_id, message.from_user, action_key, target_label, reason, target_id)
        return
    if message.chat.type in _GROUP_TYPES:
        _record_log(message.chat.id, message.from_user, action_key, target_label, reason, target_id)
        return
    if permissions.is_owner(message.from_user.id):
        return
    for chat_id in db_man.get_admin_chats(message.from_user.id):
        _record_log(chat_id, message.from_user, action_key, target_label, reason, target_id)


async def _parse_ban(message: types.Message, command: CommandObject, bot: Bot) -> tuple[int | None, str]:
    """Resolve the ban target and the optional reason from a reply or `<target> [reason]`."""
    args = (command.args or '').strip()

    if message.reply_to_message and message.reply_to_message.from_user:
        return _check_user_target(message.reply_to_message.from_user), _cap_reason(args)

    for entity in message.entities or []:
        if entity.type == 'text_mention' and entity.user:
            return _check_user_target(entity.user), _cap_reason(_entity_tail(message.text or '', entity))

    if not args:
        return None, ''

    parts = args.split(maxsplit=1)
    token = parts[0]
    reason = _cap_reason(parts[1].strip() if len(parts) > 1 else '')

    numeric = _parse_id_token(token)
    if numeric is not None:
        return numeric, reason

    if token.startswith('@'):
        return await _resolve_username_token(bot, token), reason

    return None, reason


_REASON_MAX = 300


def _cap_reason(reason: str) -> str:
    """Reasons are echoed into announcements, DMs and log lines; an essay-length one would push
    those past Telegram's 4096 limit and the send would fail after the ban was already recorded."""
    return reason if len(reason) <= _REASON_MAX else reason[:_REASON_MAX - 1] + '…'


async def _ban_target_or_reply(message: types.Message, command: CommandObject, bot: Bot) -> tuple[int | None, str]:
    try:
        target_id, reason = await _parse_ban(message, command, bot)
    except _TargetRefused as e:
        await _refuse(message, e)
        return None, ''
    if target_id == bot.id:
        await _ianswer(message, translator.get_string('cannot_target_bot'))
        return None, reason
    if target_id is None:
        provided = bool(message.reply_to_message) or bool((command.args or '').strip())
        key = 'mod_user_not_found' if provided else 'mod_specify_user'
        await _ianswer(message, translator.get_string(key))
    return target_id, reason


def _clear_captcha_state(chat_id: int, user_id: int) -> None:
    """Stop the captcha subsystem from managing this user in this chat: drop any pending-captcha
    bookkeeping and cancel a scheduled 24h-kick auto-unban. Call right after a real ban, so a
    later captcha pass (which blindly restores permissions in every pending chat) or the kick
    task's own unban (which only cares that 24h passed, not that staff banned them meanwhile)
    can't quietly undo it."""
    db_man.remove_pending_chat(user_id, chat_id)
    db_man.remove_pending_unban(chat_id, user_id)


def _clear_captcha_state_everywhere(user_id: int) -> None:
    db_man.clear_pending_chats(user_id)
    db_man.remove_all_pending_unbans(user_id)


def _dm_text(key: str, chat_title: str, message: types.Message, reason: str = '', dur_words: str = '') -> str:
    role_word = _actor_role_word(message.chat.id, message.from_user.id)
    text = translator.get_string(key).format(chat_title, role_word, _user_label(message.from_user))
    if dur_words:
        text += translator.get_string('mute_for').format(dur_words)
    if reason:
        text += ', ' + translator.get_string('log_reason').format(reason)
    return text


async def _dm_target(bot: Bot, target_id: int, text: str) -> None:
    try:
        await bot.send_message(target_id, text)
    except Exception:
        pass


async def _chat_title(bot: Bot, chat_id: int) -> str:
    try:
        return (await bot.get_chat(chat_id)).title or str(chat_id)
    except Exception:
        return str(chat_id)


async def _chat_title_or_none(bot: Bot, chat_id: int) -> str | None:
    """Like _chat_title, but returns None instead of falling back to the numeric id string when
    the chat can't be resolved (bot no longer a member, chat deleted, etc.) -- lets /punl tell a
    genuinely known chat title apart from an unresolvable one, so it can omit the '[chat]'
    prefix instead of showing a meaningless bare number."""
    try:
        chat = await bot.get_chat(chat_id)
        return chat.title or None
    except Exception:
        return None


def _accessible_chats(user_id: int) -> list[int]:
    if permissions.is_owner(user_id):
        return db_man.get_bot_chats()
    return db_man.get_admin_chats(user_id)


async def _entity_name(bot: Bot, entity_id: int) -> str:
    """`Name (@username, id N)` for a user or channel by id — title for channels, full name for
    users; the @username is included whenever the entity has one."""
    try:
        chat = await bot.get_chat(entity_id)
        name = getattr(chat, 'title', None) or getattr(chat, 'full_name', None) or getattr(chat, 'first_name', None)
        if name:
            details = [f'@{chat.username}'] if chat.username else []
            details.append(f'id {entity_id}')
            return f'{name} ({", ".join(details)})'
        if chat.username:
            return f'@{chat.username} (id {entity_id})'
        return f'id {entity_id}'
    except Exception:
        return f'id {entity_id}'


async def _global_name(bot: Bot, user_id: int) -> str:
    """Same as _entity_name; kept as a separate name for the user-facing call sites."""
    return await _entity_name(bot, user_id)


_DURATION_RE = re.compile(r'^(\d+)(mo|[smhdw])$')
_UNIT_SECONDS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800, 'mo': 2592000}

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
_DEFAULT_PERMS = frozenset({'messages', 'media', 'stickers', 'polls', 'links'})

try:
    _VALID_PERM_FIELDS = set(ChatPermissions.model_fields)
except AttributeError:
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
    if _VALID_PERM_FIELDS:
        fields = {f: v for f, v in fields.items() if f in _VALID_PERM_FIELDS}
    return ChatPermissions(**fields)


def _perm_supported(key: str) -> bool:
    """Whether this aiogram build knows the permission's field(s) (else the toggle is hidden)."""
    if not _VALID_PERM_FIELDS:
        return True
    return any(field in _VALID_PERM_FIELDS for field in _PERM_GROUPS[key])


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
    return 0 if count == 1 else 1


def _human_duration_words(token: str) -> str:
    """Turn a duration token like `5h` into spelled-out words, e.g. `5 часов` / `5 hours`."""
    match = _DURATION_RE.match(token.lower())
    if not match:
        return token
    count, unit = int(match.group(1)), match.group(2)
    forms = translator.get_string(_DUR_UNIT_KEY[unit]).split('|')
    word = forms[min(_plural_index(count), len(forms) - 1)]
    return f'{count} {word}'


def _seconds_to_words(seconds: int) -> str:
    """Spelled-out words for a raw seconds value (e.g. from an admin-panel numeric setting),
    picking the largest unit that divides it evenly. _human_duration_words only accepts a single
    token like `5h`, and _human_time can emit multi-part strings like `1h 30m` that don't
    round-trip through it — this always works because such values were themselves parsed from a
    token via _parse_duration in the first place."""
    for unit, unit_seconds in (('mo', 2592000), ('w', 604800), ('d', 86400), ('h', 3600), ('m', 60)):
        if seconds and seconds % unit_seconds == 0:
            return _human_duration_words(f'{seconds // unit_seconds}{unit}')
    return _human_duration_words(f'{seconds}s')


def _human_elapsed(seconds: int) -> str:
    """Spelled-out elapsed time between two arbitrary timestamps, e.g. '1 час 15 минут' —
    unlike _seconds_to_words (exact single unit, meant for admin-configured settings), this
    shows up to the two largest non-zero units so arbitrary real-world gaps read naturally."""
    seconds = max(0, int(seconds))
    parts = []
    remaining = seconds
    for unit, unit_seconds in (('d', 86400), ('h', 3600), ('m', 60), ('s', 1)):
        if remaining >= unit_seconds:
            count = remaining // unit_seconds
            remaining -= count * unit_seconds
            parts.append(_human_duration_words(f'{count}{unit}'))
        if len(parts) == 2:
            break
    return ' '.join(parts) if parts else _human_duration_words('0s')


_COOLDOWN_COMMANDS = ['ban', 'mute', 'unmute', 'unban', 'delete', 'delete_user']


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
