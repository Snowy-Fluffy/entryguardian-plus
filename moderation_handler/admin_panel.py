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

from typing import Any
from datetime import datetime
from aiogram import F, types, Bot
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import permissions
from .common import (
    router, db_man, translator,
    _accessible_chats, _chat_title, _display_name, _display_name_html,
    _entity_name, _global_name, _resolve_username_token,
    _record_log, _human_time, _COOLDOWN_COMMANDS,
    _chat_perm_set, _perm_supported, _PERM_GROUPS, _PERM_ORDER,
    _parse_duration,
)
from .chat_admin import _leave_chat

_panel_state: dict[int, dict[str, Any]] = {}

_log_search: dict[int, str] = {}

_gban_search: dict[int, str] = {}

_LOG_PAGE_SIZE = 10

_ROLE_ACTIONS = {
    'aa': ('admin', 'is_admin', 'already_admin', 'admin_added', 'log_add_adm'),
    'am': ('moderator', 'is_moderator', 'already_mod', 'mod_added', 'log_add_mod'),
}


def _can_access_chat(user_id: int, chat_id: int) -> bool:
    return permissions.is_owner(user_id) or db_man.is_admin(chat_id, user_id)


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
        [InlineKeyboardButton(
            text=translator.get_string(
                'admin_btn_kick_on' if db_man.is_kick_enabled(chat_id) else 'admin_btn_kick_off'
            ),
            callback_data=f'adm:kick:{chat_id}',
        )],
        [InlineKeyboardButton(
            text=translator.get_string(
                'admin_btn_delsys_on' if db_man.is_delete_system_messages(chat_id) else 'admin_btn_delsys_off'
            ),
            callback_data=f'adm:delsys:{chat_id}',
        )],
        [InlineKeyboardButton(text=translator.get_string('admin_btn_antispam'), callback_data=f'adm:asp:{chat_id}')],
        [InlineKeyboardButton(
            text=translator.get_string(
                'admin_btn_autoaccept_on' if db_man.is_auto_accept(chat_id) else 'admin_btn_autoaccept_off'
            ),
            callback_data=f'adm:jreq:{chat_id}',
        )],
        [InlineKeyboardButton(text=translator.get_string('admin_btn_perms'), callback_data=f'adm:perm:{chat_id}')],
        [InlineKeyboardButton(text=translator.get_string('admin_btn_gbans'), callback_data=f'adm:gb:{chat_id}')],
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


_GBAN_PAGE_SIZE = 10


async def _gban_matches(bot: Bot, kind: str, oid: int, needle: str) -> bool:
    """Whether a blocklist entry matches a search query — id always checked locally; for
    users the cached username/display name (seen_users, no API call) is also checked; for
    channels (far fewer of them in practice) a live getChat lookup is used instead, since
    there's no local title cache for channels."""
    if needle in str(oid):
        return True
    if kind == 'u':
        cached = db_man.find_user_by_id(oid)
        if cached:
            username, display_name = cached
            if (username and needle in username.lower()) or (display_name and needle in display_name.lower()):
                return True
        return False
    return needle in (await _entity_name(bot, oid)).lower()


