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

import sqlite3
import config
from datetime import datetime

from .users import UsersMixin
from .roles import RolesMixin
from .bans import BansMixin
from .mutes import MutesMixin
from .chat_settings import ChatSettingsMixin
from .antispam import AntispamMixin
from .logs import LogsMixin
from .message_log import MessageLogMixin


class DBManager(UsersMixin, RolesMixin, BansMixin, MutesMixin, ChatSettingsMixin,
                 AntispamMixin, LogsMixin, MessageLogMixin):
	def __init__(self):
		self.connection = sqlite3.connect(config.DB_PATH, check_same_thread=False)
		self.cursor = self.connection.cursor()
		self._msg_buffer: list[tuple[int, int, int, int]] = []
		self._channel_msg_buffer: list[tuple[int, int, int, int]] = []
		tables = {row[0] for row in self.cursor.execute('SELECT name FROM sqlite_master WHERE type="table"').fetchall()}
		if 'user' not in tables:
			self.cursor.execute('CREATE TABLE user(id, verified, blocked_until)')
		if 'pending_chats' not in tables:
			self.cursor.execute('CREATE TABLE pending_chats(user_id INTEGER, chat_id INTEGER, since INTEGER)')
		if 'roles' not in tables:
			self.cursor.execute('CREATE TABLE roles(chat_id INTEGER, user_id INTEGER, role TEXT, UNIQUE(chat_id, user_id))')
		if 'seen_users' not in tables:
			self.cursor.execute('CREATE TABLE seen_users(user_id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, first_seen INTEGER)')
		if 'blocklist' not in tables:
			self.cursor.execute('CREATE TABLE blocklist(user_id INTEGER PRIMARY KEY)')
		if 'bot_chats' not in tables:
			self.cursor.execute('CREATE TABLE bot_chats(chat_id INTEGER PRIMARY KEY)')
		if 'ban_exceptions' not in tables:
			self.cursor.execute('CREATE TABLE ban_exceptions(chat_id INTEGER, user_id INTEGER, UNIQUE(chat_id, user_id))')
		if 'local_bans' not in tables:
			self.cursor.execute('CREATE TABLE local_bans(chat_id INTEGER, user_id INTEGER, UNIQUE(chat_id, user_id))')
		if 'action_log' not in tables:
			self.cursor.execute('CREATE TABLE action_log(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, ts INTEGER, text TEXT, target_id INTEGER, action_key TEXT)')
		if 'captcha_disabled' not in tables:
			self.cursor.execute('CREATE TABLE captcha_disabled(chat_id INTEGER PRIMARY KEY)')
		if 'kick_disabled' not in tables:
			self.cursor.execute('CREATE TABLE kick_disabled(chat_id INTEGER PRIMARY KEY)')
		if 'pending_unbans' not in tables:
			self.cursor.execute('CREATE TABLE pending_unbans(chat_id INTEGER, user_id INTEGER, next_ts INTEGER, UNIQUE(chat_id, user_id))')
		if 'raid_mode' not in tables:
			self.cursor.execute('CREATE TABLE raid_mode(chat_id INTEGER PRIMARY KEY)')
		if 'chat_rules' not in tables:
			self.cursor.execute('CREATE TABLE chat_rules(chat_id INTEGER PRIMARY KEY, text TEXT)')
		if 'mutes' not in tables:
			self.cursor.execute('CREATE TABLE mutes(chat_id INTEGER, user_id INTEGER, until INTEGER, UNIQUE(chat_id, user_id))')
		if 'global_mutes' not in tables:
			self.cursor.execute('CREATE TABLE global_mutes(user_id INTEGER PRIMARY KEY, until INTEGER)')
		if 'stopped_chats' not in tables:
			self.cursor.execute('CREATE TABLE stopped_chats(chat_id INTEGER PRIMARY KEY)')
		if 'cooldowns' not in tables:
			self.cursor.execute('CREATE TABLE cooldowns(chat_id INTEGER, command TEXT, seconds INTEGER, UNIQUE(chat_id, command))')
		if 'cooldown_use' not in tables:
			self.cursor.execute('CREATE TABLE cooldown_use(chat_id INTEGER, user_id INTEGER, command TEXT, last_ts INTEGER, UNIQUE(chat_id, user_id, command))')
		if 'command_banned' not in tables:
			self.cursor.execute('CREATE TABLE command_banned(user_id INTEGER PRIMARY KEY)')
		if 'channel_blocklist' not in tables:
			self.cursor.execute('CREATE TABLE channel_blocklist(channel_id INTEGER PRIMARY KEY)')
		if 'channel_ban_exceptions' not in tables:
			self.cursor.execute('CREATE TABLE channel_ban_exceptions(chat_id INTEGER, channel_id INTEGER, UNIQUE(chat_id, channel_id))')
		if 'channels_banned' not in tables:
			self.cursor.execute('CREATE TABLE channels_banned(chat_id INTEGER PRIMARY KEY)')
		if 'chat_perms' not in tables:
			self.cursor.execute('CREATE TABLE chat_perms(chat_id INTEGER PRIMARY KEY, perms TEXT)')
		if 'antispam_settings' not in tables:
			self.cursor.execute(
				'CREATE TABLE antispam_settings(chat_id INTEGER PRIMARY KEY, enabled INTEGER, '
				'count INTEGER, window INTEGER, mute_seconds INTEGER, notify INTEGER, '
				'unicode_guard INTEGER DEFAULT 1)'
			)
		antispam_cols = {row[1] for row in self.cursor.execute('PRAGMA table_info(antispam_settings)').fetchall()}
		if 'unicode_guard' not in antispam_cols:
			self.cursor.execute('ALTER TABLE antispam_settings ADD COLUMN unicode_guard INTEGER DEFAULT 1')
		if 'antispam_streak' not in tables:
			self.cursor.execute(
				'CREATE TABLE antispam_streak(chat_id INTEGER, user_id INTEGER, signature TEXT, '
				'count INTEGER, first_ts INTEGER, message_ids TEXT, UNIQUE(chat_id, user_id))'
			)
		if 'recent_messages' not in tables:
			self.cursor.execute('CREATE TABLE recent_messages(chat_id INTEGER, user_id INTEGER, message_id INTEGER, ts INTEGER)')
			self.cursor.execute('CREATE INDEX idx_recent_messages ON recent_messages(chat_id, user_id, ts)')
		if 'recent_channel_messages' not in tables:
			self.cursor.execute('CREATE TABLE recent_channel_messages(chat_id INTEGER, channel_id INTEGER, message_id INTEGER, ts INTEGER)')
			self.cursor.execute('CREATE INDEX idx_recent_channel_messages ON recent_channel_messages(chat_id, channel_id, ts)')
		if 'join_request_chats' not in tables:
			self.cursor.execute('CREATE TABLE join_request_chats(chat_id INTEGER PRIMARY KEY)')
		if 'auto_accept' not in tables:
			self.cursor.execute('CREATE TABLE auto_accept(chat_id INTEGER PRIMARY KEY)')
		if 'delete_system_messages' not in tables:
			self.cursor.execute('CREATE TABLE delete_system_messages(chat_id INTEGER PRIMARY KEY)')
		if 'captcha_ips' not in tables:
			self.cursor.execute('CREATE TABLE captcha_ips(user_id INTEGER PRIMARY KEY, ip TEXT, user_agent TEXT, ts INTEGER)')
		captcha_ip_cols = {row[1] for row in self.cursor.execute('PRAGMA table_info(captcha_ips)').fetchall()}
		if 'user_agent' not in captcha_ip_cols:
			self.cursor.execute('ALTER TABLE captcha_ips ADD COLUMN user_agent TEXT')
		log_cols = {row[1] for row in self.cursor.execute('PRAGMA table_info(action_log)').fetchall()}
		if 'target_id' not in log_cols:
			self.cursor.execute('ALTER TABLE action_log ADD COLUMN target_id INTEGER')
		if 'action_key' not in log_cols:
			self.cursor.execute('ALTER TABLE action_log ADD COLUMN action_key TEXT')
		seen_cols = {row[1] for row in self.cursor.execute('PRAGMA table_info(seen_users)').fetchall()}
		if 'display_name' not in seen_cols:
			self.cursor.execute('ALTER TABLE seen_users ADD COLUMN display_name TEXT')
		if 'first_seen' not in seen_cols:
			self.cursor.execute('ALTER TABLE seen_users ADD COLUMN first_seen INTEGER')
			self.cursor.execute('UPDATE seen_users SET first_seen=? WHERE first_seen IS NULL', (self.unix_time(),))
		pending_cols = {row[1] for row in self.cursor.execute('PRAGMA table_info(pending_chats)').fetchall()}
		if 'since' not in pending_cols:
			self.cursor.execute('ALTER TABLE pending_chats ADD COLUMN since INTEGER')
			self.cursor.execute('UPDATE pending_chats SET since=? WHERE since IS NULL', (self.unix_time(),))
		self.cursor.execute('DROP TABLE IF EXISTS welcome_log')
		self.connection.commit()

	def unix_time(self):
		return int(datetime.now().timestamp())
