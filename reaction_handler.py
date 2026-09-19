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

from aiogram import Router, Bot
from aiogram.types import MessageReactionUpdated
from dbmanager import DBManager
from datetime import datetime
import config
import permissions
# Imported straight from the package's shared module (not re-exported via moderation_handler/__init__):
# these are the same helpers the message middleware uses for the equivalent checks, so the two
# enforcement paths can't drift apart.
from moderation_handler.common import _isend, _clear_captcha_state, translator

router = Router()
db_man = DBManager()


@router.message_reaction()
async def on_reaction(event: MessageReactionUpdated, bot: Bot):
    """A reaction is the one thing a restricted member can still do, so it gets the same
    enforcement a message does (see UserTrackingMiddleware) — minus deletion, since the Bot API
    can't remove someone else's reaction; the ban is all we can do.

    Who reacted comes in two shapes: `event.user` for a person, or `event.actor_chat` when the
    reaction was left on behalf of a chat. An actor_chat of type 'channel' is a channel (via
    "Send As"/anonymous channel reaction) and is treated like a channel post: banned if it's on
    the global channel blocklist, or if this chat forbids channels. Any other actor_chat is the
    group itself — an anonymous admin — and is left alone.
    """
    chat_id = event.chat.id
    db_man.remember_chat(chat_id)

    actor = event.actor_chat
    if actor is not None and actor.type == 'channel':
        if db_man.is_channel_blocklisted(actor.id) and not db_man.is_channel_ban_exception(chat_id, actor.id):
            try:
                await bot.ban_chat_sender_chat(chat_id, actor.id)
            except Exception:
                pass
        elif db_man.is_channels_banned(chat_id):
            try:
                await bot.ban_chat_sender_chat(chat_id, actor.id)
            except Exception:
                pass
            try:
                await _isend(bot, chat_id, translator.get_string('channels_forbidden'))
            except Exception:
                pass
        return

    user = event.user
    if not user:
        return

    db_man.remember_user(user.id, user.username, user.full_name)
    user_id = user.id

    if permissions.is_owner(user_id):
        return

    if db_man.is_blocklisted(user_id) and not db_man.is_ban_exception(chat_id, user_id):
        try:
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            _clear_captcha_state(chat_id, user_id)
        except Exception:
            pass
        return

    if chat_id not in db_man.get_pending_chats(user_id):
        return

    banned_until = int(datetime.now().timestamp()) + config.COOL_DOWN
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id, until_date=banned_until)
    except Exception:
        pass
