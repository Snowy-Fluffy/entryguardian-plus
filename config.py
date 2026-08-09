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

# Per-IP rate limit on the captcha web endpoints (/captcha/* and /api/captcha/*).
RATE_LIMIT_MAX: int = int(os.getenv('RATE_LIMIT_MAX', '40'))
RATE_LIMIT_WINDOW: float = float(os.getenv('RATE_LIMIT_WINDOW', '10'))

# Trust X-Real-IP / X-Forwarded-For from the reverse proxy in front of the web server (nginx,
# per the README setup) instead of the raw TCP peer address — which, behind any reverse proxy
# (and especially through Docker's published-port NAT), is only the proxy's own address, not
# the visitor's. Only disable this if the web server is ever reachable directly, bypassing
# your reverse proxy — otherwise a client could spoof its own IP via these headers.
TRUST_PROXY_HEADERS: bool = os.getenv('TRUST_PROXY_HEADERS', '1').strip().lower() in ('1', 'true', 'yes', 'on')

# Optional: remember the IP address a user solved the captcha from (shown in /punl).
# Off by default — enabling this stores personal data (IP addresses), so it's opt-in.
COLLECT_CAPTCHA_IPS: bool = os.getenv('COLLECT_CAPTCHA_IPS', '0').strip().lower() in ('1', 'true', 'yes', 'on')

# Repeated-message antispam: mutes a user who posts the same content N times in a row within a
# time window (configurable per chat via /admin). On by default. This requires the bot to look
# at the content/media id of every message to detect duplicates — turn off if that's a concern.
ANTISPAM_ENABLED: bool = os.getenv('ANTISPAM_ENABLED', '1').strip().lower() in ('1', 'true', 'yes', 'on')
