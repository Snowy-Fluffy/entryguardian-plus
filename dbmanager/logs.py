class LogsMixin:
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
