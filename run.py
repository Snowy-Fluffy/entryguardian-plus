



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
        chat_member_handler.captcha_timeout_task(bot),
        chat_member_handler.pending_unban_retry_task(bot),
    )


if __name__ == '__main__':
    asyncio.run(main())