async def _build_gbans_page(bot: Bot, chat_id: int, page: int, query: str = '') -> tuple[str, InlineKeyboardMarkup]:
    """Paged view of the global ban list (blocklisted users and channels), optionally filtered
    by a search query (id, username or cached display name for users; id or title for
    channels)."""
    items = [('u', uid) for uid in db_man.get_blocklist()] + \
            [('c', cid) for cid in db_man.get_channel_blocklist()]
    if query:
        needle = query.strip().lower().lstrip('@')
        items = [item for item in items if await _gban_matches(bot, item[0], item[1], needle)]

    back = [InlineKeyboardButton(text=translator.get_string('admin_btn_back'), callback_data=f'adm:c:{chat_id}')]
    if not items:
        empty = translator.get_string('gban_search_empty').format(query) if query else translator.get_string('gban_empty')
        return empty, InlineKeyboardMarkup(inline_keyboard=[_gban_action_row(chat_id, query), back])

    pages = (len(items) + _GBAN_PAGE_SIZE - 1) // _GBAN_PAGE_SIZE
    page = max(0, min(page, pages - 1))
    chunk = items[page * _GBAN_PAGE_SIZE:(page + 1) * _GBAN_PAGE_SIZE]
    header = translator.get_string('gban_search_header').format(query, len(items)) if query \
        else translator.get_string('gban_header').format(len(items))
    lines = [header, translator.get_string('log_page_info').format(page + 1, pages, len(items)), '']
    for kind, oid in chunk:
        icon = '📢' if kind == 'c' else '👤'
        lines.append(f'{icon} {await _entity_name(bot, oid)}')

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text=translator.get_string('log_prev'), callback_data=f'adm:gbpg:{chat_id}:{page - 1}'))
    nav.append(InlineKeyboardButton(text=f'{page + 1}/{pages}', callback_data='adm:noop'))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text=translator.get_string('log_next'), callback_data=f'adm:gbpg:{chat_id}:{page + 1}'))
    return '\n'.join(lines), InlineKeyboardMarkup(inline_keyboard=[nav, _gban_action_row(chat_id, query), back])


def _gban_action_row(chat_id: int, query: str) -> list[InlineKeyboardButton]:
    """The search / clear-search button row shown under the global ban list."""
    row = [InlineKeyboardButton(text=translator.get_string('log_search_btn'), callback_data=f'adm:gbs:{chat_id}')]
    if query:
        row.append(InlineKeyboardButton(text=translator.get_string('log_clear_btn'), callback_data=f'adm:gbclr:{chat_id}'))
    return row


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


