class MessageLogMixin:
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
