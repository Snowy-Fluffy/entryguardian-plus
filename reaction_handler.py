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

from aiogram import Router, Bot
from aiogram.types import MessageReactionUpdated
from dbmanager import DBManager
from datetime import datetime
import config

router = Router()
db_man = DBManager()



@router.message_reaction()
async def on_reaction(event: MessageReactionUpdated, bot: Bot):
    if not event.user:
        return

    user_id = event.user.id
    chat_id = event.chat.id

    if db_man.is_blocklisted(user_id):
        return

    if chat_id not in db_man.get_pending_chats(user_id):
        return

    banned_until = int(datetime.now().timestamp()) + config.COOL_DOWN
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id, until_date=banned_until)
    except Exception:
        pass

