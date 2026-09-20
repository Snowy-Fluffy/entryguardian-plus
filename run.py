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

from aiogram import Bot, Dispatcher
import asyncio
import logging
import personal_msg_handler
import chat_member_handler
import reaction_handler
import moderation_handler
import webserver
import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
# aiogram logs every single update at INFO ("Update id=… is handled/not handled …") — pure noise
# at our volume; keep its warnings/errors, drop the per-update chatter.
logging.getLogger('aiogram.event').setLevel(logging.WARNING)
log = logging.getLogger('entryguardian')

bot = Bot(token=config.TOKEN)
dp = Dispatcher()


async def _supervised(name: str, coro_fn, *args) -> None:
    """Run a background loop forever, restarting it after a crash instead of letting the
    exception propagate out of asyncio.gather and take the whole bot down with it."""
    while True:
        try:
            await coro_fn(*args)
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('background task %s crashed; restarting in 5s', name)
            await asyncio.sleep(5)


async def main():
    bot_info = await bot.get_me()
    chat_member_handler.bot_username = bot_info.username

    dp.message.outer_middleware(moderation_handler.UserTrackingMiddleware())
    dp.edited_message.outer_middleware(moderation_handler.UserTrackingMiddleware())

    dp.include_router(moderation_handler.router)
    dp.include_router(personal_msg_handler.router)
    dp.include_router(chat_member_handler.router)
    dp.include_router(reaction_handler.router)

    await asyncio.gather(
        dp.start_polling(bot, allowed_updates=['message', 'edited_message', 'chat_member', 'my_chat_member', 'message_reaction', 'callback_query', 'chat_join_request']),
        webserver.start_server(),
        _supervised('rate_limit_cleanup', webserver.rate_limit_cleanup_task),
        _supervised('session_expiry', personal_msg_handler.session_expiry_task, bot),
        _supervised('raid_reminder', chat_member_handler.raid_reminder_task, bot),
        _supervised('captcha_timeout', chat_member_handler.captcha_timeout_task, bot),
        _supervised('pending_unban_retry', chat_member_handler.pending_unban_retry_task, bot),
        _supervised('flush_messages', moderation_handler.flush_messages_task),
        _supervised('purge_old_messages', moderation_handler.purge_old_messages_task),
        _supervised('scheduled_delete', moderation_handler.scheduled_delete_task, bot),
    )


if __name__ == '__main__':
    asyncio.run(main())
