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

from aiogram import Bot, Dispatcher
import asyncio
import personal_msg_handler
import chat_member_handler
import reaction_handler
import moderation_handler
import webserver
import config

bot = Bot(token=config.TOKEN)
dp = Dispatcher()


async def main():
    bot_info = await bot.get_me()
    chat_member_handler.bot_username = bot_info.username

    dp.message.outer_middleware(moderation_handler.UserTrackingMiddleware())

    dp.include_router(moderation_handler.router)
    dp.include_router(personal_msg_handler.router)
    dp.include_router(chat_member_handler.router)
    dp.include_router(reaction_handler.router)

    await asyncio.gather(
        dp.start_polling(bot, allowed_updates=['message', 'chat_member', 'my_chat_member', 'message_reaction', 'callback_query']),
        webserver.start_server(),
        personal_msg_handler.session_expiry_task(bot),
        chat_member_handler.raid_reminder_task(bot),
    )


if __name__ == '__main__':
    asyncio.run(main())
