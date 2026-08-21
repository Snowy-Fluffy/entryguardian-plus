# Entry Guardian

Telegram anti-spam bot that gates group entry behind a interactive captcha. When a new user joins a group, the bot mutes them and sends them a link to play a short minigame. After completing the challenge the user receives a 6-character code, sends it to the bot, and gets unmuted.

The captcha type is chosen randomly from the enabled types: **DOOM** (shoot N enemies), **Tetris** (place N pieces), or **Mario** (reach the flagpole).

## How it works

1. A new user joins the group → bot mutes them and posts a welcome message with a button linking to the bot's DM.
2. The user sends `/start` to the bot in DM → bot replies with a button that opens the captcha page.
3. The user completes the minigame in the browser (one of DOOM / Tetris / Mario, chosen at random).
4. Up to three more stages run before the code is revealed, each gating the next:
   - **Cloudflare Turnstile** — a browser-verification widget; the token is checked server-to-server against Cloudflare's `siteverify` endpoint. Optional: if `TURNSTILE_SITE_KEY`/`TURNSTILE_SECRET_KEY` aren't set, this stage is skipped entirely and the flow goes straight from the minigame to Altcha.
   - **Altcha** proof-of-work — a self-hosted (`altcha-org/altcha`) widget solving a `PBKDF2/SHA-512` challenge (cost `10000`, genuinely random effort — no pre-solved/deterministic challenges), shown after a short "Ещё один момент..." message. The challenge is signed (HMAC, in-process secret) and bound to the session id so a solved payload can't be replayed against a different session.
   - A classic distorted-text **captcha image** (`lepture/captcha`) showing the 6-character code — replaces the old "ghost font" noise-GIF approach (which turned out to be breakable by a script tracking the motion pattern between the foreground/background noise).
5. The user sends the code to the bot → bot verifies it, unmutes the user in all pending chats, and deletes the welcome message.

Sessions expire after 10 minutes. Failed code attempts are limited; too many wrong attempts result in a temporary block. A newcomer who never passes the captcha within **24 hours** is kicked from the chat (and immediately unbanned, so they can rejoin and try again). This 24h kick is on by default and can be turned off per chat from the admin panel; the welcome message tells the newcomer about the 24-hour limit only when the kick is enabled for that chat. The unban half of a kick is recorded in a persistent queue and retried (honouring rate-limit `Retry-After`) until it succeeds, so a failed/rate-limited unban — or a restart mid-kick — never leaves someone stuck banned. Re-joining within an hour of the last prompt doesn't re-post the welcome message, to avoid spam.

## Captcha types

| Type | Task | Anti-bot measures |
|------|------|-------------------|
| **DOOM** | Kill N enemies | N kill events with per-kill cooldown + minimum play time |
| **Tetris** | Place N pieces on target slots | N placement events + minimum play time |
| **Mario** | Reach the flagpole (shortened 1-1 level) | Flagpole event + minimum time from page load to flagpole (≥ 5 s) |

All types share a common server-side defense: a per-session **challenge token** (generated at page load, required for every API call) and a **minimum play time** check before `/complete` is accepted. On top of that, the captcha web endpoints (`/captcha/*` and `/api/captcha/*`) are **rate-limited per IP** (`RATE_LIMIT_MAX` requests per `RATE_LIMIT_WINDOW` seconds, default 40/10s) so a script hitting them directly — bypassing the minigame's own pacing — can't flood them.

## Requirements

