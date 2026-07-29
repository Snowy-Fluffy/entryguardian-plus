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

import os
from dotenv import load_dotenv

load_dotenv()

TOKEN: str = os.environ['TOKEN']
DB_PATH: str = os.getenv('DB_PATH', 'users.db')
MAX_ATTEMPTS: int = int(os.getenv('MAX_ATTEMPTS', '3'))
COOL_DOWN: int = int(os.getenv('COOL_DOWN', '900'))
LOCALE: str = os.getenv('LOCALE', 'ru_RU')
OWNERS: set[int] = {int(x.strip()) for x in os.getenv('OWNERS', '').split(',') if x.strip()}

WEB_HOST: str = os.getenv('WEB_HOST', '127.0.0.1')
WEB_PORT: int = int(os.getenv('WEB_PORT', '8080'))
CAPTCHA_BASE_URL: str = os.getenv('CAPTCHA_BASE_URL', 'http://localhost:8080/captcha')
CAPTCHA_TIMEOUT: int = int(os.getenv('CAPTCHA_TIMEOUT', '600'))
CAPTCHA_ENEMIES: int = int(os.getenv('CAPTCHA_ENEMIES', '4'))
MIN_PLAY_TIME: float = float(os.getenv('MIN_PLAY_TIME', '3.0'))
KILL_COOLDOWN: float = float(os.getenv('KILL_COOLDOWN', '0.5'))
CAPTCHA_MIN_PIECES: int = int(os.getenv('CAPTCHA_MIN_PIECES', '3'))
MARIO_MIN_PLAY_TIME: float = float(os.getenv('MARIO_MIN_PLAY_TIME', '5.0'))
CAPTCHA_TYPES: list[str] = [t.strip() for t in os.getenv('CAPTCHA_TYPES', 'doom,tetris,mario').split(',') if t.strip()]
TURNSTILE_SITE_KEY: str = os.getenv('TURNSTILE_SITE_KEY', '')
TURNSTILE_SECRET_KEY: str = os.getenv('TURNSTILE_SECRET_KEY', '')
TURNSTILE_ENABLED: bool = bool(TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY)
USERNAME_RESOLVE: int = int(os.getenv('USERNAME_RESOLVE', '0'))
APIFY_TOKEN: str = os.getenv('APIFY_TOKEN', '')
