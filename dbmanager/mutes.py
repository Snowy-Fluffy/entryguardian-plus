class MutesMixin:
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
