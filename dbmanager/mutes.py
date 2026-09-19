class MutesMixin:
	def add_mute(self, chat_id, user_id, until):
		"""Local mute. Also drops any local exception for this chat: a fresh local mute is the
		chat's newest decision and overrides an earlier local amnesty of a global mute."""
		self.cursor.execute(
			'INSERT INTO mutes(chat_id, user_id, until) VALUES (?, ?, ?) '
			'ON CONFLICT(chat_id, user_id) DO UPDATE SET until=excluded.until',
			(chat_id, user_id, until or 0)
		)
		self.cursor.execute('DELETE FROM mute_exceptions WHERE chat_id=? AND user_id=?', (chat_id, user_id))
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

	def add_mute_exception(self, chat_id, user_id):
		"""A local /unmute of a globally muted user: lifts the global mute in this one chat only.
		Mirrors ban_exceptions for bans — local decisions win over global ones."""
		self.cursor.execute('INSERT OR IGNORE INTO mute_exceptions(chat_id, user_id) VALUES (?, ?)', (chat_id, user_id))
		self.connection.commit()

	def remove_mute_exception(self, chat_id, user_id):
		self.cursor.execute('DELETE FROM mute_exceptions WHERE chat_id=? AND user_id=?', (chat_id, user_id))
		self.connection.commit()

	def is_mute_exception(self, chat_id, user_id):
		return bool(self.cursor.execute('SELECT 1 FROM mute_exceptions WHERE chat_id=? AND user_id=?', (chat_id, user_id)).fetchone())

	def clear_mute_exceptions(self, user_id):
		"""Drop every per-chat exception for a user — a fresh global mute (or a global unmute,
		which makes them moot) resets any earlier local amnesty."""
		self.cursor.execute('DELETE FROM mute_exceptions WHERE user_id=?', (user_id,))
		self.connection.commit()

	def effective_mute(self, chat_id, user_id):
		"""Whether the user is muted in this chat and until when (0 = forever). Local beats
		global, in this order: an active local mute always applies; otherwise a local exception
		(a local /unmute of a global mute) means not muted here; otherwise the global mute
		applies."""
		if self.is_muted(chat_id, user_id):
			return True, self.get_mute_until(chat_id, user_id)
		if self.is_mute_exception(chat_id, user_id):
			return False, 0
		if self.is_globally_muted(user_id):
			return True, self.get_global_mute_until(user_id)
		return False, 0
