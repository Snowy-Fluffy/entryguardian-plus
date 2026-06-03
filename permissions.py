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

import config


def is_owner(user_id: int) -> bool:
    """Owners are defined only in .env and have access everywhere."""
    return user_id in config.OWNERS


def effective_role(db_man, chat_id: int, user_id: int) -> str | None:
    """Return the effective role of a user in a chat: 'owner', 'admin', 'moderator' or None."""
    if is_owner(user_id):
        return 'owner'
    return db_man.get_role(chat_id, user_id)


def can_manage_roles(db_man, chat_id: int, user_id: int) -> bool:
    """Owners (anywhere) and admins (of this chat) may add/remove admins and moderators."""
    return is_owner(user_id) or db_man.is_admin(chat_id, user_id)


def is_staff(db_man, chat_id: int, user_id: int) -> bool:
    """Owners (anywhere), admins and moderators (of this chat) count as staff."""
    return is_owner(user_id) or db_man.get_role(chat_id, user_id) in ('admin', 'moderator')
