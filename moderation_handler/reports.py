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

from datetime import datetime
import ipaddress
from aiogram import types, Bot
from aiogram.filters import Command, CommandObject
import permissions
import config
from .common import (
    router, db_man, translator, _GROUP_TYPES,
    _delete_silently, _ianswer, _esc, _full_user_info, _chat_info, _message_link,
    _report_recipients, _reply_channel, _channel_mention, _resolve_target, _deny,
    _display_name_html, _global_name, _accessible_chats, _chat_title,
)


@router.message(Command('report'))
async def report_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Report a message to the chat's staff (and every owner) in DM. Anyone may use it.

    Used as a reply, it reports (and forwards) that message. Used on its own, it sends a
    general report with no specific message indicated.
    """
    if message.chat.type not in _GROUP_TYPES:
        await _ianswer(message, translator.get_string('mod_group_only'))
        return

    reported = message.reply_to_message
    reporter = message.from_user
    reason = (command.args or '').strip()
    chat_title = message.chat.title or str(message.chat.id)

    lines = [
        translator.get_string('report_header'),
        translator.get_string('report_time').format(datetime.now().strftime('%d.%m.%Y %H:%M:%S')),
        translator.get_string('report_chat').format(chat_title),
        translator.get_string('report_from').format(_full_user_info(reporter)),
    ]
    if reported is not None:
        if reported.sender_chat and reported.sender_chat.type == 'channel':
            lines.append(translator.get_string('report_on').format(_chat_info(reported.sender_chat)))
        elif reported.from_user:
            lines.append(translator.get_string('report_on').format(_full_user_info(reported.from_user)))
        link = _message_link(message.chat, reported.message_id)
        if link:
            lines.append(translator.get_string('report_link').format(link))
    else:
        lines.append(translator.get_string('report_no_message'))
    if reason:
        lines.append(translator.get_string('report_reason').format(reason))
    notice = '\n'.join(lines)

    for uid in _report_recipients(message.chat.id, reporter.id):
        try:
            await bot.send_message(uid, notice)
            if reported is not None:
                await bot.forward_message(uid, message.chat.id, reported.message_id)
        except Exception:
            pass

    await _delete_silently(message)
    await _ianswer(message, translator.get_string('report_sent'))


_PUNISH_LOG_KEYS = {
    'log_ban', 'log_lban', 'log_sban', 'log_slban',
    'log_unban', 'log_ungban', 'log_unsban', 'log_unsgban',
    'log_mute', 'log_gmute', 'log_smute', 'log_gsmute',
    'log_unmute', 'log_unsmute', 'log_ungmute', 'log_ungsmute',
    'log_delete',
}
_PUNL_MAX = 40


def _first_seen_line(target_id: int) -> str:
    """The 'First seen:' line shown at the top of /punl output."""
    when = datetime.fromtimestamp(db_man.get_first_seen(target_id)).strftime('%d.%m.%Y %H:%M:%S')
    return translator.get_string('punl_first_seen').format(when)


def _global_block_line(target_id: int) -> str | None:
    """The 'globally blocked' line shown at the top of /punl and /uinfo, if the target (user
    or channel — ids never collide between the two) is currently on the global blocklist."""
    if db_man.is_blocklisted(target_id) or db_man.is_channel_blocklisted(target_id):
        return translator.get_string('punl_globally_blocked')
    return None


def _captcha_status_line(target_id: int) -> str:
    """The captcha pass/fail line shown in /uinfo — separate from _first_seen_line, which
    tracks first *observation* (any message/join), not whether the captcha was solved."""
    status = db_man.get_captcha_status(target_id)
    if status is None:
        return translator.get_string('uinfo_captcha_unknown')
    verified, blocked_until = status
    if verified:
        return translator.get_string('uinfo_captcha_passed')
    if blocked_until and blocked_until > db_man.unix_time():
        when = datetime.fromtimestamp(blocked_until).strftime('%d.%m.%Y %H:%M:%S')
        return translator.get_string('uinfo_captcha_blocked').format(when)
    return translator.get_string('uinfo_captcha_not_passed')


def _ip_link(ip: str) -> str:
    """Render an IP as a clickable https://ipinfo.io/<ip> link. Falls back to plain escaped
    text if it isn't actually a well-formed IP (e.g. an old 'unknown' placeholder)."""
    escaped = _esc(ip)
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return escaped
    return f'<a href="https://ipinfo.io/{escaped}">{escaped}</a>'


async def _captcha_visit_lines(bot: Bot, target_id: int) -> list[str]:
    """The 'IP:'/'User-Agent:'/same-IP lines shown in /uinfo below 'First seen:', if the
    opt-in IP collection is on and a first-visit record exists. Each field is shown
    independently and only when present, so e.g. an empty User-Agent header doesn't produce
    a blank line. Both fields are HTML-escaped like any other user-supplied text shown in a
    message (the User-Agent is also sanitized/capped before it's ever stored)."""
    if not config.COLLECT_CAPTCHA_IPS:
        return []
    record = db_man.get_captcha_ip(target_id)
    if not record:
        return []
    ip, user_agent, _ts = record
    lines = []
    if ip:
        lines.append(translator.get_string('punl_captcha_ip').format(_ip_link(ip)))
    if user_agent:
        lines.append(translator.get_string('punl_captcha_ua').format(_esc(user_agent)))
    if ip:
        others = [uid for uid in db_man.get_users_by_ip(ip) if uid != target_id]
        if others:
            lines.append(translator.get_string('uinfo_same_ip_header').format(len(others)))
            for uid in others:
                lines.append(_esc(await _global_name(bot, uid)))
    return lines


