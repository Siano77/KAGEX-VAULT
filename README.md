# KAGEX VAULT Telegram Bot

A deployable Telegram bot for file/tool rewards paid for with points. The included interface uses Telegram reply and inline buttons; its layout follows the supplied reference image and button labels/main replies use Unicode small-cap lettering. Telegram controls button colors and animations, so exact visual styling and animated button effects are not available through the Bot API. You can assign a photo or GIF to the bot's main views with `/setimage`.

## Included

- Referral links (`?start=ref_USER_ID`); a referred, previously unseen Telegram account earns the inviter **4 points** by default after it starts the bot and passes any configured force-join check. Change `REFERRAL_POINTS` to alter the award. Repeat starts and self-referrals do not count.
- A points balance shared by referral awards, redeem codes, and admin adjustments.
- Admin-uploaded document, photo, video, animation, or audio rewards, each with its own title and point cost. A successful claim debits points once and delivers the stored Telegram file.
- Plain-text rewards that arrive as ordinary Telegram text messages, plus file rewards such as `.txt`, `.zip`, and other Telegram-supported document formats.
- Unique, case-insensitive codes with a points amount, optional expiry in hours, optional total-use cap, and one redemption per user. A code adds to a user's existing points.
- Force-join targets for channels or groups, with an in-bot join/check flow.
- Per-view image/GIF customization for start, profile, tools, referrals, and help.
- SQLite transactions for atomic code redemption, referral awards, and reward claims.

## 1. Create and configure the Telegram bot

1. In Telegram, open **@BotFather**, run `/newbot`, and copy the token it returns. Do not share the token or commit it to Git.
2. Obtain your numeric Telegram user ID (for example, from a trusted user-ID lookup bot).
3. Copy `.env.example` to `.env`, then set `BOT_TOKEN` and `ADMIN_IDS`. If there are multiple admins, separate numeric IDs with commas.
4. Install and run locally:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env  # edit .env before running
   python bot.py
   ```

   The bot uses long polling; do not run multiple bot instances with the same token at once. Its SQLite database is created at `DATABASE_PATH` automatically.

## 2. Admin commands

Send `/admin` to the bot as an account listed in `ADMIN_IDS`.

| Action | How to do it |
|---|---|
| Add a reward | Send a file with caption `/additem POINTS Title`, e.g. `/additem 12 Starter pack`. The caption is the title and the number is the cost. |
| Add a plain-text reward | Send `/addtext POINTS Title`, then send the text content in your next message. The claimed reward is delivered as normal message text, not a document. |
| List rewards | `/items` |
| Remove a reward | `/delitem ITEM_ID` (disables it without deleting its record) |
| Create a redeem code | `/createcode CODE POINTS EXPIRY_HOURS MAX_USES`, e.g. `/createcode WELCOME25 25 72 50`. Use `0` for no expiry or unlimited total uses. |
| Add force-join target | `/addchannel @publicchannel https://t.me/publicchannel` or `/addchannel -1001234567890 https://t.me/+invitehash` |
| List/remove force-join targets | `/channels`; `/removechannel CHAT_REF` |
| Adjust a user's points | `/setpoints USER_ID SIGNED_DELTA`, e.g. `/setpoints 123456789 10` or `/setpoints 123456789 -3` |
| Set a view image or GIF | `/setimage profile`, then send a photo or animation as a separate message. Supported views: `start`, `profile`, `tools`, `referrals`, `help`. `/cancel` cancels the pending upload. |

For force-join verification, add the bot as an administrator in each channel/group and grant the necessary membership visibility. Telegram must let the bot call `getChatMember` for users; if this is misconfigured, the bot deliberately blocks access rather than silently bypassing the requirement. For private groups/channels, use their numeric chat ID and a working invite link. Test each target with an ordinary non-admin account.

## 3. Deploy on Railway

The project already includes a Dockerfile that installs the requirements and starts `python bot.py`. Railway can deploy a GitHub repository containing a Dockerfile as a long-running service. This bot uses Telegram long polling, so it does not need a public web domain or an HTTP port.

