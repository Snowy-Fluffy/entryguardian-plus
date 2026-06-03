# Entry Guardian

Telegram anti-spam bot that gates group entry behind a interactive captcha. When a new user joins a group, the bot mutes them and sends them a link to play a short minigame. After completing the challenge the user receives an 8-character code, sends it to the bot, and gets unmuted.

The captcha type is chosen randomly from the enabled types: **DOOM** (shoot N enemies), **Tetris** (place N pieces), or **Mario** (reach the flagpole).

## How it works

1. A new user joins the group → bot mutes them and posts a welcome message with a button linking to the bot's DM.
2. The user sends `/start` to the bot in DM → bot replies with a button that opens the captcha page.
3. The user completes the minigame in the browser (one of DOOM / Tetris / Mario, chosen at random).
4. On completion the page shows an 8-character code.
5. The user sends the code to the bot → bot verifies it, unmutes the user in all pending chats, and deletes the welcome message.

Sessions expire after 10 minutes. Failed code attempts are limited; too many wrong attempts result in a temporary block.

## Captcha types

| Type | Task | Anti-bot measures |
|------|------|-------------------|
| **DOOM** | Kill N enemies | N kill events with per-kill cooldown + minimum play time |
| **Tetris** | Place N pieces on target slots | N placement events + minimum play time |
| **Mario** | Reach the flagpole (shortened 1-1 level) | Flagpole event + minimum time from page load to flagpole (≥ 5 s) |

All types share a common server-side defense: a per-session **challenge token** (generated at page load, required for every API call) and a **minimum play time** check before `/complete` is accepted.

## Requirements

