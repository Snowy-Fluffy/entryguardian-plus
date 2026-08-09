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
from aiogram.filters import Command
import permissions
from .common import (
    router, db_man, translator, _GROUP_TYPES,
    _delete_silently, _ianswer, _require, _log_action,
)


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
    await _ianswer(message, text, disable_preview=True)


@router.message(Command('stopchat'))
async def stopchat_cmd(message: types.Message, bot: Bot) -> None:
    if message.chat.type not in _GROUP_TYPES or not permissions.is_owner(message.from_user.id):
        return
    await _delete_silently(message)
    db_man.set_chat_stopped(message.chat.id, True)
    await _ianswer(message, translator.get_string('stopchat_msg'))


@router.message(Command('startchat'))
async def startchat_cmd(message: types.Message, bot: Bot) -> None:
    if message.chat.type not in _GROUP_TYPES or not permissions.is_owner(message.from_user.id):
        return
    await _delete_silently(message)
    db_man.set_chat_stopped(message.chat.id, False)
    await _ianswer(message, translator.get_string('startchat_msg'))


@router.message(Command('leavechat'))
async def leavechat_cmd(message: types.Message, bot: Bot) -> None:
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
