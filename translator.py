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

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar

_L10N_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'l10n')

_cache: dict[str, dict] = {}

_current: ContextVar[str | None] = ContextVar('entryguardian_lang', default=None)
"""The locale of whoever the bot is currently talking to. Set once at the entry of an update
handler (see moderation_handler.LanguageMiddleware / UserTrackingMiddleware) so the ~350
`translator.get_string()` calls need no language argument; unset means the process default."""

# Telegram client language codes that map to the Russian locale. Everything else gets English.
_RU_CODES = frozenset({'ru', 'uk', 'be', 'kk', 'ky', 'tg', 'uz'})


def available_locales() -> list[str]:
	return sorted(f[:-5] for f in os.listdir(_L10N_DIR) if f.endswith('.json'))


def _load(lc_code: str) -> dict:
	table = _cache.get(lc_code)
	if table is None:
		with open(os.path.join(_L10N_DIR, f'{lc_code}.json'), encoding='utf-8') as file:
			table = _cache[lc_code] = json.load(file)
	return table


def normalize(lang: str | None) -> str | None:
	"""`lang` if it names a shipped locale, else None."""
	return lang if lang and lang in available_locales() else None


def locale_name(lc_code: str) -> str:
	"""Human name of a locale in its own language (`lang_name` key of its file)."""
	return _load(lc_code).get('lang_name', lc_code)


def lang_for_code(language_code: str | None, default: str) -> str:
	"""Map a Telegram `language_code` (IETF tag such as 'ru', 'pt-br') to a shipped locale.
	No code at all (some clients hide it) falls back to `default`."""
	if not language_code:
		return default
	base = language_code.lower().replace('_', '-').split('-')[0]
	for lc in available_locales():
		if lc.lower().split('_')[0] == base:
			return lc
	if base in _RU_CODES and 'ru_RU' in available_locales():
		return 'ru_RU'
	return 'en_US' if 'en_US' in available_locales() else default


def current_lang() -> str | None:
	return _current.get()


def set_lang(lang: str | None):
	"""Set the locale for the current context; returns a token for reset_lang()."""
	return _current.set(normalize(lang))


def reset_lang(token) -> None:
	_current.reset(token)


@contextmanager
def use_lang(lang: str | None):
	"""Speak `lang` inside the block (None/unknown = the process default)."""
	token = _current.set(normalize(lang))
	try:
		yield
	finally:
		_current.reset(token)


class Translator:
	locale = dict()

	def __init__(self, lc_code):
		self.default = lc_code
		self.locale = _load(lc_code)

	def get_string(self, string_id):
		lang = _current.get()
		if lang and lang != self.default:
			value = _load(lang).get(string_id)
			if value is not None:
				return value
		return self.locale.get(string_id, string_id)

	def get_string_in(self, lang, string_id):
		"""One string in an explicit locale, regardless of the current context."""
		with use_lang(lang):
			return self.get_string(string_id)
