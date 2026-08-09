class AntispamMixin:
	_ANTISPAM_DEFAULTS = {
		'enabled': True, 'count': 3, 'window': 21600, 'mute_seconds': 1800, 'notify': True,
		'unicode_guard': True,
	}

	def get_antispam_settings(self, chat_id):
		row = self.cursor.execute(
			'SELECT enabled, count, window, mute_seconds, notify, unicode_guard '
			'FROM antispam_settings WHERE chat_id=?',
			(chat_id,)
		).fetchone()
		if row is None:
			return dict(self._ANTISPAM_DEFAULTS)
		return {
			'enabled': bool(row[0]), 'count': row[1], 'window': row[2], 'mute_seconds': row[3],
			'notify': bool(row[4]), 'unicode_guard': bool(row[5]),
		}

	def set_antispam_field(self, chat_id, field, value):
		current = self.get_antispam_settings(chat_id)
		current[field] = value
		self.cursor.execute(
			'INSERT INTO antispam_settings(chat_id, enabled, count, window, mute_seconds, notify, unicode_guard) '
			'VALUES (?, ?, ?, ?, ?, ?, ?) '
			'ON CONFLICT(chat_id) DO UPDATE SET enabled=excluded.enabled, count=excluded.count, '
			'window=excluded.window, mute_seconds=excluded.mute_seconds, notify=excluded.notify, '
			'unicode_guard=excluded.unicode_guard',
			(chat_id, int(current['enabled']), current['count'], current['window'],
			 current['mute_seconds'], int(current['notify']), int(current['unicode_guard']))
		)
		self.connection.commit()

	def get_antispam_streak(self, chat_id, user_id):
		row = self.cursor.execute(
			'SELECT signature, count, first_ts, message_ids FROM antispam_streak WHERE chat_id=? AND user_id=?',
			(chat_id, user_id)
		).fetchone()
		if row is None:
			return None
		return {
			'signature': row[0], 'count': row[1], 'first_ts': row[2],
			'message_ids': [int(x) for x in row[3].split(',') if x],
		}

	def set_antispam_streak(self, chat_id, user_id, signature, count, first_ts, message_ids):
		self.cursor.execute(
			'INSERT INTO antispam_streak(chat_id, user_id, signature, count, first_ts, message_ids) '
			'VALUES (?, ?, ?, ?, ?, ?) '
			'ON CONFLICT(chat_id, user_id) DO UPDATE SET signature=excluded.signature, count=excluded.count, '
			'first_ts=excluded.first_ts, message_ids=excluded.message_ids',
			(chat_id, user_id, signature, count, first_ts, ','.join(str(m) for m in message_ids))
		)
		self.connection.commit()

	def clear_antispam_streak(self, chat_id, user_id):
		self.cursor.execute('DELETE FROM antispam_streak WHERE chat_id=? AND user_id=?', (chat_id, user_id))
		self.connection.commit()