async def _render_punishments(bot: Bot, rows: list, target_id: int, target_label: str,
                              chat_ids: set[int], show_chat: bool, *, include_first_seen: bool = True) -> str:
    """Render a user's/channel's punishment history from get_punishments() rows, scoped to
    chat_ids. Never includes IP/User-Agent — that's owner-only, DM-only, shown by /uinfo
    instead. include_first_seen is False for channels: they're never in seen_users (only real
    users are tracked there), so 'first seen' would just be a meaningless backfilled 'now'."""
    header_lines = [_first_seen_line(target_id)] if include_first_seen else []
    block_line = _global_block_line(target_id)
    if block_line:
        header_lines.append(block_line)
    rows = [r for r in rows if r[2] in _PUNISH_LOG_KEYS and r[0] in chat_ids]
    if not rows:
        empty = translator.get_string('punl_empty').format(target_label)
        return ('\n'.join(header_lines) + '\n' + empty) if header_lines else empty
    lines = header_lines + [translator.get_string('punl_header').format(target_label, len(rows))]
    titles: dict[int, str] = {}
    for chat_id, ts, _action_key, text in rows[:_PUNL_MAX]:
        when = datetime.fromtimestamp(ts).strftime('%d.%m.%Y %H:%M:%S')
        text = _esc(text)
        if show_chat:
            if chat_id not in titles:
                titles[chat_id] = _esc(await _chat_title(bot, chat_id))
            lines.append(f'{when} | [{titles[chat_id]}] {text}')
        else:
            lines.append(f'{when} | {text}')
    if len(rows) > _PUNL_MAX:
        lines.append(translator.get_string('punl_more').format(len(rows) - _PUNL_MAX))
    out = '\n'.join(lines)
    return out if len(out) <= 4000 else out[:3999] + '…'


@router.message(Command('punl'))
async def punishments_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Show a user's (or, by replying to a channel's post, a channel's) punishment history.
    In a group: staff only, scoped to that chat. In DM: owners/admins, aggregated across the
    chats they manage."""
    await _delete_silently(message)
    is_group = message.chat.type in _GROUP_TYPES
    user_id = message.from_user.id

    if is_group:
        if not permissions.is_staff(db_man, message.chat.id, user_id):
            await _deny(message)
            return
        chat_ids, show_chat = {message.chat.id}, False
    else:
        if not (permissions.is_owner(user_id) or db_man.get_admin_chats(user_id)):
            await message.answer(translator.get_string('mod_no_permission'))
            return
        chat_ids, show_chat = set(_accessible_chats(user_id)), True

    channel = _reply_channel(message)
    if channel is not None:
        target_id = channel.id
        target_label = _channel_mention(channel)
        include_first_seen = False
    else:
        target_id = await _resolve_target(message, command, bot)
        if target_id is None:
            provided = bool(message.reply_to_message) or bool((command.args or '').strip())
            key = 'mod_user_not_found' if provided else 'mod_specify_user'
            await (_ianswer(message, translator.get_string(key)) if is_group
                   else message.answer(translator.get_string(key)))
            return
        target_label = await (_display_name_html(bot, message.chat.id, target_id) if is_group
                              else _global_name(bot, target_id))
        if not is_group:
            target_label = _esc(target_label)
        include_first_seen = True

    if not db_man.user_has_any_record(target_id):
        await (_ianswer(message, translator.get_string('punl_unknown_user')) if is_group
               else message.answer(translator.get_string('punl_unknown_user')))
        return

    rows = db_man.get_punishments(target_id)
    text = await _render_punishments(bot, rows, target_id, target_label, chat_ids, show_chat,
                                     include_first_seen=include_first_seen)
    await message.answer(text, parse_mode='HTML', link_preview_options=types.LinkPreviewOptions(is_disabled=True))


@router.message(Command('uinfo'))
async def uinfo_cmd(message: types.Message, command: CommandObject, bot: Bot) -> None:
    """Owner-only, DM-only: display name/username/id (same resolution as /punl in DM), whether
    the captcha was passed, first-seen date, and the captured IP/User-Agent
    (config.COLLECT_CAPTCHA_IPS) — no punishment history here, that's what /punl is for.
    Silently ignored outside a private chat (doesn't even hint the command exists)."""
    if message.chat.type != 'private':
        return
    user_id = message.from_user.id
    if not permissions.is_owner(user_id):
        await message.answer(translator.get_string('mod_no_permission'))
        return

    target_id = await _resolve_target(message, command, bot)
    if target_id is None:
        provided = bool(message.reply_to_message) or bool((command.args or '').strip())
        key = 'mod_user_not_found' if provided else 'mod_specify_user'
        await message.answer(translator.get_string(key))
        return

    if not db_man.user_has_any_record(target_id):
        await message.answer(translator.get_string('punl_unknown_user'))
        return

    target_label = _esc(await _global_name(bot, target_id))
    lines = [translator.get_string('uinfo_header').format(target_label)]
    block_line = _global_block_line(target_id)
    if block_line:
        lines.append(block_line)
    lines.append(_captcha_status_line(target_id))
    lines.append(_first_seen_line(target_id))
    lines += await _captcha_visit_lines(bot, target_id)
    await message.answer('\n'.join(lines), parse_mode='HTML',
                         link_preview_options=types.LinkPreviewOptions(is_disabled=True))
