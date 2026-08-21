import config


class UsersMixin:
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

	def get_captcha_status(self, user_id):
		"""(verified, blocked_until) for the captcha's own tracking of this user, or None if
		they've never gone through it at all (no row in `user`). blocked_until is only
		meaningful (in the future) while a temp-block from too many wrong code attempts is
		still active; -1/past means no active block."""
		row = self.cursor.execute('SELECT verified, blocked_until FROM user WHERE id=?', (user_id,)).fetchone()
		return (bool(row[0]), row[1]) if row else None

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
		('local_bans', 'user_id'),
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

	def get_matched_ip_groups(self, order_by='cnt'):
		"""Every first-captcha-visit IP shared by 2+ distinct users, also flagging whether any of
		those users is currently on the global blocklist (has_banned, 1/0) -- computed via a
		LEFT JOIN so /iptop's list view doesn't need a per-row follow-up query. order_by='cnt'
		sorts by group size desc (ties by most recent); order_by='recent' sorts by most recent
		visit in the group desc (ties by size). Excludes '' / 'unknown' (the _client_ip() fallback
		placeholder) so a proxy misconfiguration funneling many users onto 'unknown' doesn't look
		like a real match."""
		order_sql = 'cnt DESC, last_ts DESC' if order_by == 'cnt' else 'last_ts DESC, cnt DESC'
		# order_sql is one of two hardcoded literals chosen in Python -- never built from caller
		# input -- so this stays within the "always parameterize" rule; ip/threshold use `?`.
		return self.cursor.execute(
			f'SELECT ci.ip, COUNT(*) AS cnt, MAX(ci.ts) AS last_ts, '
			f'MAX(CASE WHEN bl.user_id IS NOT NULL THEN 1 ELSE 0 END) AS has_banned '
			f'FROM captcha_ips ci LEFT JOIN blocklist bl ON bl.user_id = ci.user_id '
			f"WHERE ci.ip IS NOT NULL AND ci.ip <> '' AND ci.ip <> 'unknown' "
			f'GROUP BY ci.ip HAVING COUNT(*) >= 2 ORDER BY {order_sql}'
		).fetchall()

	def get_users_by_ip_with_details(self, ip):
		"""user_id, user_agent, ts for every user sharing this first-captcha-visit ip, most recent
		first -- feeds /iptop's drill-down view."""
		return self.cursor.execute(
			'SELECT user_id, user_agent, ts FROM captcha_ips WHERE ip=? ORDER BY ts DESC', (ip,)
		).fetchall()
