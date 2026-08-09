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

"""The moderation system: roles, bans/mutes (local and global), anti-raid, bulk message
deletion, the inline-keyboard admin panel, and the outer UserTrackingMiddleware that enforces
most of it reactively. Split across submodules by command family; common.py holds every helper
shared by more than one of them. Importing this package registers every command/callback handler
on `router` as a side effect — see each submodule for its own command grid."""

from .common import router, build_chat_permissions
from .middleware import UserTrackingMiddleware, flush_messages_task, purge_old_messages_task
from . import roles
from . import reports
from . import bans
from . import mutes
from . import deletion
from . import chat_admin
from . import admin_panel

__all__ = [
    'router',
    'UserTrackingMiddleware',
    'flush_messages_task',
    'purge_old_messages_task',
    'build_chat_permissions',
]
