class ChatSettingsMixin:
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

	def get_chat_perms(self, chat_id):
		row = self.cursor.execute('SELECT perms FROM chat_perms WHERE chat_id=?', (chat_id,)).fetchone()
		if row is None:
			return None
		return [p for p in row[0].split(',') if p]

	def set_chat_perms(self, chat_id, perms):
		self.cursor.execute('INSERT OR REPLACE INTO chat_perms(chat_id, perms) VALUES (?, ?)',
			(chat_id, ','.join(perms)))
		self.connection.commit()

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

	def set_delete_system_messages(self, chat_id, enabled):
		if enabled:
			self.cursor.execute('INSERT OR IGNORE INTO delete_system_messages(chat_id) VALUES (?)', (chat_id,))
		else:
			self.cursor.execute('DELETE FROM delete_system_messages WHERE chat_id=?', (chat_id,))
		self.connection.commit()

	def is_delete_system_messages(self, chat_id):
		return bool(self.cursor.execute('SELECT 1 FROM delete_system_messages WHERE chat_id=?', (chat_id,)).fetchone())

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