- Python 3.11+
- Docker + Docker Compose (recommended)
- A domain with HTTPS (nginx reverse proxy) so the captcha page is accessible from the internet
- A Telegram bot token from [@BotFather](https://t.me/BotFather) with **Group privacy mode disabled** and **Group member events** enabled

## Configuration

Copy `.env.example` to `.env` and fill in the values (`.env.example` lists all available options):

```env
TOKEN=<bot token>
DB_PATH=users.db

# Captcha web server
WEB_HOST=0.0.0.0
WEB_PORT=8080
CAPTCHA_BASE_URL=https://yourdomain.com/captcha

# Which captcha types to use (chosen randomly per session)
CAPTCHA_TYPES=doom,tetris,mario

# Session lifetime
CAPTCHA_TIMEOUT=600       # seconds (default 10 min)

# General anti-bot timing
MIN_PLAY_TIME=3.0         # minimum seconds page must be open before /complete is accepted
KILL_COOLDOWN=0.5         # minimum seconds between registered kill events (doom)

# Per-type difficulty
CAPTCHA_ENEMIES=4         # DOOM: enemies the player must kill
CAPTCHA_MIN_PIECES=3      # Tetris: pieces the player must place
MARIO_MIN_PLAY_TIME=5.0   # Mario: minimum seconds from page load to flagpole event

# Bot behaviour
MAX_ATTEMPTS=3            # wrong code attempts before temp block
COOL_DOWN=900             # temp block duration in seconds
LOCALE=ru_RU
OWNERS=                   # comma-separated Telegram user IDs of bot owners (full access in every chat)
```

## Moderation

The bot has a per-chat role system with three levels:

- **Owners** — defined only in `.env` via `OWNERS`. They have access to every command in every chat the bot is in.
- **Admins** — scoped to a single chat. The **chat creator** is made an admin automatically when the bot is added. Admins have access to all commands in their own chat (never in other chats) and can manage both admins and moderators of that chat.
- **Moderators** — scoped to a single chat, below admins. They can be added/removed only by admins (or owners) and cannot manage roles themselves.

### Commands (group chats only)

| Command | Who can use it | Effect |
|---------|----------------|--------|
| `/ban [reason]` | moderators, admins, owners | **Local** ban: ban the target user only in **this** chat and announce it |
| `/gban [reason]` | admins, owners | **Global** ban: blocklist the user and ban them in every chat the bot is in. Announced in **all** chats — the origin chat shows who banned them (and the reason), other chats show that the user was banned in the origin chat (and the reason) |
| `/sban` | admins, owners | **Silent local** ban: ban the target only in **this** chat, with nothing posted to the chat |
| `/sgban` | admins, owners | **Silent** global ban: same reach as `/gban`, but nothing is posted to the chat |
| `/unban` | moderators, admins, owners | **Local** unban: lift the ban in **this** chat as an exception. The user stays on the global blocklist and still can't join the bot's other chats |
| `/ungban` | admins, owners | **Global** unban: remove the user from the blocklist and lift their ban in every chat the bot is in |
| `/unsban` | admins, owners | **Silent local** unban: same as `/unban`, with nothing posted to the chat |
| `/unsgban` | admins, owners | **Silent global** unban: same as `/ungban`, with nothing posted to the chat |
| `/mute [duration] [reason]` | moderators, admins, owners | Mute the target in **this** chat. Duration like `10m`, `1h`, `1d`, `1w` (omit for permanent); reason optional. e.g. `mute @user 10m spam` or reply with `mute 1d` |
| `/gmute` | admins, owners | **Global** mute: mute the target in every chat the bot is in, announced in all of them |
| `/smute` | admins, owners | **Silent local** mute (no announcement) |
| `/gsmute` | admins, owners | **Silent global** mute (no announcement) |
| `/unmute [reason]` | moderators, admins, owners | Unmute the target in **this** chat (optional reason) |
| `/ungmute [reason]` | admins, owners | **Global** unmute across every chat |
| `/unsmute` | admins, owners | **Silent local** unmute |
| `/ungsmute` | admins, owners | **Silent global** unmute |
| `/delete` | moderators, admins, owners | Delete the message this command **replies to** (and the command itself). Telegram only lets bots delete messages up to 48h old |
| `/raid_on` | admins, owners | Enable **anti-raid** in this chat: every newcomer is locally banned, silently, with no captcha shown. The blocklist ID check still runs. While on, the bot posts a reminder every 5 minutes with how many were banned, so it isn't left on by accident |
| `/raid_off` | admins, owners | Disable anti-raid |
| `/add_adm` | admins, owners | Make the target user an admin of this chat |
| `/del_adm` | admins, owners | Remove an admin of this chat (the chat creator cannot be removed) |
| `/add_mod` | admins, owners | Make the target user a moderator of this chat |
| `/del_mod` | admins, owners | Remove a moderator of this chat |
| `/rules` | everyone | Show this chat's custom rules (set via the admin panel) |
| `/staff` | everyone | List the admins and moderators of this chat (owners are not shown) |
| `/admin` | admins, owners | Open the **admin panel** in a private chat with the bot (see below) |
| `/help` | moderators, admins, owners | Show the list of available commands |

The target user can be specified by **replying** to their message, or by passing their numeric **ID** (`/ban 123456789`) or **@username** (`/ban @user spam`). Replying or using a numeric ID is the most reliable; `@username` only resolves for users the bot has already seen in a chat (privacy mode must be off) or for public accounts.

Command messages are deleted automatically after they are processed (the bot needs the *delete messages* admin right for this).

### Admin panel (`/admin`)

Sent in a **private chat** with the bot, `/admin` opens an inline-button panel. It is available to **admins** (for their own chats) and **owners** (for every chat the bot is in), and only **after the user has passed the captcha** (i.e. is verified). From the panel you can:

- pick a chat (when you manage more than one);
- see its admins and moderators;
- add or remove an admin or a moderator (the bot asks for the target's `@username` or ID);
- view that chat's staff action log;
- set or clear that chat's custom rules (shown in-chat via `/rules`);
- toggle the captcha for that chat. When the captcha is off, new members can write immediately without solving it — but the blocklist ID check on join still always runs;
- toggle anti-raid for that chat (same effect as `/raid_on` / `/raid_off`).

Owners additionally get two owner-only controls in the panel: stopping/resuming a chat (the bot ignores all commands from it) and a **global command ban** — a denylist of users who are completely forbidden from using any bot command (except `/start`) and the admin panel everywhere, even if they are admins or moderators (owners can't be added).

The staff action log keeps the **last 200 actions per chat** (bans, unbans, role changes). Owner actions are **not** logged.

### Blocklist

There is no `BLOCKLIST` env variable anymore. The blocklist lives in the database and is populated by `/gban` and `/sgban`. Anyone on the blocklist is banned automatically when they try to join any chat the bot guards. Because Telegram does not let a bot enumerate a group's existing members when it is added, a blocklisted user who is *already* in a newly-added chat is banned as soon as they are next active (send a message), provided the bot is an admin there and privacy mode is off.

> The bot can only enumerate chats it has observed *after* this version was deployed (Telegram does not expose the full list of a bot's chats). Re-adding the bot, or any message in a group, registers that chat for global bans.

## Running with Docker

```bash
# Create an empty database file so Docker mounts it as a file, not a directory
touch users.db

docker compose up -d
docker compose logs -f
```

The web server listens on `127.0.0.1:8080` on the host. Proxy it with nginx:

```nginx
location /captcha/ {
    proxy_pass http://127.0.0.1:8080/captcha/;
}

location /api/captcha/ {
    proxy_pass http://127.0.0.1:8080/api/captcha/;
}

location /doom/ {
    proxy_pass http://127.0.0.1:8080/doom/;
}

location /tetris/ {
    proxy_pass http://127.0.0.1:8080/tetris/;
}

location /mario/ {
    proxy_pass http://127.0.0.1:8080/mario/;
}
```

> **Note:** set `WEB_HOST=0.0.0.0` in `.env` — inside Docker the container's loopback is not reachable from the host.

## Running without Docker

```bash
pip install -r requirements.txt
cp .env.example .env  # edit .env
python run.py
```

## Adding the bot to a group

1. Create a bot via [@BotFather](https://t.me/BotFather), get the token, set it as `TOKEN` in `.env`.
2. Add the bot to your group and grant it **administrator** rights (restrict members, delete messages, ban members).
3. Start the bot.

## Project structure

```
entryguardian/
├── run.py                    # entry point — starts bot, web server, expiry task
├── config.py                 # settings loaded from .env
├── webserver.py              # aiohttp: captcha page, kill API, complete API, static files
├── session_manager.py        # in-memory session store
├── personal_msg_handler.py   # /start and code verification in bot DM
├── chat_member_handler.py    # new member detection, mute, welcome message
├── reaction_handler.py       # reaction events
├── moderation_handler.py     # role commands + auto-admin on bot join
├── permissions.py            # role/permission helpers (owner, admin, moderator)
├── dbmanager.py              # SQLite: verified users, pending chats, roles
├── translator.py             # locale string loader
├── captcha.html              # DOOM minigame page (served under /doom/)
├── tetris_captcha.html       # Tetris minigame page (served under /tetris/)
├── tetris_captcha.js         # Tetris game logic
├── mario_captcha.html        # Mario minigame page (served under /mario/)
├── FullScreenMario.min.js    # FullScreenMario engine (served under /mario/)
├── templates/
│   └── captcha_wrapper.html  # outer page that hosts the game iframe
├── l10n/
│   ├── ru_RU.json            # Russian locale strings
│   └── en_US.json            # English locale strings
├── static/                   # DOOM game assets (sprites, sounds)
├── Dockerfile
└── docker-compose.yml
```

## License

GNU General Public License v3.0 — see `LICENSE`.