1. Extract the project ZIP and upload the project files to a **private GitHub repository**. Keep `bot.py`, `requirements.txt`, and `Dockerfile` in the repository root. Do not upload a real `.env` file or commit a Telegram token. The `.env.example` file contains placeholders only; do not import its example token into Railway.
2. In Railway, create a project from the GitHub repository and add these service variables: `BOT_TOKEN` (your @BotFather token), `ADMIN_IDS` (your numeric Telegram user ID), `DATABASE_PATH=/data/kagex_vault.sqlite3`, and `REFERRAL_POINTS=4`. You may also set `SUPPORT_URL` and `LOG_LEVEL=INFO`. Railway's Variables tab passes these values to the running service as environment variables.
3. Attach a persistent Volume to the bot service and set its mount path to `/data`. The `DATABASE_PATH` above must be inside that mount, otherwise the SQLite database may be lost when the container is replaced. Railway volumes are billed separately according to their current storage pricing; check the current plan and price before creating one.
4. Deploy the service. Railway should build from the Dockerfile and use its `CMD` (`python bot.py`). If you set a start command manually, use `python bot.py`. Watch the deployment logs for `KAGEX VAULT bot starting`, then open the bot in Telegram and send `/start`.
5. Keep one active service instance for this bot token. Back up the SQLite database from the mounted volume periodically.

Official references: [Railway quick start](https://docs.railway.com/quick-start), [variables](https://docs.railway.com/variables), [using volumes](https://docs.railway.com/volumes), and [volume reference](https://docs.railway.com/volumes/reference).

## 4. Deploy on Render (included Blueprint)

This repository includes `render.yaml` and a Dockerfile. The Blueprint creates a **single background worker** and mounts a persistent disk at `/var/data`; the database file is stored on that disk. A persistent disk is important: without durable storage, points, uploaded reward metadata, codes, and force-join settings can be lost on a redeploy or restart.

1. Put the project in a private GitHub repository and connect it to Render, or create a Render Blueprint from the repository.
2. Review the service configuration in `render.yaml`. The worker uses the `starter` plan and a 1 GB persistent disk; check Render's current pricing and available regions before creating it.
3. In the Render service environment, set `BOT_TOKEN` and `ADMIN_IDS` as secrets/values. Do not set the token in the YAML or source files.
4. Deploy and check the worker logs for `KAGEX VAULT bot starting`. Open the bot in Telegram and send `/start`.

Because Telegram long polling is a continuously running process, deploy it as a worker/always-on VM—not as a short-lived serverless function. If selecting a different host, ensure its filesystem/database is persistent and run exactly one worker for this bot token.

## 5. Replit or another host

Use a long-running/Reserved VM-style deployment rather than a request-scaled web deployment. Set the same secrets (`BOT_TOKEN`, `ADMIN_IDS`) and environment variables, install `requirements.txt`, and set the run command to `python bot.py`. Set `DATABASE_PATH` to a location on persistent storage. Confirm that the selected Replit deployment plan retains its filesystem across restart/redeploy; otherwise configure a persistent database/storage service before using the bot with real users.

## Environment variables

| Name | Default | Purpose |
|---|---|---|
| `BOT_TOKEN` | required | BotFather token; keep private |
| `ADMIN_IDS` | empty | Comma-separated numeric Telegram IDs with admin access |
| `DATABASE_PATH` | `data/kagex_vault.sqlite3` | SQLite file path; should be on durable storage in production |
| `REFERRAL_POINTS` | `4` | Points per eligible referral |
| `SUPPORT_URL` | empty | Optional support link shown in the inline menu |
| `LOG_LEVEL` | `INFO` | Python log level |

## Operational notes and limits

- The bot stores Telegram `file_id` values rather than copying files to the server. Telegram then delivers that media when a user claims it. Documents are accepted in their normal formats (including `.txt` and `.zip`); plain-text rewards use `/addtext`.
- Referral validation is bot-start based, not a proof of real-world identity. It prevents duplicate Telegram user IDs and self-referrals but cannot prevent users creating alternate accounts.
- SQLite is appropriate for a modest single-worker bot. Do not launch multiple replicas against the same SQLite database. For larger scale, migrate the database layer to PostgreSQL and use one webhook/polling strategy with appropriate concurrency controls.
- Back up the persistent database periodically. Admin access is controlled by numeric IDs, so configure `ADMIN_IDS` carefully.
- No bot token or deployment credentials are included in this project. You must add those in your host's secret/environment settings.
