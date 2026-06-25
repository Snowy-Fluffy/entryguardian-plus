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

from aiogram import Router, types, Bot
from aiogram.filters import CommandStart
from aiogram.types import ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton
from dbmanager import DBManager
from translator import Translator
import chat_member_handler
import moderation_handler
import session_manager
import config
import asyncio
import random

router = Router()
db_man = DBManager()
translator = Translator(config.LOCALE)

_attempts_left: dict[int, int] = {}


async def _unrestrict_user(bot: Bot, user_id: int) -> None:
    for chat_id in db_man.get_pending_chats(user_id):
        try:
            muted, until = db_man.effective_mute(chat_id, user_id)
            if muted:
                await bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=user_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until or None,
                )
            else:
                await bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=user_id,
                    permissions=moderation_handler.build_chat_permissions(chat_id),
                )
        except Exception:
            pass
    db_man.clear_pending_chats(user_id)


def _build_link_msg(session_id: str) -> tuple[str, InlineKeyboardMarkup]:
    url = f'{config.CAPTCHA_BASE_URL}/{session_id}'
    text = translator.get_string('captcha_link_msg')
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=translator.get_string('captcha_button'), url=url)
    ]])
    return text, markup


@router.message(CommandStart())
async def start_handler(message: types.Message) -> None:
    user_id = message.from_user.id

    if message.chat.id < 0:
        return

    if db_man.is_blocklisted(user_id):
        await message.answer(translator.get_string('blocked_msg'))
        return

    if db_man.is_user_blocked(user_id):
        await message.answer(translator.get_string('temp_block'))
        return

    if db_man.is_user_allowed(user_id):
        await message.answer(translator.get_string('already_verified'))
        return

    pending = session_manager.get_pending_session(user_id)
    if pending:
        text, markup = _build_link_msg(pending)
        await message.answer(text, reply_markup=markup)
        return

    captcha_type = random.choice(config.CAPTCHA_TYPES)
    session_id = session_manager.create_session(user_id, captcha_type)
    _attempts_left[user_id] = config.MAX_ATTEMPTS
    text, markup = _build_link_msg(session_id)
    await message.answer(text, reply_markup=markup)


@router.message()
async def handle_code_attempt(message: types.Message, bot: Bot) -> None:
    if message.chat.type != 'private':
        return

    user_id = message.from_user.id

    if db_man.is_user_blocked(user_id):
        await message.answer(translator.get_string('temp_block'))
        return

    if db_man.is_user_allowed(user_id):
        return

    if not session_manager.has_any_session(user_id):
        await message.answer(translator.get_string('no_captcha_requested'))
        return

    code = (message.text or '').strip().upper()
    session_id = session_manager.find_by_code(user_id, code)

    if session_id is None:
        if user_id not in _attempts_left:
            _attempts_left[user_id] = config.MAX_ATTEMPTS
        _attempts_left[user_id] -= 1
        if _attempts_left[user_id] > 0:
            await message.answer(translator.get_string('incorrect'))
        else:
            db_man.temp_block(user_id)
            session_manager.remove_user_sessions(user_id)
            _attempts_left.pop(user_id, None)
            await message.answer(translator.get_string('temp_block'))
        return

    await message.answer(translator.get_string('verified'))
    db_man.verify_user(user_id)
    session_manager.remove_session(session_id)
    _attempts_left.pop(user_id, None)
    await _unrestrict_user(bot, user_id)
    await chat_member_handler.delete_welcome_msg(bot, user_id)


async def session_expiry_task(bot: Bot) -> None:
    while True:
        await asyncio.sleep(30)
        for user_id in session_manager.cleanup_expired():
            try:
                await bot.send_message(user_id, translator.get_string('captcha_expired'))
            except Exception:
                pass