def _build_antispam_menu(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Per-chat repeated-message antispam settings: on/off, repeat threshold, time window, mute
    duration, and whether to DM staff — same sub-menu shape as _build_cooldown_menu."""
    settings = db_man.get_antispam_settings(chat_id)
    buttons = [
        [InlineKeyboardButton(
            text=translator.get_string('antispam_btn_on' if settings['enabled'] else 'antispam_btn_off'),
            callback_data=f'adm:asptog:{chat_id}',
        )],
        [InlineKeyboardButton(
            text=translator.get_string('antispam_field_count').format(settings['count']),
            callback_data=f'adm:aspset:{chat_id}:count',
        )],
        [InlineKeyboardButton(
            text=translator.get_string('antispam_field_window').format(_human_time(settings['window'])),
            callback_data=f'adm:aspset:{chat_id}:window',
        )],
        [InlineKeyboardButton(
            text=translator.get_string('antispam_field_mute').format(_human_time(settings['mute_seconds'])),
            callback_data=f'adm:aspset:{chat_id}:mute',
        )],
        [InlineKeyboardButton(
            text=translator.get_string('antispam_btn_notify_on' if settings['notify'] else 'antispam_btn_notify_off'),
            callback_data=f'adm:aspnotify:{chat_id}',
        )],
        [InlineKeyboardButton(
            text=translator.get_string('antispam_btn_unicode_on' if settings['unicode_guard'] else 'antispam_btn_unicode_off'),
            callback_data=f'adm:aspuc:{chat_id}',
        )],
        [InlineKeyboardButton(text=translator.get_string('admin_btn_back'), callback_data=f'adm:c:{chat_id}')],
    ]
    return translator.get_string('antispam_title'), InlineKeyboardMarkup(inline_keyboard=buttons)


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
    rows = db_man.get_logs(chat_id)
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

    if action == 'noop':
        await callback.answer()
        return

    chat_id = int(parts[2])
    if not _can_access_chat(user_id, chat_id):
        await callback.answer(translator.get_string('admin_access_lost'), show_alert=True)
        return

    if action == 'c':
        _panel_state.pop(user_id, None)
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await _edit(callback.message, text, markup)
    elif action == 'lg':
        _panel_state.pop(user_id, None)
        _log_search[user_id] = ''
        text, markup = _render_logs_page(chat_id, '', 0)
        await _edit(callback.message, text, markup)
    elif action == 'lgpg':
        text, markup = _render_logs_page(chat_id, _log_search.get(user_id, ''), int(parts[3]))
        await _edit(callback.message, text, markup)
    elif action == 'lgs':
        _panel_state[user_id] = {'action': 'logsearch', 'chat_id': chat_id}
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=translator.get_string('admin_btn_cancel'), callback_data=f'adm:lg:{chat_id}')
        ]])
        await _edit(callback.message, translator.get_string('log_search_prompt'), markup)
    elif action == 'lgclr':
        _panel_state.pop(user_id, None)
        _log_search[user_id] = ''
        text, markup = _render_logs_page(chat_id, '', 0)
        await _edit(callback.message, text, markup)
    elif action == 'gb':
        _panel_state.pop(user_id, None)
        _gban_search[user_id] = ''
        text, markup = await _build_gbans_page(bot, chat_id, 0)
        await _edit(callback.message, text, markup)
    elif action == 'gbpg':
        text, markup = await _build_gbans_page(bot, chat_id, int(parts[3]), _gban_search.get(user_id, ''))
        await _edit(callback.message, text, markup)
    elif action == 'gbs':
        _panel_state[user_id] = {'action': 'gbansearch', 'chat_id': chat_id}
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=translator.get_string('admin_btn_cancel'), callback_data=f'adm:gb:{chat_id}')
        ]])
        await _edit(callback.message, translator.get_string('gban_search_prompt'), markup)
    elif action == 'gbclr':
        _panel_state.pop(user_id, None)
        _gban_search[user_id] = ''
        text, markup = await _build_gbans_page(bot, chat_id, 0)
        await _edit(callback.message, text, markup)
    elif action == 'cap':
        new_state = not db_man.is_captcha_enabled(chat_id)
        db_man.set_captcha_enabled(chat_id, new_state)
        _record_log(chat_id, callback.from_user, 'log_captcha',
                    translator.get_string('captcha_state_on' if new_state else 'captcha_state_off'))
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await _edit(callback.message, text, markup)
    elif action == 'raid':
        new_state = not db_man.is_raid_mode(chat_id)
        db_man.set_raid_mode(chat_id, new_state)
        _record_log(chat_id, callback.from_user, 'log_raid',
                    translator.get_string('raid_state_on' if new_state else 'raid_state_off'))
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await _edit(callback.message, text, markup)
    elif action == 'chan':
        new_state = not db_man.is_channels_banned(chat_id)
        db_man.set_channels_banned(chat_id, new_state)
        _record_log(chat_id, callback.from_user, 'log_channels',
                    translator.get_string('channels_state_on' if new_state else 'channels_state_off'))
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await _edit(callback.message, text, markup)
    elif action == 'kick':
        new_state = not db_man.is_kick_enabled(chat_id)
        db_man.set_kick_enabled(chat_id, new_state)
        _record_log(chat_id, callback.from_user, 'log_kick',
                    translator.get_string('kick_state_on' if new_state else 'kick_state_off'))
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await _edit(callback.message, text, markup)
    elif action == 'delsys':
        new_state = not db_man.is_delete_system_messages(chat_id)
        db_man.set_delete_system_messages(chat_id, new_state)
        _record_log(chat_id, callback.from_user, 'log_delsys',
                    translator.get_string('delsys_state_on' if new_state else 'delsys_state_off'))
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await _edit(callback.message, text, markup)
    elif action == 'jreq':
        new_state = not db_man.is_auto_accept(chat_id)
        db_man.set_auto_accept(chat_id, new_state)
        _record_log(chat_id, callback.from_user, 'log_autoaccept',
                    translator.get_string('autoaccept_state_on' if new_state else 'autoaccept_state_off'))
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await _edit(callback.message, text, markup)
    elif action == 'perm':
        text, markup = _build_perms_menu(chat_id)
        await _edit(callback.message, text, markup)
    elif action == 'permt':
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
    elif action == 'stop':
        if not permissions.is_owner(user_id):
            await callback.answer(translator.get_string('admin_no_access'), show_alert=True)
            return
        db_man.set_chat_stopped(chat_id, not db_man.is_chat_stopped(chat_id))
        text, markup = await _build_chat_menu(bot, chat_id, user_id)
        await _edit(callback.message, text, markup)
    elif action in ('leave', 'leavego'):
        if not permissions.is_owner(user_id):
            await callback.answer(translator.get_string('admin_no_access'), show_alert=True)
            return
        if action == 'leave':
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=translator.get_string('leave_confirm_btn'), callback_data=f'adm:leavego:{chat_id}')],
                [InlineKeyboardButton(text=translator.get_string('admin_btn_cancel'), callback_data=f'adm:c:{chat_id}')],
            ])
            await _edit(callback.message, translator.get_string('leave_confirm_text'), markup)
        else:
            await _leave_chat(bot, chat_id)
            _panel_state.pop(user_id, None)
            text, markup = await _build_chat_list(bot, user_id)
            await _edit(callback.message, text, markup)
            await callback.answer(translator.get_string('leave_done'), show_alert=True)
            return
    elif action == 'rl':
        _panel_state[user_id] = {'action': 'rules', 'chat_id': chat_id}
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=translator.get_string('admin_btn_cancel'), callback_data=f'adm:c:{chat_id}')
        ]])
        await _edit(callback.message, translator.get_string('admin_prompt_rules'), markup)
    elif action == 'cd':
        text, markup = _build_cooldown_menu(chat_id)
        await _edit(callback.message, text, markup)
    elif action == 'cdset':
        cmd = parts[3]
        _panel_state[user_id] = {'action': 'cooldown', 'chat_id': chat_id, 'cmd': cmd}
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=translator.get_string('admin_btn_cancel'), callback_data=f'adm:cd:{chat_id}')
        ]])
        await _edit(callback.message, translator.get_string('admin_prompt_cooldown').format(cmd), markup)
    elif action == 'asp':
        _panel_state.pop(user_id, None)
        text, markup = _build_antispam_menu(chat_id)
        await _edit(callback.message, text, markup)
    elif action == 'asptog':
        new_state = not db_man.get_antispam_settings(chat_id)['enabled']
        db_man.set_antispam_field(chat_id, 'enabled', new_state)
        _record_log(chat_id, callback.from_user, 'log_antispam_toggle',
                    translator.get_string('antispam_state_on' if new_state else 'antispam_state_off'))
        text, markup = _build_antispam_menu(chat_id)
        await _edit(callback.message, text, markup)
    elif action == 'aspnotify':
        new_state = not db_man.get_antispam_settings(chat_id)['notify']
        db_man.set_antispam_field(chat_id, 'notify', new_state)
        _record_log(chat_id, callback.from_user, 'log_antispam_notify',
                    translator.get_string('antispam_state_on' if new_state else 'antispam_state_off'))
        text, markup = _build_antispam_menu(chat_id)
        await _edit(callback.message, text, markup)
    elif action == 'aspuc':
        new_state = not db_man.get_antispam_settings(chat_id)['unicode_guard']
        db_man.set_antispam_field(chat_id, 'unicode_guard', new_state)
        _record_log(chat_id, callback.from_user, 'log_antispam_unicode',
                    translator.get_string('antispam_state_on' if new_state else 'antispam_state_off'))
        text, markup = _build_antispam_menu(chat_id)
        await _edit(callback.message, text, markup)
    elif action == 'aspset':
        field = parts[3]
        _panel_state[user_id] = {'action': 'antispam', 'chat_id': chat_id, 'field': field}
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=translator.get_string('admin_btn_cancel'), callback_data=f'adm:asp:{chat_id}')
        ]])
        await _edit(callback.message, translator.get_string(f'antispam_prompt_{field}'), markup)
    elif action in ('cb', 'cbadd', 'cbdel'):
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
        else:
            text, markup = await _build_command_ban_menu(bot, chat_id)
            await _edit(callback.message, text, markup)
    elif action in ('aa', 'ra', 'am', 'rm'):
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
    return await _resolve_username_token(bot, token)


async def _apply_panel_role(bot: Bot, actor: types.User, chat_id: int, action: str, target_id: int) -> str:
    if target_id == bot.id:
        return translator.get_string('cannot_target_bot')
    name = await _display_name(bot, chat_id, target_id)

    if action in _ROLE_ACTIONS:
        role, checker, already_key, done_key, log_key = _ROLE_ACTIONS[action]
        if getattr(db_man, checker)(chat_id, target_id):
            return translator.get_string(already_key).format(name)
        db_man.set_role(chat_id, target_id, role)
        _record_log(chat_id, actor, log_key, name)
        return translator.get_string(done_key).format(name)

    if action == 'ra':
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

    if state['action'] == 'gbansearch':
        _panel_state.pop(user_id, None)
        query = (message.text or '').strip()
        _gban_search[user_id] = query
        text, markup = await _build_gbans_page(bot, chat_id, 0, query)
        await message.answer(text, reply_markup=markup)
        return

    if state['action'] == 'cmdban':
        if not permissions.is_owner(user_id):
            _panel_state.pop(user_id, None)
            await message.answer(translator.get_string('admin_no_access'))
            return
        target_id = await _resolve_panel_target(message, bot)
        if target_id is None:
            await message.answer(translator.get_string('admin_bad_user'))
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
                await message.answer(translator.get_string('cooldown_bad_value'))
                return
        _panel_state.pop(user_id, None)
        db_man.set_cooldown(chat_id, cmd, seconds)
        value = _human_time(seconds) if seconds else translator.get_string('cooldown_off')
        _record_log(chat_id, message.from_user, 'log_cooldown', f'/{cmd} = {value}')
        await message.answer(translator.get_string('cooldown_set').format(cmd))
        text, markup = _build_cooldown_menu(chat_id)
        await message.answer(text, reply_markup=markup)
        return

    if state['action'] == 'antispam':
        field = state['field']
        raw = (message.text or '').strip().lower()
        if field == 'count':
            if not raw.isdigit() or int(raw) < 2:
                await message.answer(translator.get_string('antispam_bad_count'))
                return
            value = int(raw)
        elif field == 'window':
            seconds = _parse_duration(raw)
            if seconds is None or not (60 <= seconds <= 172800):
                await message.answer(translator.get_string('antispam_bad_window'))
                return
            value = seconds
        else:
            seconds = _parse_duration(raw)
            if seconds is None or seconds < 60:
                await message.answer(translator.get_string('antispam_bad_mute'))
                return
            value = seconds
        _panel_state.pop(user_id, None)
        db_field = {'count': 'count', 'window': 'window', 'mute': 'mute_seconds'}[field]
        db_man.set_antispam_field(chat_id, db_field, value)
        _record_log(chat_id, message.from_user, 'log_antispam_set', f'{field} = {value}')
        await message.answer(translator.get_string('antispam_set'))
        text, markup = _build_antispam_menu(chat_id)
        await message.answer(text, reply_markup=markup)
        return

    target_id = await _resolve_panel_target(message, bot)
    if target_id is None:
        await message.answer(translator.get_string('admin_bad_user'))
        return

    _panel_state.pop(user_id, None)
    result = await _apply_panel_role(bot, message.from_user, chat_id, state['action'], target_id)
    await message.answer(result)
    text, markup = await _build_chat_menu(bot, chat_id, user_id)
    await message.answer(text, reply_markup=markup)
