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
import re
from moderation_handler.common import _flood_safe, _chat_title, _human_elapsed
from translator import use_lang, current_lang
from datetime import datetime

router = Router()
db_man = DBManager()
translator = Translator(config.LOCALE)

_attempts_left: dict[int, int] = {}

_CODE_RE = re.compile(r'[A-Z0-9]{6}')


async def _unrestrict_user(bot: Bot, user_id: int) -> list[int]:
    """Lift the captcha restriction in every chat the user is pending in (re-applying a
    standing mute instead where one exists). Returns the chats where it *failed*: those keep
    their pending row — a rejoin (handle_new_user) or the next verification finishes the job —
    and the user is told, rather than being greeted with "verified!" while still muted."""
    failed: list[int] = []
    for chat_id in db_man.get_pending_chats(user_id):
        muted, until = db_man.effective_mute(chat_id, user_id)
        if muted:
            call = lambda: bot.restrict_chat_member(
                chat_id=chat_id, user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False), until_date=until or None)
        else:
            call = lambda: bot.restrict_chat_member(
                chat_id=chat_id, user_id=user_id,
                permissions=moderation_handler.build_chat_permissions(chat_id))
        try:
            await _flood_safe(call)
        except Exception as e:
            # Nothing to lift (bot lost rights / user already left) isn't a failure to report.
            text = str(e).lower()
            if 'not a member' in text or 'user not found' in text or 'chat not found' in text:
                pass
            else:
                failed.append(chat_id)
                continue
        db_man.remove_pending_chat(user_id, chat_id)
    return failed


_MAX_TITLES_SHOWN = 5


def _speak() -> str:
    """Locale the bot is speaking in right now (set per update by the middleware)."""
    return current_lang() or config.LOCALE


async def _chat_list(bot: Bot, chat_ids: list[int]) -> str:
    titles = [f'«{await _chat_title(bot, cid)}»' for cid in chat_ids[:_MAX_TITLES_SHOWN]]
    if len(chat_ids) > _MAX_TITLES_SHOWN:
        titles.append(translator.get_string('and_more').format(len(chat_ids) - _MAX_TITLES_SHOWN))
    return ', '.join(titles)


async def _pending_overview(bot: Bot, user_id: int) -> str:
    """Where the user is still waiting for verification and how long until the chats remove them
    (only chats with the captcha-timeout kick on count). Empty if they're not pending anywhere."""
    rows = db_man.get_pending_chats_since(user_id)
    if not rows:
        return ''
    text = '\n\n' + translator.get_string('captcha_pending_chats').format(await _chat_list(bot, [cid for cid, _ in rows]))
    now = db_man.unix_time()
    left = [since + chat_member_handler._CAPTCHA_KICK_AFTER - now for cid, since in rows
            if since is not None and db_man.is_captcha_enabled(cid) and db_man.is_kick_enabled(cid)]
    if left:
        text += '\n' + translator.get_string('captcha_kick_deadline').format(_human_elapsed(max(0, min(left))))
    return text


async def _build_link_msg(bot: Bot, user_id: int, session_id: str) -> tuple[str, InlineKeyboardMarkup]:
    url = f'{config.CAPTCHA_BASE_URL}/{session_id}'
    text = translator.get_string('captcha_link_msg').format(
        max(1, config.CAPTCHA_TIMEOUT // 60), config.MAX_ATTEMPTS, _human_elapsed(config.COOL_DOWN))
    text += await _pending_overview(bot, user_id)
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=translator.get_string('captcha_button'), url=url)
    ]])
    return text, markup


def _blocklisted_text(user_id: int) -> str:
    """Why a globally banned user can't pass the captcha: when it happened and what to do."""
    ts = db_man.get_blocklist_ts(user_id)
    if ts:
        return translator.get_string('blocked_msg_since').format(datetime.fromtimestamp(ts).strftime('%d.%m.%Y'))
    return translator.get_string('blocked_msg')


def _temp_block_text(user_id: int) -> str:
    left = max(0, db_man.get_blocked_until(user_id) - db_man.unix_time())
    return translator.get_string('temp_block').format(_human_elapsed(left))


