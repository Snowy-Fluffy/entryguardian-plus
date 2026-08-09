class RolesMixin:
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