- Python 3.11+
- Docker + Docker Compose (recommended)
- A domain with HTTPS (nginx reverse proxy) so the captcha page is accessible from the internet
- A Telegram bot token from [@BotFather](https://t.me/BotFather) with **Group privacy mode disabled** and **Group member events** enabled
- Optional: a [Cloudflare Turnstile](https://dash.cloudflare.com/?to=/:account/turnstile) widget (site key + secret key) for the browser-verification stage of the captcha — if omitted, that stage is skipped

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

# Cloudflare Turnstile keys — optional, gates the stage after the minigame.
# Leave both empty to skip this stage entirely.
# For local dev without a real Cloudflare-registered domain, use Cloudflare's dummy test keys
# (always pass, work on any host including localhost):
#   TURNSTILE_SITE_KEY=1x00000000000000000000AA
#   TURNSTILE_SECRET_KEY=1x0000000000000000000000000000000AA
TURNSTILE_SITE_KEY=
TURNSTILE_SECRET_KEY=

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

# Per-IP rate limit on the captcha web endpoints (/captcha/* and /api/captcha/*)
RATE_LIMIT_MAX=40           # requests per RATE_LIMIT_WINDOW seconds, per IP
RATE_LIMIT_WINDOW=10

# Trust X-Real-IP/X-Forwarded-For from nginx instead of the raw TCP peer (which behind any
# reverse proxy, and especially through Docker's published-port NAT, is only the proxy's own
# address). Keep on for the nginx setup below; turn off only if the port is ever reachable
# directly, bypassing nginx.
TRUST_PROXY_HEADERS=1

# Optional: remember the IP + User-Agent seen the FIRST time a user opens their captcha page,
# shown in /punl. Off by default (stores personal data) — see "Moderation" below.
COLLECT_CAPTCHA_IPS=0

# Repeated-message antispam (deletes+mutes a user who posts the same content N times in a row
# within a time window). On by default. Per-chat thresholds are set via /admin — this only gates
# whether the feature exists at all; requires inspecting every message's content/media id to
# detect duplicates, so turn off if that's a privacy concern. See "Moderation" below.
ANTISPAM_ENABLED=1
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
| `/mute [duration] [reason]` | moderators, admins, owners | Mute the target in **this** chat. Duration like `10m` (minutes), `1h`, `1d`, `1w`, `2mo` (months — note `mo`, since `m` is minutes); **minimum 1 minute** (seconds aren't accepted); omit for permanent; reason optional. e.g. `mute @user 10m spam` or reply with `mute 1d` |
| `/gmute` | admins, owners | **Global** mute: mute the target in every chat the bot is in, announced in all of them |
| `/smute` | admins, owners | **Silent local** mute (no announcement) |
| `/gsmute` | admins, owners | **Silent global** mute (no announcement) |
| `/unmute [reason]` | moderators, admins, owners | Unmute the target in **this** chat (optional reason) |
| `/ungmute [reason]` | admins, owners | **Global** unmute across every chat |
| `/unsmute` | admins, owners | **Silent local** unmute |
| `/ungsmute` | admins, owners | **Silent global** unmute |
| `/delete` | moderators, admins, owners | Delete the message this command **replies to** (and the command itself). Telegram only lets bots delete messages up to 48h old |
| `/delete_user [target] [c<N>\|period]` | moderators, admins, owners | Bulk-delete a user's **tracked messages in this chat** — by reply or `@username`/ID, same target resolution as `/mute`/`/ban`. With no extra argument, deletes everything tracked (up to the 2-day retention window below); `c200` deletes their last 200 tracked messages; a duration like `1h`/`1d` deletes everything from that period onward (rejected with an error if it exceeds the 2-day retention window). e.g. `/delete_user @spammer 1h` or reply with `/delete_user c50`. Confirms in the chat with how many were deleted and whether it was "all", a count, or a period. **Reply to a channel's post** with `/delete_user` to bulk-delete that channel's tracked messages instead (same as banning a channel, this only works by reply, not by ID/@username) |
| `/sdelete_user` | admins, owners | Silent variant of `/delete_user` — same bulk deletion, nothing posted to the chat |
| `/delete_chat [c<N>\|period]` | admins, owners | Same as `/delete_user`, but for **every tracked message in the chat, from anyone** (users and channels alike) — no target. `/delete_chat c500` clears the last 500 tracked messages, `/delete_chat 1h` clears everything tracked from the last hour, plain `/delete_chat` clears everything tracked (up to the 2-day window) |
| `/sdelete_chat` | admins, owners | Silent variant of `/delete_chat` — same bulk deletion, nothing posted to the chat |
| `/punl` | moderators, admins, owners (in a group); admins/owners (in DM) | Show a user's **punishment history** (bans, mutes, their reversals, deletions) plus "First seen", and, if they're currently on the global blocklist, a leading "globally blocked" line. Target by reply, ID or @username. In a group it covers that chat; sent in DM it aggregates across every chat the requester manages, labelling each entry with its chat. **Reply to a channel's post** instead to see that channel's punishment history (bans/unbans only — a channel can't be muted); no "First seen" for channels, since that's only tracked for real users |
| `/uinfo` | owners only, **private chat with the bot only** (silently ignored elsewhere) | Show a user's display name/@username/id (same resolution as `/punl` in DM), a leading "globally blocked" line if they're currently on the global blocklist, whether they've **passed the captcha** (or are currently temp-blocked from wrong code attempts, or never started it), "First seen", and, if `COLLECT_CAPTCHA_IPS` is on and recorded, the IP/User-Agent from their first captcha visit — no punishment history (use `/punl` for that). Target by reply, ID or @username |
| `/iptop` | owners only, **private chat with the bot only** (silently ignored elsewhere) | Paginated list of every first-captcha-visit IP shared by 2+ distinct users (requires `COLLECT_CAPTCHA_IPS`), for spotting ban evasion / multi-accounting. A button toggles sorting between most users and most recently matched; tapping an IP drills into the list of users behind it, formatted the same way as `/uinfo` (name, `ipinfo.io` link, User-Agent) |
| `/raid_on` | admins, owners | Enable **anti-raid** in this chat: every newcomer is locally banned, silently, with no captcha shown. The blocklist ID check still runs. While on, the bot posts a reminder every 5 minutes with how many were banned, so it isn't left on by accident — each new reminder replaces the previous one (old one deleted) instead of piling up in the chat |
| `/raid_off` | admins, owners | Disable anti-raid |
| `/add_adm` | admins, owners | Make the target user an admin of this chat |
| `/del_adm` | admins, owners | Remove an admin of this chat (the chat creator cannot be removed) |
| `/add_mod` | admins, owners | Make the target user a moderator of this chat |
| `/del_mod` | admins, owners | Remove a moderator of this chat |
| `/report` | everyone | Report a message. Reply to a message with `/report [reason]` to report that message, or send `/report [reason]` on its own for a general report with no specific message indicated. Every owner (from any chat) and the chat's own admins and moderators get a DM from the bot with the chat, who reported (name/@username/id), who was reported and a link to the message plus the message forwarded (when replying), or a note that no specific message was indicated (when not). The bot replies in the chat with an italic "Report sent" |
| `/rules` | everyone | Show this chat's custom rules (set via the admin panel) |
| `/staff` | everyone | List the admins and moderators of this chat (owners are not shown) |
| `/admin` | admins, owners | Open the **admin panel** in a private chat with the bot (see below) |
| `/help` | everyone | Show the commands available **to that specific user** (regular members see the everyone-commands, moderators also see moderator commands, admins/owners see everything). The message auto-deletes after 1 minute |

The target user can be specified by **replying** to their message, or by passing their numeric **ID** (`/ban 123456789`) or **@username** (`/ban @user spam`). Replying or using a numeric ID is the most reliable. An `@username` is first resolved **live** through Telegram (`getChat`) — which works for channels and public groups but **not for regular users** (the Bot API can't turn a user's @username into an id). For users, the bot falls back to its id↔username/display-name **cache** (filled from observed messages and live lookups; reassignments are tracked, and all values are bound as query parameters so a name can never inject SQL). So `@username` resolves a user only if the bot has seen them; there is a small window where a freshly-reassigned username could still point at the previous owner until the new one is seen — reply or numeric ID avoid this entirely. There is no external/third-party fallback for a cache miss (deliberately — such services are outside the bot's control and unreliable); the command reports that the user couldn't be determined and asks for a reply or numeric ID instead.

**Banning channels:** when someone posts in the group **as a channel**, a normal ban would only hit the anonymous `@Channel_Bot`. Instead, **reply** to the channel's message with `/ban` (or `/gban`, `/sban`, `/sgban`) and the bot bans the *channel sender* itself (and `/unban` … `/unsgban` reverse it). Global channel bans are remembered and re-applied in every chat the bot guards, just like user bans. Channels can't be muted (Telegram has no such action) — the bot tells you to ban instead — and can't be given a staff role.

Command messages are deleted automatically after they are processed (the bot needs the *delete messages* admin right for this).

**Plain members** (no role — not a moderator, admin or owner) get a flat **10-second cooldown between commands** in each group chat, to keep a regular user from flooding the chat with commands like `/report`/`/rules`/`/staff`/`/help`. Commands sent within the cooldown are silently dropped (no reply, so as not to add to the noise). Staff and owners are exempt.

**Message tracking for `/delete_user`/`/delete_chat`:** the bot keeps a rolling 2-day log of `(chat, user, message id, timestamp)` for every group message it sees, and separately `(chat, channel, message id, timestamp)` for messages posted as a channel (channel posts otherwise all share one anonymous `@Channel_Bot` sender, so they're tracked by the real channel id instead) — no message content, just enough to locate and delete them later, auto-expired in the background. This backs the bulk-delete commands — deleting a raider's (or spam channel's, or a whole flood's) messages in bulk — and is capped at 2 days because that's also the limit of how old a message Telegram lets a bot delete.

**Captcha IP/User-Agent tracking (opt-in):** when `COLLECT_CAPTCHA_IPS=1`, the bot remembers the IP address and User-Agent seen the **first** time a user opens their captcha page — the IP of the browser that opened the link, i.e. the user's own. Only that first record is kept: reloading the page, requesting a new captcha later, etc. never overwrites it. Shown only via `/uinfo` and `/iptop` (owners, private chat with the bot only — never in `/punl`, which staff/admins can also reach) as separate "IP:"/"User-Agent:" lines below "First seen:" — each only appears if that particular field was actually captured (e.g. no User-Agent header means no "User-Agent:" line), and both are simply absent if the feature has never recorded anything for that user. `/iptop` is the sweep across the whole table — every IP shared by 2+ distinct users at once — rather than `/uinfo`'s per-target lookup. The IP is a clickable link to `https://ipinfo.io/<ip>` for a quick lookup. This is meant to help spot several Telegram accounts opening the captcha from the same IP (a sign of one operator running multiple accounts), not as an automatic ban signal — shared/mobile IPs (CGNAT) make IP alone unreliable for that. The User-Agent is sanitized before storage (control characters stripped, capped to 300 chars) and HTML-escaped again on display; both fields are always written via parameterized queries, so neither can affect the database regardless of content. Both are stored unencrypted, so treat them as personal data subject to whatever retention/consent rules apply in your jurisdiction. Off by default.

The **global** commands (`/gban`, `/sgban`, `/gmute`, `/gsmute`, `/ungban`, `/unsgban`, `/ungmute`, `/ungsmute`) can also be sent in a **private chat with the bot** by owners and by anyone who is an admin of at least one chat (target by ID or @username). When issued from DM the broadcast to all chats omits the source chat — it just says "globally banned/muted/…" — and the issuer gets a confirmation in DM. Logging from DM goes to each chat the issuer administers (owners aren't logged). Silent variants still post and DM nothing to the chats/target.

Punishment announcements (bans, mutes, unbans, unmutes) are posted in italics and always show the staff member and the target with their numeric id. When a target is given by a bare **ID**, the bot looks the person up across every chat it's in (then its local cache) to show a real display name instead of just the number; the name links to the public profile (`t.me/username`) when the account has one, and falls back to plain `display name (id …)` or a bare `id …` when nothing is known. Mute durations are spelled out (e.g. `5 hours` rather than `5h`). For non-silent punishments the bot also DMs the affected user a rephrased copy of the notice (if they have ever started a chat with the bot); silent variants (`/sban`, `/sgban`, `/smute`, `/gsmute`, `/unsban`, `/unsgban`, `/unsmute`, `/ungsmute`) post and DM nothing.

### Admin panel (`/admin`)

Sent in a **private chat** with the bot, `/admin` opens an inline-button panel. It is available to **admins** (for their own chats) and **owners** (for every chat the bot is in), and only **after the user has passed the captcha** (i.e. is verified). From the panel you can:

- pick a chat (when you manage more than one);
- see its admins and moderators;
- add or remove an admin or a moderator (the bot asks for the target's `@username` or ID);
- view that chat's staff action log — the full history is kept and shown newest-first, paged (with ◀️/▶️ buttons), and searchable (🔍) by any text such as a name, id or action;
- set or clear that chat's custom rules (shown in-chat via `/rules`);
- toggle the captcha for that chat. When the captcha is off, new members can write immediately without solving it — but the blocklist ID check on join still always runs;
- toggle anti-raid for that chat (same effect as `/raid_on` / `/raid_off`);
- toggle the **24-hour kick** for that chat — whether users who don't pass the captcha within 24h are kicked (on by default);
- toggle **deletion of Telegram's system messages** for that chat (off by default) — the native "X joined the group" / "X left the group" service messages, distinct from the bot's own welcome/captcha message;
- configure the **repeated-message antispam** for that chat: on/off (on by default), how many identical messages in a row trigger it (default 3, minimum 2), the time window they must fall within (default 6h, 1 minute–2 days), the mute duration (default 30m, minimum 1 minute), whether to DM staff/owners when it fires (default on), and a separate suspicious-Unicode message filter (default on) — see below for what "identical"/"suspicious" mean and what happens when each triggers;
- toggle **"channels forbidden"** for that chat (off by default). When on, any message posted on behalf of a channel is deleted and that channel is banned, with a "Channels are forbidden in this chat" notice. A linked channel's auto-forwarded posts are left alone;
- manage the **rights granted to users after they pass the captcha** — toggle each permission individually (send messages, send media, stickers/GIFs, polls, link previews, and *edit own tag* — the `can_edit_tag` right from Bot API 9.5, shown only if the installed aiogram supports it). Unmuting a user restores this same set. By default members get the sending rights; "edit own tag" is off until enabled. Admin-type rights (adding members, pinning, changing chat info) are never granted here.
- view the **global ban list** — a paged, searchable list of every blocklisted user and channel (those banned via `/gban` / `/sgban`), same 🔍 search UI as the staff action log (by id, username, or cached display name for users; id or title for channels). Removal is still done with `/ungban`.
- for chats that require **admin approval to join** (Telegram's "Approve new members" setting) — toggle **auto-accept** of join requests. This button only appears once the bot has actually seen a join request from that chat (there is no API to query the setting up front). Off by default: requests then wait for a human admin to approve/decline as usual, exactly like today. When on, the bot approves every request itself and the user goes through the normal captcha flow (mute, minigame, code) just like a regular join. Either way, a request from a **blocklisted** user is always declined and the user is banned in that chat, regardless of the toggle.

Owners additionally get owner-only controls in the panel: stopping/resuming a chat (the bot ignores all commands from it), a **global command ban** — a denylist of users who are completely forbidden from using any bot command (except `/start`) and the admin panel everywhere, even if they are admins or moderators (owners can't be added) — and a **Leave chat** button (with a confirmation) that makes the bot leave that chat. Owners can also make the bot leave from inside a group with the hidden `/leavechat` command.

The staff action log keeps the **full history per chat** (bans, unbans, mutes, role changes, etc.) — it is no longer capped. In the panel it is paginated and searchable, and each entry is timestamped with the full date including the year. Owner actions are logged too, but the log only ever records an actor's name and id (never a role), so a bot owner appears there indistinguishable from a regular administrator.

### Blocklist

There is no `BLOCKLIST` env variable anymore. The blocklist lives in the database and is populated by `/gban` and `/sgban`. Anyone on the blocklist is banned automatically when they try to join any chat the bot guards. The bot enforces the blocklist on a new chat **automatically**: when it is added to a chat, and again when it is promoted to administrator (the point at which it first gains ban rights), it silently sweeps the whole blocklist over that chat — a preemptive ban works by user id even for members the Bot API can't enumerate, so blocklisted users who were already in the chat are banned without waiting for them to speak. The same sweep also runs the first time the bot sees a chat it was already in before this version was deployed (revealed by the first message there).

As a safety net, a blocklisted user who somehow slips through is still banned as soon as they are next active (send a message), provided the bot is an admin there and privacy mode is off.

The same kind of safety net applies to mutes: if a muted user's Telegram-level restriction is ever lifted outside the bot (an admin manually unmuting them from Telegram's own UI, a failed `restrict_chat_member` call, etc.) while the bot's own mute record (local or global) hasn't expired yet, the next message they send is deleted and their mute is silently re-applied up to the originally recorded expiry — the bot's mute record is the source of truth, not whatever Telegram's restriction currently says.

`/ban`/`/sban` (**local** ban) get the same treatment: the bot records the ban in a `local_bans` table when it's issued, and if the user is ever manually unbanned in Telegram directly (bypassing `/unban`/`/unsban`) and somehow still manages to send a message, it's deleted and they're banned again on the spot. `/unban`/`/unsban` clear that record (as does a global unban, everywhere at once); a plain global ban (`/gban`/`/sgban`) doesn't need this — it's already covered by the existing blocklist safety net described above.

**Repeated-message antispam** (`ANTISPAM_ENABLED` in `.env`, on/off and thresholds per chat via `/admin` —
see above): watches every plain member's (never staff's or an owner's) messages for **strictly consecutive**
duplicates — any other message from them in between resets the count. "Identical" means literally the same
text, or the same underlying file for a sticker/GIF/photo/video/video note/voice message/document/audio
(compared by file id, not by re-uploading/hashing); a caption counts too, so the same photo with a different
caption isn't treated as a repeat. Once the configured count is reached within the configured window, the
bot deletes every one of those messages except the last, mutes the user for the configured duration, and
posts an italic announcement naming them in the chat. If "notify staff" is on for that chat, every owner and
that chat's own admins/moderators also get a DM (same recipients/mechanism as `/report`) with the details
and the last message forwarded to them.

A message posted **as a channel** is tracked and punished separately, by the channel's own id, not lumped in
with regular users — channels can flood a chat via "Send As" without being an admin of the group, and every
channel post's `from_user` is the same generic `@Channel_Bot` no matter which channel posted it, so tracking
by user id would both miss this case and wrongly merge different channels' streaks together. Since a channel
can't be muted (no such action exists), a channel that trips the threshold is **banned** instead, with its
own announcement wording. An **anonymous group admin** posting (`sender_chat` set, but not a channel) is
exempt like any other staff action, and a linked channel's own auto-forwarded post into the discussion group
is never treated as spam.

A separate per-chat toggle under the same "🚨 Антиспам" menu (on by default, also gated by `ANTISPAM_ENABLED`)
silently **deletes** messages (text or caption) containing suspicious Unicode: invisible/zero-width
characters, bidi direction-override/embedding/isolate control characters (used to visually spoof text —
e.g. making a malicious link or filename display as something else), or zalgo (an abnormal stack of
combining diacritical marks on one letter). This is deletion only — no mute, no chat announcement, no staff
DM — just a staff action-log entry (chat's log in `/admin`) naming who it deleted the message from, and
it doesn't count toward or interact with the repeat-message threshold above beyond breaking an
in-progress streak (the message never survives to be compared). It's deliberately narrow: ordinary text in
any language, including normal accented/diacritic use (Arabic, Hebrew, Vietnamese, etc.) and "fancy font"
Unicode blocks (bold/gothic/etc. styled text), is never flagged.

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
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}

location /api/captcha/ {
    proxy_pass http://127.0.0.1:8080/api/captcha/;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
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

location /altcha/ {
    proxy_pass http://127.0.0.1:8080/altcha/;
}
```

> **Note:** set `WEB_HOST=0.0.0.0` in `.env` — inside Docker the container's loopback is not reachable from the host.

> **Real client IP:** the bot resolves the visitor's IP (used by the rate limiter, Cloudflare Turnstile's `remoteip`, and the optional captcha-IP tracking below) from the `X-Real-IP`/`X-Forwarded-For` headers set above — never from the raw TCP connection, which behind nginx (and especially through Docker's published-port NAT) is only nginx's/Docker's own address. If nginx itself sits behind Cloudflare, first restore the real visitor IP with nginx's `real_ip` module (`real_ip_header CF-Connecting-IP;` + `set_real_ip_from <Cloudflare ranges>;` at the `http` level) so `$remote_addr` above is already correct — otherwise you'd just be forwarding Cloudflare's edge IP instead. This behavior is controlled by `TRUST_PROXY_HEADERS` (on by default).

## Running without Docker

```bash
pip install -r requirements.txt
cp .env.example .env  # edit .env
python run.py
```

## Adding the bot to a group

1. Create a bot via [@BotFather](https://t.me/BotFather), get the token, set it as `TOKEN` in `.env`.
2. Add the bot to your group and grant it **administrator** rights (restrict members, delete messages, ban members). If the group approves new members by request, also grant **add new members** (`can_invite_users`) — required for the bot to approve/decline join requests, needed by the auto-accept option above.
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
├── moderation_handler/       # roles, bans/mutes, anti-raid, deletion, admin panel (package, split by command family)
├── permissions.py            # role/permission helpers (owner, admin, moderator)
├── dbmanager/                # SQLite: verified users, pending chats, roles, ... (package, one DBManager class via mixins)
├── translator.py             # locale string loader
├── captcha.html              # DOOM minigame page (served under /doom/)
├── tetris_captcha.html       # Tetris minigame page (served under /tetris/)
├── tetris_captcha.js         # Tetris game logic
├── mario_captcha.html        # Mario minigame page (served under /mario/)
├── FullScreenMario.min.js    # FullScreenMario engine (served under /mario/)
├── altcha.js, altcha.css     # vendored Altcha widget (self-hosted, served under /altcha/)
├── workers/pbkdf2.js         # vendored Altcha PoW worker (served under /altcha/workers/)
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