@router.message(CommandStart())
async def start_handler(message: types.Message, bot: Bot) -> None:
    user_id = message.from_user.id

    if message.chat.id < 0:
        return

    if db_man.is_blocklisted(user_id):
        await message.answer(_blocklisted_text(user_id))
        return

    if db_man.is_user_blocked(user_id):
        await message.answer(_temp_block_text(user_id))
        return

    if db_man.is_user_allowed(user_id):
        await message.answer(translator.get_string('already_verified'))
        return

    pending = session_manager.get_pending_session(user_id)
    if pending:
        text, markup = await _build_link_msg(bot, user_id, pending)
        await message.answer(text, reply_markup=markup)
        return
    if session_manager.get_completed_session(user_id):
        # The web part is done and a code exists — no point starting a new game; just type it.
        await message.answer(translator.get_string('code_already_issued'))
        return

    captcha_type = random.choice(config.CAPTCHA_TYPES)
    session_id = session_manager.create_session(user_id, captcha_type, _speak())
    _attempts_left[user_id] = config.MAX_ATTEMPTS
    text, markup = await _build_link_msg(bot, user_id, session_id)
    await message.answer(text, reply_markup=markup)


@router.message()
async def handle_code_attempt(message: types.Message, bot: Bot) -> None:
    if message.chat.type != 'private':
        return

    user_id = message.from_user.id

    if db_man.is_blocklisted(user_id):
        await message.answer(_blocklisted_text(user_id))
        return

    if db_man.is_user_blocked(user_id):
        await message.answer(_temp_block_text(user_id))
        return

    if db_man.is_user_allowed(user_id):
        return

    if not session_manager.has_any_session(user_id):
        await message.answer(translator.get_string('no_captcha_requested'))
        return

    code = (message.text or '').strip().upper()
    if not _CODE_RE.fullmatch(code):
        # A sticker, a "hi", a question — not a code attempt. Don't burn an attempt (three of
        # those would temp-block a human and kill their web session); just point at the code.
        await message.answer(translator.get_string('code_hint'))
        return
    session_id = session_manager.find_by_code(user_id, code)

    if session_id is None:
        if user_id not in _attempts_left:
            _attempts_left[user_id] = config.MAX_ATTEMPTS
        _attempts_left[user_id] -= 1
        if _attempts_left[user_id] > 0:
            await message.answer(translator.get_string('incorrect').format(_attempts_left[user_id], config.MAX_ATTEMPTS))
        else:
            db_man.temp_block(user_id)
            session_manager.remove_user_sessions(user_id)
            _attempts_left.pop(user_id, None)
            await message.answer(translator.get_string('temp_block_new').format(
                config.MAX_ATTEMPTS, _human_elapsed(config.COOL_DOWN)))
        return

    waiting = db_man.get_pending_chats(user_id)
    db_man.verify_user(user_id)
    session_manager.remove_session(session_id)
    _attempts_left.pop(user_id, None)
    failed = await _unrestrict_user(bot, user_id)
    await chat_member_handler.delete_welcome_msg(bot, user_id)
    opened = [cid for cid in waiting if cid not in failed]
    if opened:
        text = translator.get_string('verified_chats').format(await _chat_list(bot, opened))
    else:
        text = translator.get_string('verified')
    for chat_id in failed:
        text += '\n' + translator.get_string('verified_partial').format(await _chat_title(bot, chat_id))
    await message.answer(text)


async def session_expiry_task(bot: Bot) -> None:
    while True:
        await asyncio.sleep(30)
        for user_id, lang in session_manager.cleanup_expired():
            _attempts_left.pop(user_id, None)
            try:
                # No update is in flight here, so speak the language the session was started in.
                with use_lang(lang or db_man.get_user_lang(user_id)):
                    text = translator.get_string('captcha_expired').format(max(1, config.CAPTCHA_TIMEOUT // 60))
                await bot.send_message(user_id, text)
            except Exception:
                pass
