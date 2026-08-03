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

class DBManager:
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
		if 'delete_join_messages' not in tables:
			self.cursor.execute('CREATE TABLE delete_join_messages(chat_id INTEGER PRIMARY KEY)')
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

	def is_user_known(self, user_id):
		result = self.cursor.execute('SELECT id FROM user WHERE id=?', (user_id,)).fetchone()
		return bool(result)

	def is_user_blocked(self, user_id):
		if not self.is_user_known(user_id):
			return False
		result = self.cursor.execute('SELECT blocked_until FROM user WHERE id=?', (user_id,)).fetchone()
		return result[0] > self.unix_time()

	def is_user_allowed(self, user_id):
		if self.is_user_known(user_id) and not self.is_user_blocked(user_id):
			result = self.cursor.execute('SELECT verified FROM user WHERE id=?', (user_id,)).fetchone()
			return bool(result[0])
		return False

	def verify_user(self, user_id):
		if not self.is_user_known(user_id):
			self.cursor.execute('INSERT INTO user VALUES (?, 1, -1)', (user_id,))
		else:
			self.cursor.execute('UPDATE user SET verified=1 WHERE id=?', (user_id,))
		self.connection.commit()

	def temp_block(self, user_id):
		blocked_until = self.unix_time() + config.COOL_DOWN
		if not self.is_user_known(user_id):
			self.cursor.execute('INSERT INTO user VALUES (?, 0, ?)', (user_id, blocked_until))
		else:
			self.cursor.execute('UPDATE user SET blocked_until=? WHERE id=?', (blocked_until, user_id))
		self.connection.commit()

	def add_pending_chat(self, user_id, chat_id):
		existing = self.cursor.execute(
			'SELECT 1 FROM pending_chats WHERE user_id=? AND chat_id=?',
			(user_id, chat_id)
		).fetchone()
		if not existing:
			self.cursor.execute(
				'INSERT INTO pending_chats(user_id, chat_id, since) VALUES (?, ?, ?)',
				(user_id, chat_id, self.unix_time())
			)
			self.connection.commit()

	def get_pending_chats(self, user_id):
		rows = self.cursor.execute(
			'SELECT chat_id FROM pending_chats WHERE user_id=?',
			(user_id,)
		).fetchall()
		return [row[0] for row in rows]

	def remove_pending_chat(self, user_id, chat_id):
		self.cursor.execute(
			'DELETE FROM pending_chats WHERE user_id=? AND chat_id=?',
			(user_id, chat_id)
		)
		self.connection.commit()

	def clear_pending_chats(self, user_id):
		self.cursor.execute('DELETE FROM pending_chats WHERE user_id=?', (user_id,))
		self.connection.commit()

	def set_pending_since(self, chat_id, user_id):
		now = self.unix_time()
		updated = self.cursor.execute(
			'UPDATE pending_chats SET since=? WHERE user_id=? AND chat_id=?',
			(now, user_id, chat_id)
		).rowcount
		if not updated:
			self.cursor.execute(
				'INSERT INTO pending_chats(user_id, chat_id, since) VALUES (?, ?, ?)',
				(user_id, chat_id, now)
			)
		self.connection.commit()

	def welcome_within(self, chat_id, user_id, period):
		row = self.cursor.execute(
			'SELECT since FROM pending_chats WHERE user_id=? AND chat_id=?',
			(user_id, chat_id)
		).fetchone()
		return bool(row) and row[0] is not None and row[0] > self.unix_time() - period

	def get_expired_pending(self, max_age):
		cutoff = self.unix_time() - max_age
		return self.cursor.execute(
			'SELECT user_id, chat_id FROM pending_chats WHERE since IS NOT NULL AND since < ?',
			(cutoff,)
		).fetchall()

	def set_role(self, chat_id, user_id, role):
		self.cursor.execute(
			'INSERT INTO roles(chat_id, user_id, role) VALUES (?, ?, ?) '
			'ON CONFLICT(chat_id, user_id) DO UPDATE SET role=excluded.role',
			(chat_id, user_id, role)
		)
		self.connection.commit()

	def remove_role(self, chat_id, user_id):
		self.cursor.execute(
			'DELETE FROM roles WHERE chat_id=? AND user_id=?',
			(chat_id, user_id)
		)
		self.connection.commit()

	def get_role(self, chat_id, user_id):
		result = self.cursor.execute(
			'SELECT role FROM roles WHERE chat_id=? AND user_id=?',
			(chat_id, user_id)
		).fetchone()
		return result[0] if result else None

	def is_admin(self, chat_id, user_id):
		return self.get_role(chat_id, user_id) == 'admin'

	def is_moderator(self, chat_id, user_id):
		return self.get_role(chat_id, user_id) == 'moderator'

	def get_admin_chats(self, user_id):
		return [row[0] for row in self.cursor.execute(
			"SELECT chat_id FROM roles WHERE user_id=? AND role='admin'",
			(user_id,)
		).fetchall()]

	def get_moderator_chats(self, user_id):
		return [row[0] for row in self.cursor.execute(
			"SELECT chat_id FROM roles WHERE user_id=? AND role='moderator'",
			(user_id,)
		).fetchall()]

	def list_roles(self, chat_id):
		return self.cursor.execute(
			'SELECT user_id, role FROM roles WHERE chat_id=?',
			(chat_id,)
		).fetchall()

	def remember_user(self, user_id, username=None, display_name=None):
		username = username.lstrip('@').lower() if username else None
		if not username and not display_name:
			return
		if username:
			self.cursor.execute(
				'UPDATE seen_users SET username=NULL WHERE username=? AND user_id<>?',
				(username, user_id)
			)
		self.cursor.execute(
			'INSERT INTO seen_users(user_id, username, display_name, first_seen) VALUES (?, ?, ?, ?) '
			'ON CONFLICT(user_id) DO UPDATE SET '
			'username=COALESCE(excluded.username, seen_users.username), '
			'display_name=COALESCE(excluded.display_name, seen_users.display_name)',
			(user_id, username, display_name, self.unix_time())
		)
		self.connection.commit()

	def get_first_seen(self, user_id):
		"""Look up first_seen for a user, backfilling it with the current time if missing."""
		row = self.cursor.execute('SELECT first_seen FROM seen_users WHERE user_id=?', (user_id,)).fetchone()
		if row is not None and row[0] is not None:
			return row[0]
		now = self.unix_time()
		if row is None:
			self.cursor.execute('INSERT INTO seen_users(user_id, first_seen) VALUES (?, ?)', (user_id, now))
		else:
			self.cursor.execute('UPDATE seen_users SET first_seen=? WHERE user_id=?', (now, user_id))
		self.connection.commit()
		return now

	_USER_ID_LOOKUPS = (
		('user', 'id'),
		('seen_users', 'user_id'),
		('pending_chats', 'user_id'),
		('roles', 'user_id'),
		('blocklist', 'user_id'),
		('ban_exceptions', 'user_id'),
		('action_log', 'target_id'),
		('pending_unbans', 'user_id'),
		('mutes', 'user_id'),
		('global_mutes', 'user_id'),
		('cooldown_use', 'user_id'),
		('command_banned', 'user_id'),
		('captcha_ips', 'user_id'),
	)

	def user_has_any_record(self, user_id):
		"""Whether this user_id shows up in any table at all, not just seen_users."""
		for table, col in self._USER_ID_LOOKUPS:
			if self.cursor.execute(f'SELECT 1 FROM {table} WHERE {col}=? LIMIT 1', (user_id,)).fetchone():
				return True
		return False

	def find_user_by_username(self, username):
		username = username.lstrip('@').lower()
		row = self.cursor.execute(
			'SELECT user_id FROM seen_users WHERE username=?',
			(username,)
		).fetchone()
		return row[0] if row else None

	def find_user_by_id(self, user_id):
		row = self.cursor.execute(
			'SELECT username, display_name FROM seen_users WHERE user_id=?',
			(user_id,)
		).fetchone()
		return (row[0], row[1]) if row else None

	def add_to_blocklist(self, user_id):
		self.cursor.execute('INSERT OR IGNORE INTO blocklist(user_id) VALUES (?)', (user_id,))
		self.connection.commit()

	def remove_from_blocklist(self, user_id):
		self.cursor.execute('DELETE FROM blocklist WHERE user_id=?', (user_id,))
		self.connection.commit()

	def is_blocklisted(self, user_id):
		return bool(self.cursor.execute('SELECT 1 FROM blocklist WHERE user_id=?', (user_id,)).fetchone())

	def get_blocklist(self):
		return [row[0] for row in self.cursor.execute('SELECT user_id FROM blocklist').fetchall()]

	def remember_chat(self, chat_id):
		"""Record a chat the bot is in. Returns True if this chat was not known before."""
		self.cursor.execute('INSERT OR IGNORE INTO bot_chats(chat_id) VALUES (?)', (chat_id,))
		newly_added = self.cursor.rowcount > 0
		self.connection.commit()
		return newly_added

	def forget_chat(self, chat_id):
		self.cursor.execute('DELETE FROM bot_chats WHERE chat_id=?', (chat_id,))
		self.connection.commit()

	def get_bot_chats(self):
		return [row[0] for row in self.cursor.execute('SELECT chat_id FROM bot_chats').fetchall()]

	def add_ban_exception(self, chat_id, user_id):
		self.cursor.execute('INSERT OR IGNORE INTO ban_exceptions(chat_id, user_id) VALUES (?, ?)', (chat_id, user_id))
		self.connection.commit()

	def remove_ban_exception(self, chat_id, user_id):
		self.cursor.execute('DELETE FROM ban_exceptions WHERE chat_id=? AND user_id=?', (chat_id, user_id))
		self.connection.commit()

	def is_ban_exception(self, chat_id, user_id):
		return bool(self.cursor.execute('SELECT 1 FROM ban_exceptions WHERE chat_id=? AND user_id=?', (chat_id, user_id)).fetchone())

	def clear_ban_exceptions(self, user_id):
		self.cursor.execute('DELETE FROM ban_exceptions WHERE user_id=?', (user_id,))
		self.connection.commit()

	def add_channel_to_blocklist(self, channel_id):
		self.cursor.execute('INSERT OR IGNORE INTO channel_blocklist(channel_id) VALUES (?)', (channel_id,))
		self.connection.commit()

	def remove_channel_from_blocklist(self, channel_id):
		self.cursor.execute('DELETE FROM channel_blocklist WHERE channel_id=?', (channel_id,))
		self.connection.commit()

	def is_channel_blocklisted(self, channel_id):
		return bool(self.cursor.execute('SELECT 1 FROM channel_blocklist WHERE channel_id=?', (channel_id,)).fetchone())

	def get_channel_blocklist(self):
		return [row[0] for row in self.cursor.execute('SELECT channel_id FROM channel_blocklist').fetchall()]

	def add_channel_ban_exception(self, chat_id, channel_id):
		self.cursor.execute('INSERT OR IGNORE INTO channel_ban_exceptions(chat_id, channel_id) VALUES (?, ?)', (chat_id, channel_id))
		self.connection.commit()

	def is_channel_ban_exception(self, chat_id, channel_id):
		return bool(self.cursor.execute('SELECT 1 FROM channel_ban_exceptions WHERE chat_id=? AND channel_id=?', (chat_id, channel_id)).fetchone())

	def clear_channel_ban_exceptions(self, channel_id):
		self.cursor.execute('DELETE FROM channel_ban_exceptions WHERE channel_id=?', (channel_id,))
		self.connection.commit()

	def set_channels_banned(self, chat_id, enabled):
		if enabled:
			self.cursor.execute('INSERT OR IGNORE INTO channels_banned(chat_id) VALUES (?)', (chat_id,))
		else:
			self.cursor.execute('DELETE FROM channels_banned WHERE chat_id=?', (chat_id,))
		self.connection.commit()

	def is_channels_banned(self, chat_id):
		return bool(self.cursor.execute('SELECT 1 FROM channels_banned WHERE chat_id=?', (chat_id,)).fetchone())

	def get_chat_perms(self, chat_id):
		row = self.cursor.execute('SELECT perms FROM chat_perms WHERE chat_id=?', (chat_id,)).fetchone()
		if row is None:
			return None
		return [p for p in row[0].split(',') if p]

	def set_chat_perms(self, chat_id, perms):
		self.cursor.execute('INSERT OR REPLACE INTO chat_perms(chat_id, perms) VALUES (?, ?)',
			(chat_id, ','.join(perms)))
		self.connection.commit()

	def add_log(self, chat_id, text, target_id=None, action_key=None):
		self.cursor.execute(
			'INSERT INTO action_log(chat_id, ts, text, target_id, action_key) VALUES (?, ?, ?, ?, ?)',
			(chat_id, self.unix_time(), text, target_id, action_key)
		)
		self.connection.commit()

	def get_logs(self, chat_id):
		return self.cursor.execute(
			'SELECT ts, text FROM action_log WHERE chat_id=? ORDER BY id DESC',
			(chat_id,)
		).fetchall()

	def get_punishments(self, target_id):
		return self.cursor.execute(
			'SELECT chat_id, ts, action_key, text FROM action_log WHERE target_id=? ORDER BY id DESC',
			(target_id,)
		).fetchall()

	def set_captcha_enabled(self, chat_id, enabled):
		if enabled:
			self.cursor.execute('DELETE FROM captcha_disabled WHERE chat_id=?', (chat_id,))
		else:
			self.cursor.execute('INSERT OR IGNORE INTO captcha_disabled(chat_id) VALUES (?)', (chat_id,))
		self.connection.commit()

	def is_captcha_enabled(self, chat_id):
		return not bool(self.cursor.execute('SELECT 1 FROM captcha_disabled WHERE chat_id=?', (chat_id,)).fetchone())

	def set_kick_enabled(self, chat_id, enabled):
		if enabled:
			self.cursor.execute('DELETE FROM kick_disabled WHERE chat_id=?', (chat_id,))
		else:
			self.cursor.execute('INSERT OR IGNORE INTO kick_disabled(chat_id) VALUES (?)', (chat_id,))
		self.connection.commit()

	def is_kick_enabled(self, chat_id):
		return not bool(self.cursor.execute('SELECT 1 FROM kick_disabled WHERE chat_id=?', (chat_id,)).fetchone())

	def add_pending_unban(self, chat_id, user_id, next_ts):
		self.cursor.execute(
			'INSERT OR REPLACE INTO pending_unbans(chat_id, user_id, next_ts) VALUES (?, ?, ?)',
			(chat_id, user_id, next_ts)
		)
		self.connection.commit()

	def remove_all_pending_unbans(self, user_id):
		self.cursor.execute('DELETE FROM pending_unbans WHERE user_id=?', (user_id,))
		self.connection.commit()

	def remove_pending_unban(self, chat_id, user_id):
		self.cursor.execute('DELETE FROM pending_unbans WHERE chat_id=? AND user_id=?', (chat_id, user_id))
		self.connection.commit()

	def get_due_unbans(self, now):
		return self.cursor.execute(
			'SELECT chat_id, user_id FROM pending_unbans WHERE next_ts <= ?', (now,)
		).fetchall()

	def set_raid_mode(self, chat_id, on):
		if on:
			self.cursor.execute('INSERT OR IGNORE INTO raid_mode(chat_id) VALUES (?)', (chat_id,))
		else:
			self.cursor.execute('DELETE FROM raid_mode WHERE chat_id=?', (chat_id,))
		self.connection.commit()

	def is_raid_mode(self, chat_id):
		return bool(self.cursor.execute('SELECT 1 FROM raid_mode WHERE chat_id=?', (chat_id,)).fetchone())

	def get_raid_chats(self):
		return [row[0] for row in self.cursor.execute('SELECT chat_id FROM raid_mode').fetchall()]

	def mark_join_request_chat(self, chat_id):
		"""Record that this chat requires join-request approval, discovered from an actual request."""
		self.cursor.execute('INSERT OR IGNORE INTO join_request_chats(chat_id) VALUES (?)', (chat_id,))
		self.connection.commit()

	def is_join_request_chat(self, chat_id):
		return bool(self.cursor.execute('SELECT 1 FROM join_request_chats WHERE chat_id=?', (chat_id,)).fetchone())

	def set_auto_accept(self, chat_id, enabled):
		if enabled:
			self.cursor.execute('INSERT OR IGNORE INTO auto_accept(chat_id) VALUES (?)', (chat_id,))
		else:
			self.cursor.execute('DELETE FROM auto_accept WHERE chat_id=?', (chat_id,))
		self.connection.commit()

	def is_auto_accept(self, chat_id):
		return bool(self.cursor.execute('SELECT 1 FROM auto_accept WHERE chat_id=?', (chat_id,)).fetchone())

	def set_delete_join_messages(self, chat_id, enabled):
		if enabled:
			self.cursor.execute('INSERT OR IGNORE INTO delete_join_messages(chat_id) VALUES (?)', (chat_id,))
		else:
			self.cursor.execute('DELETE FROM delete_join_messages WHERE chat_id=?', (chat_id,))
		self.connection.commit()

	def is_delete_join_messages(self, chat_id):
		return bool(self.cursor.execute('SELECT 1 FROM delete_join_messages WHERE chat_id=?', (chat_id,)).fetchone())

	def record_first_captcha_visit(self, user_id, ip, user_agent, ts):
		"""Remember the IP/User-Agent seen the first time this user opened a captcha page.
		Only the very first record is kept — later visits (reloads, new sessions) never
		overwrite it. Opt-in (config.COLLECT_CAPTCHA_IPS)."""
		self.cursor.execute(
			'INSERT OR IGNORE INTO captcha_ips(user_id, ip, user_agent, ts) VALUES (?, ?, ?, ?)',
			(user_id, ip, user_agent, ts)
		)
		self.connection.commit()

	def get_captcha_ip(self, user_id):
		row = self.cursor.execute(
			'SELECT ip, user_agent, ts FROM captcha_ips WHERE user_id=?', (user_id,)
		).fetchone()
		return (row[0], row[1], row[2]) if row else None

	def get_users_by_ip(self, ip):
		"""Every user_id whose first-captcha-visit IP matches — used to spot several Telegram
		accounts opening the captcha from the same IP."""
		return [row[0] for row in self.cursor.execute(
			'SELECT user_id FROM captcha_ips WHERE ip=?', (ip,)
		).fetchall()]

	def set_rules(self, chat_id, text):
		if text:
			self.cursor.execute(
				'INSERT INTO chat_rules(chat_id, text) VALUES (?, ?) '
				'ON CONFLICT(chat_id) DO UPDATE SET text=excluded.text',
				(chat_id, text)
			)
		else:
			self.cursor.execute('DELETE FROM chat_rules WHERE chat_id=?', (chat_id,))
		self.connection.commit()

	def get_rules(self, chat_id):
		row = self.cursor.execute('SELECT text FROM chat_rules WHERE chat_id=?', (chat_id,)).fetchone()
		return row[0] if row else None

	def add_mute(self, chat_id, user_id, until):
		self.cursor.execute(
			'INSERT INTO mutes(chat_id, user_id, until) VALUES (?, ?, ?) '
			'ON CONFLICT(chat_id, user_id) DO UPDATE SET until=excluded.until',
			(chat_id, user_id, until or 0)
		)
		self.connection.commit()

	def remove_mute(self, chat_id, user_id):
		self.cursor.execute('DELETE FROM mutes WHERE chat_id=? AND user_id=?', (chat_id, user_id))
		self.connection.commit()

	def is_muted(self, chat_id, user_id):
		row = self.cursor.execute('SELECT until FROM mutes WHERE chat_id=? AND user_id=?', (chat_id, user_id)).fetchone()
		if not row:
			return False
		until = row[0]
		if until and until <= self.unix_time():
			self.remove_mute(chat_id, user_id)
			return False
		return True

	def get_mute_until(self, chat_id, user_id):
		row = self.cursor.execute('SELECT until FROM mutes WHERE chat_id=? AND user_id=?', (chat_id, user_id)).fetchone()
		return row[0] if row else 0

	def set_global_mute(self, user_id, until):
		self.cursor.execute(
			'INSERT INTO global_mutes(user_id, until) VALUES (?, ?) '
			'ON CONFLICT(user_id) DO UPDATE SET until=excluded.until',
			(user_id, until or 0)
		)
		self.connection.commit()

	def remove_global_mute(self, user_id):
		self.cursor.execute('DELETE FROM global_mutes WHERE user_id=?', (user_id,))
		self.connection.commit()

	def is_globally_muted(self, user_id):
		row = self.cursor.execute('SELECT until FROM global_mutes WHERE user_id=?', (user_id,)).fetchone()
		if not row:
			return False
		until = row[0]
		if until and until <= self.unix_time():
			self.remove_global_mute(user_id)
			return False
		return True

	def get_global_mute_until(self, user_id):
		row = self.cursor.execute('SELECT until FROM global_mutes WHERE user_id=?', (user_id,)).fetchone()
		return row[0] if row else 0

	def effective_mute(self, chat_id, user_id):
		"""Whether the user is muted in this chat (globally or locally) and until when (0 = forever)."""
		if self.is_globally_muted(user_id):
			return True, self.get_global_mute_until(user_id)
		if self.is_muted(chat_id, user_id):
			return True, self.get_mute_until(chat_id, user_id)
		return False, 0

	def set_chat_stopped(self, chat_id, stopped):
		if stopped:
			self.cursor.execute('INSERT OR IGNORE INTO stopped_chats(chat_id) VALUES (?)', (chat_id,))
		else:
			self.cursor.execute('DELETE FROM stopped_chats WHERE chat_id=?', (chat_id,))
		self.connection.commit()

	def is_chat_stopped(self, chat_id):
		return bool(self.cursor.execute('SELECT 1 FROM stopped_chats WHERE chat_id=?', (chat_id,)).fetchone())

	def set_cooldown(self, chat_id, command, seconds):
		if seconds and seconds > 0:
			self.cursor.execute(
				'INSERT INTO cooldowns(chat_id, command, seconds) VALUES (?, ?, ?) '
				'ON CONFLICT(chat_id, command) DO UPDATE SET seconds=excluded.seconds',
				(chat_id, command, seconds)
			)
		else:
			self.cursor.execute('DELETE FROM cooldowns WHERE chat_id=? AND command=?', (chat_id, command))
		self.connection.commit()

	def get_cooldown(self, chat_id, command):
		row = self.cursor.execute(
			'SELECT seconds FROM cooldowns WHERE chat_id=? AND command=?', (chat_id, command)
		).fetchone()
		return row[0] if row else 0

	def cooldown_remaining(self, chat_id, user_id, command, cd_seconds):
		row = self.cursor.execute(
			'SELECT last_ts FROM cooldown_use WHERE chat_id=? AND user_id=? AND command=?',
			(chat_id, user_id, command)
		).fetchone()
		if not row:
			return 0
		return max(0, cd_seconds - (self.unix_time() - row[0]))

	def record_cooldown_use(self, chat_id, user_id, command):
		self.cursor.execute(
			'INSERT INTO cooldown_use(chat_id, user_id, command, last_ts) VALUES (?, ?, ?, ?) '
			'ON CONFLICT(chat_id, user_id, command) DO UPDATE SET last_ts=excluded.last_ts',
			(chat_id, user_id, command, self.unix_time())
		)
		self.connection.commit()

	def add_command_ban(self, user_id):
		self.cursor.execute('INSERT OR IGNORE INTO command_banned(user_id) VALUES (?)', (user_id,))
		self.connection.commit()

	def remove_command_ban(self, user_id):
		self.cursor.execute('DELETE FROM command_banned WHERE user_id=?', (user_id,))
		self.connection.commit()

	def is_command_banned(self, user_id):
		return bool(self.cursor.execute('SELECT 1 FROM command_banned WHERE user_id=?', (user_id,)).fetchone())

	def get_command_banned(self):
		return [row[0] for row in self.cursor.execute('SELECT user_id FROM command_banned').fetchall()]

	def log_message(self, chat_id, user_id, message_id, ts):
		self._msg_buffer.append((chat_id, user_id, message_id, ts))

	def flush_messages(self):
		if not self._msg_buffer:
			return
		buffer, self._msg_buffer = self._msg_buffer, []
		self.cursor.executemany(
			'INSERT INTO recent_messages(chat_id, user_id, message_id, ts) VALUES (?, ?, ?, ?)',
			buffer
		)
		self.connection.commit()

	def purge_old_messages(self, cutoff):
		self.cursor.execute('DELETE FROM recent_messages WHERE ts < ?', (cutoff,))
		self.connection.commit()

	def get_recent_messages(self, chat_id, user_id, since=None, limit=None):
		query = 'SELECT message_id FROM recent_messages WHERE chat_id=? AND user_id=?'
		params = [chat_id, user_id]
		if since is not None:
			query += ' AND ts>=?'
			params.append(since)
		query += ' ORDER BY ts DESC, message_id DESC'
		if limit is not None:
			query += ' LIMIT ?'
			params.append(limit)
		return [row[0] for row in self.cursor.execute(query, params).fetchall()]

	def remove_messages(self, chat_id, user_id, message_ids):
		self.cursor.executemany(
			'DELETE FROM recent_messages WHERE chat_id=? AND user_id=? AND message_id=?',
			[(chat_id, user_id, mid) for mid in message_ids]
		)
		self.connection.commit()

	def log_channel_message(self, chat_id, channel_id, message_id, ts):
		self._channel_msg_buffer.append((chat_id, channel_id, message_id, ts))

	def flush_channel_messages(self):
		if not self._channel_msg_buffer:
			return
		buffer, self._channel_msg_buffer = self._channel_msg_buffer, []
		self.cursor.executemany(
			'INSERT INTO recent_channel_messages(chat_id, channel_id, message_id, ts) VALUES (?, ?, ?, ?)',
			buffer
		)
		self.connection.commit()

	def purge_old_channel_messages(self, cutoff):
		self.cursor.execute('DELETE FROM recent_channel_messages WHERE ts < ?', (cutoff,))
		self.connection.commit()

	def get_recent_channel_messages(self, chat_id, channel_id, since=None, limit=None):
		query = 'SELECT message_id FROM recent_channel_messages WHERE chat_id=? AND channel_id=?'
		params = [chat_id, channel_id]
		if since is not None:
			query += ' AND ts>=?'
			params.append(since)
		query += ' ORDER BY ts DESC, message_id DESC'
		if limit is not None:
			query += ' LIMIT ?'
			params.append(limit)
		return [row[0] for row in self.cursor.execute(query, params).fetchall()]

	def remove_channel_messages(self, chat_id, channel_id, message_ids):
		self.cursor.executemany(
			'DELETE FROM recent_channel_messages WHERE chat_id=? AND channel_id=? AND message_id=?',
			[(chat_id, channel_id, mid) for mid in message_ids]
		)
		self.connection.commit()

	def get_recent_chat_messages(self, chat_id, since=None, limit=None):
		"""Tracked message ids in this chat from anyone (users and channels), newest first."""
		query = (
			'SELECT message_id, ts FROM ('
			'SELECT message_id, ts FROM recent_messages WHERE chat_id=? '
			'UNION ALL '
			'SELECT message_id, ts FROM recent_channel_messages WHERE chat_id=?)'
		)
		params = [chat_id, chat_id]
		if since is not None:
			query += ' WHERE ts>=?'
			params.append(since)
		query += ' ORDER BY ts DESC, message_id DESC'
		if limit is not None:
			query += ' LIMIT ?'
			params.append(limit)
		return [row[0] for row in self.cursor.execute(query, params).fetchall()]

	def remove_chat_messages(self, chat_id, message_ids):
		self.cursor.executemany(
			'DELETE FROM recent_messages WHERE chat_id=? AND message_id=?',
			[(chat_id, mid) for mid in message_ids]
		)
		self.cursor.executemany(
			'DELETE FROM recent_channel_messages WHERE chat_id=? AND message_id=?',
			[(chat_id, mid) for mid in message_ids]
		)
		self.connection.commit()
