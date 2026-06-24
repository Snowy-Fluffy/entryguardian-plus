



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

