# The Watcher — BloxBerry Labs

## Render deployment
1. Upload this folder to a private GitHub repository.
2. In Render, create a **Background Worker** from that repository.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python bot.py`
5. Add the environment variable `DISCORD_TOKEN` with your newly reset Discord bot token.
6. Enable **Server Members Intent** and **Message Content Intent** in the Discord Developer Portal.
7. Invite the bot with View Channels, Read Message History, Send Messages, Embed Links and View Audit Log permissions.

## Commands
- `watcherhelp`
- `watcher @user`
- `watcher leaderboard`
- `watcher staff`
- `watcher inactive`
- `watcher history @user`
- `watcher compare @user @user`
- `watcher security`
- `watcher alerts`
- `watcher report`

Only members with authorized role ID `1567599630030610502` can use commands.

## Bump reminder
The bot posts a reminder in channel `1567186504839860234` roughly every 2 hours plus a random 1–2 minutes.
It does **not** execute DISBOARD's `/bump` command. A human with permission must run that slash command.

## Data
Activity data is saved to `watcher_activity.json`. Render Background Workers have ephemeral local storage by default, so data can be lost when the service is redeployed/restarted. Use a persistent disk or external database if you need durable history across deployments.

## Security
Never commit your Discord token to GitHub. Use Render's `DISCORD_TOKEN` environment variable. Since a token was pasted into chat, reset it before deploying.
