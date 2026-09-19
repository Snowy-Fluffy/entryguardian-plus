class BansMixin:
	def add_to_blocklist(self, user_id):
		"""Global ban. The timestamp is the *first* time this ban was issued: a repeated /gban of
		someone already listed keeps the original date (an /ungban deletes the row, so a later
		re-ban starts fresh)."""
		self.cursor.execute('INSERT OR IGNORE INTO blocklist(user_id, ts) VALUES (?, ?)', (user_id, self.unix_time()))
		self.connection.commit()

	def get_blocklist_ts(self, user_id):
		"""When the user was globally banned, or None for a pre-timestamp row nothing could backfill."""
		row = self.cursor.execute('SELECT ts FROM blocklist WHERE user_id=?', (user_id,)).fetchone()
		return row[0] if row else None

	def remove_from_blocklist(self, user_id):
		self.cursor.execute('DELETE FROM blocklist WHERE user_id=?', (user_id,))
		self.connection.commit()

	def is_blocklisted(self, user_id):
		return bool(self.cursor.execute('SELECT 1 FROM blocklist WHERE user_id=?', (user_id,)).fetchone())

	def get_blocklist(self):
		return [row[0] for row in self.cursor.execute('SELECT user_id FROM blocklist').fetchall()]

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

	def add_local_ban(self, chat_id, user_id):
		"""Record that a user was locally banned in this chat, so a manual/out-of-band unban
		(done outside the bot) can be caught and reversed reactively — mirrors mutes, which
		already had this table-backed tracking; a plain local ban previously had none."""
		self.cursor.execute('INSERT OR IGNORE INTO local_bans(chat_id, user_id) VALUES (?, ?)', (chat_id, user_id))
		self.connection.commit()

	def remove_local_ban(self, chat_id, user_id):
		self.cursor.execute('DELETE FROM local_bans WHERE chat_id=? AND user_id=?', (chat_id, user_id))
		self.connection.commit()

	def is_locally_banned(self, chat_id, user_id):
		return bool(self.cursor.execute('SELECT 1 FROM local_bans WHERE chat_id=? AND user_id=?', (chat_id, user_id)).fetchone())

	def clear_local_bans(self, user_id):
		self.cursor.execute('DELETE FROM local_bans WHERE user_id=?', (user_id,))
		self.connection.commit()

	def add_channel_to_blocklist(self, channel_id):
		self.cursor.execute('INSERT OR IGNORE INTO channel_blocklist(channel_id, ts) VALUES (?, ?)', (channel_id, self.unix_time()))
		self.connection.commit()

	def get_channel_blocklist_ts(self, channel_id):
		row = self.cursor.execute('SELECT ts FROM channel_blocklist WHERE channel_id=?', (channel_id,)).fetchone()
		return row[0] if row else None

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
