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
    _delete_silently, _ianswer, _ianswer_html, _get_target_or_reply, _is_bot_target,
    _log_action, _reply_channel, _require, _display_name_both, _display_name_html,
    _spawn, _delete_after,
)


async def _check_preconditions(message: types.Message) -> bool:
    """Group-only + 'can manage roles' check shared by every role command."""
    if not await _require(message, permissions.can_manage_roles(db_man, message.chat.id, message.from_user.id)):
        return False
    if _reply_channel(message) is not None:
        await _ianswer(message, translator.get_string('cannot_target_channel'))
        return False
    return True


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
    name_html, name = await _display_name_both(bot, message.chat.id, target_id)
    if db_man.is_admin(message.chat.id, target_id):
        await _ianswer_html(message, translator.get_string('already_admin').format(name_html))
        return
    db_man.set_role(message.chat.id, target_id, 'admin')
    _log_action(message, 'log_add_adm', name)
    await _ianswer_html(message, translator.get_string('admin_added').format(name_html))


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
    name_html, name = await _display_name_both(bot, message.chat.id, target_id)
    if not db_man.is_admin(message.chat.id, target_id):
        await _ianswer_html(message, translator.get_string('not_admin').format(name_html))
        return
    db_man.remove_role(message.chat.id, target_id)
    _log_action(message, 'log_del_adm', name)
    await _ianswer_html(message, translator.get_string('admin_removed').format(name_html))


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
    name_html, name = await _display_name_both(bot, message.chat.id, target_id)
    if db_man.is_moderator(message.chat.id, target_id):
        await _ianswer_html(message, translator.get_string('already_mod').format(name_html))
        return
    db_man.set_role(message.chat.id, target_id, 'moderator')
    _log_action(message, 'log_add_mod', name)
    await _ianswer_html(message, translator.get_string('mod_added').format(name_html))


@router.message(Command('del_mod'))
async def del_moderator(message: types.Message, command: CommandObject, bot: Bot) -> None:
    await _delete_silently(message)
    if not await _check_preconditions(message):
        return
    target_id = await _get_target_or_reply(message, command, bot)
    if target_id is None:
        return
    name_html, name = await _display_name_both(bot, message.chat.id, target_id)
    if not db_man.is_moderator(message.chat.id, target_id):
        await _ianswer_html(message, translator.get_string('not_mod').format(name_html))
        return
    db_man.remove_role(message.chat.id, target_id)
    _log_action(message, 'log_del_mod', name)
    await _ianswer_html(message, translator.get_string('mod_removed').format(name_html))


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

    admin_names = [await _display_name_html(bot, message.chat.id, uid) for uid in admins]
    mod_names = [await _display_name_html(bot, message.chat.id, uid) for uid in mods]

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
    await message.answer(
        '\n'.join(lines),
        parse_mode='HTML',
        link_preview_options=types.LinkPreviewOptions(is_disabled=True),
    )


@router.message(Command('help'))
async def help_command(message: types.Message) -> None:
    await _delete_silently(message)
    user_id = message.from_user.id
    if message.chat.type in _GROUP_TYPES:
        is_admin = permissions.can_manage_roles(db_man, message.chat.id, user_id)
        is_mod = permissions.is_staff(db_man, message.chat.id, user_id)
    else:
        is_admin = permissions.is_owner(user_id) or bool(db_man.get_admin_chats(user_id))
        is_mod = is_admin or bool(db_man.get_moderator_chats(user_id))
    parts = [translator.get_string('help_header')]
    if is_admin:
        parts.append(translator.get_string('help_admin'))
    if is_mod:
        parts.append(translator.get_string('help_mod'))
    parts.append(translator.get_string('help_everyone'))
    sent = await message.answer('\n\n'.join(parts))
    _spawn(_delete_after(message.bot, message.chat.id, sent.message_id, 60))
