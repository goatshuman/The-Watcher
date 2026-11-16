
import os
import json
import random
from datetime import datetime, timedelta, timezone

import discord
from aiohttp import web
from discord.ext import commands, tasks

# =========================================================
# THE WATCHER - BLOXBERRY LABS
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

SERVER_ID = 1108961244880314508
AUTHORIZED_ROLE_ID = 1567599630030610502
MEMBER_ROLE_ID = 1568182409957081108
APPLICANT_ROLE_ID = 1571120110830100652

BUMP_CHANNEL_ID = 1567186504839860234
DATA_FILE = "watcher_activity.json"

EMBED_COLOR = 0x2B2D31
INACTIVE_DAYS = 7
TYPING_ACTIVE_SECONDS = 300
MAX_EVENTS = 5000

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True
intents.typing = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

DEFAULT_DATA = {
    "users": {},
    "daily": {},
    "events": [],
    "alerts": [],
    "leaderboards": [],
    "next_bump_reminder": None
}


def utcnow():
    return datetime.now(timezone.utc)


def iso_now():
    return utcnow().isoformat()


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def load_data():
    if not os.path.exists(DATA_FILE):
        return DEFAULT_DATA.copy()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for key, value in DEFAULT_DATA.items():
            saved.setdefault(key, value)
        return saved
    except (OSError, json.JSONDecodeError):
        print("Could not read activity data; starting with a fresh data file.")
        return DEFAULT_DATA.copy()


data = load_data()


def save_data():
    try:
        temp = DATA_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(temp, DATA_FILE)
    except OSError as exc:
        print(f"Data save error: {exc}")


def user_data(user_id):
    key = str(user_id)
    if key not in data["users"]:
        data["users"][key] = {
            "messages": 0,
            "typing_events": 0,
            "last_activity": None,
            "last_message": None,
            "last_typing": None,
            "channels": {}
        }
    return data["users"][key]


def daily_data(user_id):
    day = utcnow().strftime("%Y-%m-%d")
    data["daily"].setdefault(day, {})
    data["daily"][day].setdefault(str(user_id), {"messages": 0, "typing": 0})
    return data["daily"][day][str(user_id)]


def add_event(user_id, kind, description):
    data["events"].append({
        "user_id": str(user_id),
        "type": kind,
        "description": description,
        "time": iso_now()
    })
    data["events"] = data["events"][-MAX_EVENTS:]


def add_alert(description, user_id=None):
    data["alerts"].append({
        "description": description,
        "user_id": str(user_id) if user_id else None,
        "time": iso_now()
    })
    data["alerts"] = data["alerts"][-MAX_EVENTS:]
    print(f"[WATCHER ALERT] {description}")
    save_data()


def embed(title, description=None):
    e = discord.Embed(
        title=title,
        description=description,
        color=EMBED_COLOR,
        timestamp=utcnow()
    )
    e.set_footer(text="The Watcher | BloxBerry Labs")
    return e


def relative_time(value):
    parsed = parse_time(value)
    return discord.utils.format_dt(parsed, style="R") if parsed else "No recorded activity"


def is_authorized(member):
    return isinstance(member, discord.Member) and any(
        role.id == AUTHORIZED_ROLE_ID for role in member.roles
    )


def is_staff(member):
    excluded = {MEMBER_ROLE_ID, APPLICANT_ROLE_ID}
    return (
        not member.bot
        and any(
            role.id not in excluded and role != member.guild.default_role
            for role in member.roles
        )
    )


def staff_members(guild):
    return [m for m in guild.members if is_staff(m)]


def activity_score(user_id):
    u = user_data(user_id)
    return u["messages"] + u["typing_events"]


async def resolve_member(guild, raw):
    raw = raw.strip().replace("<@", "").replace("!", "").replace(">", "")
    if not raw.isdigit():
        return None
    member = guild.get_member(int(raw))
    if member:
        return member
    try:
        return await guild.fetch_member(int(raw))
    except (discord.NotFound, discord.HTTPException):
        return None


async def get_target(message, token_index=1):
    if message.mentions:
        return message.mentions[0]
    parts = message.content.split()
    if len(parts) > token_index:
        return await resolve_member(message.guild, parts[token_index])
    return None


async def send_embed(message, e):
    await message.reply(embed=e, mention_author=False)


# =========================================================
# ACTIVITY TRACKING
# =========================================================

def track_message(member, channel):
    u = user_data(member.id)
    u["messages"] += 1
    u["last_activity"] = iso_now()
    u["last_message"] = iso_now()
    cid = str(channel.id)
    u["channels"][cid] = u["channels"].get(cid, 0) + 1
    daily_data(member.id)["messages"] += 1
    add_event(member.id, "message", f"Message activity in #{getattr(channel, 'name', 'unknown')}")
    save_data()


def track_typing(member, channel):
    u = user_data(member.id)
    u["typing_events"] += 1
    u["last_activity"] = iso_now()
    u["last_typing"] = iso_now()
    daily_data(member.id)["typing"] += 1
    add_event(member.id, "typing", f"Typing activity in #{getattr(channel, 'name', 'unknown')}")
    save_data()


# =========================================================
# LIVE LEADERBOARD
# =========================================================

def make_leaderboard(guild):
    staff = staff_members(guild)
    staff.sort(key=lambda m: activity_score(m.id), reverse=True)

    e = embed(
        "The Watcher | Staff Leaderboard",
        "Live staff activity rankings. This message refreshes every 60 seconds."
    )

    if not staff:
        e.description = "No staff members found."
        return e

    rows = []

    for rank, member in enumerate(staff[:25], 1):
        u = user_data(member.id)

        rows.append(
            f"**{rank}. {discord.utils.escape_markdown(member.display_name)}**\n"
            f"{member.mention} · Messages: `{u['messages']:,}` · "
            f"Typing: `{u['typing_events']:,}` · Score: `{activity_score(member.id):,}`"
        )

    e.description = "\n\n".join(rows)
    e.add_field(name="Staff tracked", value=str(len(staff)), inline=True)
    e.add_field(name="Excluded roles", value="Member and Pending Application", inline=True)

    return e


async def refresh_leaderboards():
    changed = False

    for item in data["leaderboards"][:]:
        guild = bot.get_guild(item["guild_id"])
        channel = guild.get_channel(item["channel_id"]) if guild else None

        if not channel:
            data["leaderboards"].remove(item)
            changed = True
            continue

        try:
            msg = await channel.fetch_message(item["message_id"])
            await msg.edit(embed=make_leaderboard(guild))

        except discord.NotFound:
            data["leaderboards"].remove(item)
            changed = True

        except discord.HTTPException as exc:
            print(f"Leaderboard refresh error: {exc}")

    if changed:
        save_data()


@tasks.loop(seconds=60)
async def leaderboard_loop():
    await refresh_leaderboards()


# =========================================================
# DISBOARD BUMP REMINDER
# =========================================================

def schedule_next_bump():
    delay_seconds = 2 * 60 * 60 + random.randint(60, 120)

    data["next_bump_reminder"] = (
        utcnow() + timedelta(seconds=delay_seconds)
    ).isoformat()

    save_data()


@tasks.loop(seconds=30)
async def bump_reminder_loop():
    due = parse_time(data.get("next_bump_reminder"))

    if due is None:
        schedule_next_bump()
        return

    if utcnow() < due:
        return

    channel = bot.get_channel(BUMP_CHANNEL_ID)

    if channel is None:
        try:
            channel = await bot.fetch_channel(BUMP_CHANNEL_ID)
        except discord.HTTPException:
            print("Could not access the configured DISBOARD bump channel.")
            schedule_next_bump()
            return

    try:
        await channel.send(
            "⏰ **DISBOARD bump reminder**\n"
            "It's time to run `/bump` in this channel."
        )
    except discord.HTTPException as exc:
        print(f"Could not send bump reminder: {exc}")

    schedule_next_bump()


# =========================================================
# WATCHER HELP
# =========================================================

async def cmd_help(message):
    e = embed(
        "The Watcher | Command Centre",
        "Commands available to authorized staff."
    )

    commands_list = [
        ("watcherhelp", "Show all Watcher commands."),
        ("watcher @user", "Show a member's activity report."),
        ("watcher leaderboard", "Post a live staff leaderboard."),
        ("watcher staff", "Show overall staff activity."),
        ("watcher inactive", "List staff with no recorded activity for 7+ days."),
        ("watcher history @user", "Show the member's latest recorded activity events."),
        ("watcher compare @user @user", "Compare two members' activity."),
        ("watcher security", "Show the security monitoring overview."),
        ("watcher alerts", "Show recent security alerts."),
        ("watcher report", "Generate a rolling 7-day activity report.")
    ]

    for name, desc in commands_list:
        e.add_field(name=name, value=desc, inline=False)

    e.add_field(
        name="Access",
        value="Only members with the authorized Watcher role can use commands.",
        inline=False
    )

    await send_embed(message, e)


# =========================================================
# WATCHER USER REPORT
# =========================================================

async def cmd_user(message):
    target = await get_target(message)

    if not target:
        return await message.reply(
            "Mention a member. Example: `watcher @user`",
            mention_author=False
        )

    u = user_data(target.id)

    e = embed(
        f"The Watcher | {target.display_name}",
        f"Activity report for {target.mention}"
    )

    e.set_thumbnail(url=target.display_avatar.url)

    e.add_field(name="Messages", value=f"`{u['messages']:,}`", inline=True)
    e.add_field(name="Typing events", value=f"`{u['typing_events']:,}`", inline=True)
    e.add_field(name="Activity score", value=f"`{activity_score(target.id):,}`", inline=True)

    e.add_field(name="Last activity", value=relative_time(u["last_activity"]), inline=False)
    e.add_field(name="Last message", value=relative_time(u["last_message"]), inline=True)
    e.add_field(name="Last typing", value=relative_time(u["last_typing"]), inline=True)

    e.add_field(
        name="Staff member",
        value="Yes" if is_staff(target) else "No",
        inline=True
    )

    last_typing = parse_time(u["last_typing"])

    typing_now = bool(
        last_typing
        and (utcnow() - last_typing).total_seconds() <= TYPING_ACTIVE_SECONDS
    )

    e.add_field(
        name="Typing status",
        value="Recently typing" if typing_now else "Not recently typing",
        inline=True
    )

    await send_embed(message, e)


# =========================================================
# WATCHER LEADERBOARD COMMAND
# =========================================================

async def cmd_leaderboard(message):
    sent = await message.channel.send(embed=make_leaderboard(message.guild))

    data["leaderboards"] = [
        x for x in data["leaderboards"]
        if x["message_id"] != sent.id
    ]

    data["leaderboards"].append({
        "guild_id": message.guild.id,
        "channel_id": message.channel.id,
        "message_id": sent.id
    })

    save_data()


# =========================================================
# WATCHER STAFF OVERVIEW
# =========================================================

async def cmd_staff(message):
    staff = staff_members(message.guild)

    total_messages = sum(user_data(m.id)["messages"] for m in staff)
    total_typing = sum(user_data(m.id)["typing_events"] for m in staff)

    active_typing = 0

    for m in staff:
        last = parse_time(user_data(m.id)["last_typing"])

        if last and (utcnow() - last).total_seconds() <= TYPING_ACTIVE_SECONDS:
            active_typing += 1

    e = embed(
        "The Watcher | Staff Overview",
        "Activity overview of tracked staff."
    )

    e.add_field(name="Total staff", value=str(len(staff)), inline=True)
    e.add_field(name="Recently typing", value=str(active_typing), inline=True)
    e.add_field(name="Total messages", value=f"{total_messages:,}", inline=True)
    e.add_field(name="Typing events", value=f"{total_typing:,}", inline=True)

    await send_embed(message, e)


# =========================================================
# WATCHER INACTIVE STAFF
# =========================================================

async def cmd_inactive(message):
    inactive = []

    for member in staff_members(message.guild):
        last = parse_time(user_data(member.id)["last_activity"])

        if last is None:
            inactive.append((member, None))
        else:
            days = (utcnow() - last).days

            if days >= INACTIVE_DAYS:
                inactive.append((member, days))

    inactive.sort(
        key=lambda item: item[1] if item[1] is not None else 999999,
        reverse=True
    )

    e = embed(
        "The Watcher | Inactive Staff",
        f"Staff with no recorded activity for {INACTIVE_DAYS}+ days."
    )

    if not inactive:
        e.description = "No inactive staff members found."
    else:
        e.description = "\n".join(
            f"{m.mention} — `{'No recorded activity' if days is None else str(days) + ' days inactive'}`"
            for m, days in inactive[:25]
        )

    await send_embed(message, e)


# =========================================================
# WATCHER MEMBER HISTORY
# =========================================================

async def cmd_history(message):
    target = await get_target(message, 2)

    if not target:
        return await message.reply(
            "Use `watcher history @user`.",
            mention_author=False
        )

    events = [
        x for x in data["events"]
        if x["user_id"] == str(target.id)
    ][-15:]

    e = embed(
        f"The Watcher | History: {target.display_name}",
        "Latest recorded events."
    )

    if not events:
        e.description = "No activity history recorded."
    else:
        e.description = "\n\n".join(
            f"**{x['type'].replace('_', ' ').title()}** — {x['description']}\n"
            f"{relative_time(x['time'])}"
            for x in reversed(events)
        )

    await send_embed(message, e)


# =========================================================
# WATCHER MEMBER COMPARISON
# =========================================================

async def cmd_compare(message):
    if len(message.mentions) < 2:
        return await message.reply(
            "Use `watcher compare @user1 @user2`.",
            mention_author=False
        )

    first, second = message.mentions[:2]

    e = embed(
        "The Watcher | Member Comparison",
        f"{first.mention} vs {second.mention}"
    )

    for member in (first, second):
        u = user_data(member.id)

        e.add_field(
            name=member.display_name,
            value=(
                f"Messages: `{u['messages']:,}`\n"
                f"Typing: `{u['typing_events']:,}`\n"
                f"Score: `{activity_score(member.id):,}`"
            ),
            inline=True
        )

    await send_embed(message, e)


# =========================================================
# WATCHER SECURITY OVERVIEW
# =========================================================

async def cmd_security(message):
    cutoff = utcnow() - timedelta(days=7)

    recent = [
        x for x in data["alerts"]
        if parse_time(x["time"]) and parse_time(x["time"]) >= cutoff
    ]

    e = embed(
        "The Watcher | Security Overview",
        "Recorded server events from the last 7 days."
    )

    e.add_field(name="Recent alerts", value=str(len(recent)), inline=True)
    e.add_field(
        name="Server members",
        value=str(message.guild.member_count or len(message.guild.members)),
        inline=True
    )
    e.add_field(
        name="Tracked staff",
        value=str(len(staff_members(message.guild))),
        inline=True
    )

    e.add_field(
        name="Monitored events",
        value=(
            "Role creation/deletion and permission changes\n"
            "Member role and nickname changes\n"
            "Channel creation/deletion, renames and permission changes\n"
            "Webhook configuration changes\n"
            "Message deletions and member joins/leaves"
        ),
        inline=False
    )

    await send_embed(message, e)


# =========================================================
# WATCHER SECURITY ALERTS
# =========================================================

async def cmd_alerts(message):
    alerts = data["alerts"][-15:]

    e = embed(
        "The Watcher | Security Alerts",
        "Most recent recorded alerts."
    )

    if not alerts:
        e.description = "No security alerts recorded."
    else:
        e.description = "\n\n".join(
            f"**{x['description']}**\n{relative_time(x['time'])}"
            for x in reversed(alerts)
        )

    await send_embed(message, e)


# =========================================================
# WATCHER WEEKLY REPORT
# =========================================================

async def cmd_report(message):
    cutoff = utcnow() - timedelta(days=7)
    staff = staff_members(message.guild)
    active = 0

    for m in staff:
        last = parse_time(user_data(m.id)["last_activity"])

        if last and last >= cutoff:
            active += 1

    messages = 0
    typing = 0

    for day, members in data["daily"].items():
        try:
            date = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if date >= cutoff:
            for stats in members.values():
                messages += stats.get("messages", 0)
                typing += stats.get("typing", 0)

    alerts = [
        x for x in data["alerts"]
        if parse_time(x["time"]) and parse_time(x["time"]) >= cutoff
    ]

    e = embed(
        "The Watcher | Weekly Report",
        "Rolling 7-day staff activity report."
    )

    e.add_field(name="Total staff", value=str(len(staff)), inline=True)
    e.add_field(name="Active staff", value=str(active), inline=True)
    e.add_field(name="No activity", value=str(len(staff) - active), inline=True)
    e.add_field(name="Messages", value=f"{messages:,}", inline=True)
    e.add_field(name="Typing events", value=f"{typing:,}", inline=True)
    e.add_field(name="Security alerts", value=str(len(alerts)), inline=True)

    await send_embed(message, e)


# =========================================================
# COMMAND ROUTER
# =========================================================

async def handle_watcher_command(message):
    content = message.content.strip()
    lowered = content.lower()

    if lowered == "watcherhelp":
        await cmd_help(message)
        return

    parts = content.split()
    sub = parts[1].lower() if len(parts) > 1 else ""

    if sub == "leaderboard":
        await cmd_leaderboard(message)
    elif sub == "staff":
        await cmd_staff(message)
    elif sub == "inactive":
        await cmd_inactive(message)
    elif sub == "history":
        await cmd_history(message)
    elif sub == "compare":
        await cmd_compare(message)
    elif sub == "security":
        await cmd_security(message)
    elif sub == "alerts":
        await cmd_alerts(message)
    elif sub == "report":
        await cmd_report(message)
    elif sub == "help":
        await cmd_help(message)
    elif message.mentions or (len(parts) > 1 and parts[1].isdigit()):
        await cmd_user(message)
    else:
        await message.reply(
            "Unknown command. Use `watcherhelp`.",
            mention_author=False
        )


# =========================================================
# KEEPALIVE WEB SERVER
# =========================================================

web_runner = None


async def keepalive(request):
    return web.Response(text="anshuman hi")


async def start_webserver():
    global web_runner

    if web_runner is not None:
        return

    app = web.Application()
    app.router.add_get("/", keepalive)
    app.router.add_get("/health", keepalive)

    web_runner = web.AppRunner(app)
    await web_runner.setup()

    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(web_runner, "0.0.0.0", port)

    await site.start()

    print(f"Keepalive server running on port {port}")


# =========================================================
# BOT READY
# =========================================================

@bot.event
async def on_ready():
    await start_webserver()

    print("=" * 45)
    print("THE WATCHER IS ONLINE")
    print(f"Logged in as: {bot.user}")
    print(f"Server ID: {SERVER_ID}")
    print(f"Authorized role: {AUTHORIZED_ROLE_ID}")
    print("=" * 45)

    guild = bot.get_guild(SERVER_ID)

    if guild:
        print(f"Connected to: {guild.name}")
        print(f"Cached members: {len(guild.members)}")

    if data.get("next_bump_reminder") is None:
        schedule_next_bump()

    if not leaderboard_loop.is_running():
        leaderboard_loop.start()

    if not bump_reminder_loop.is_running():
        bump_reminder_loop.start()


# =========================================================
# MESSAGE EVENTS
# =========================================================

@bot.event
async def on_message(message):
    if not message.guild or message.guild.id != SERVER_ID or message.author.bot:
        return

    content = message.content.strip()
    lowered = content.lower()

    is_command = (
        lowered == "watcherhelp"
        or lowered == "watcher"
        or lowered.startswith("watcher ")
    )

    if is_command:
        if not is_authorized(message.author):
            await message.reply(
                "You don't have permission to use The Watcher.",
                mention_author=False
            )
            return

        await handle_watcher_command(message)
        return

    track_message(message.author, message.channel)


# =========================================================
# TYPING EVENTS
# =========================================================

@bot.event
async def on_typing(channel, user, when):
    guild = getattr(channel, "guild", None)

    if not guild or guild.id != SERVER_ID or user.bot:
        return

    if isinstance(user, discord.Member):
        track_typing(user, channel)


# =========================================================
# MEMBER UPDATES
# =========================================================

@bot.event
async def on_member_update(before, after):
    if after.guild.id != SERVER_ID:
        return

    old = {r.id for r in before.roles}
    new = {r.id for r in after.roles}

    added = new - old
    removed = old - new

    if added or removed:
        added_names = [r.name for r in after.roles if r.id in added]
        removed_names = [r.name for r in before.roles if r.id in removed]

        description = (
            f"Role changes for {after}: "
            f"added {added_names}; removed {removed_names}"
        )

        add_event(after.id, "role_change", description)
        add_alert(description, after.id)

    if before.nick != after.nick:
        description = f"Nickname changed for {after}: {before.nick} -> {after.nick}"
        add_event(after.id, "nickname_change", description)
        add_alert(description, after.id)


@bot.event
async def on_member_join(member):
    if member.guild.id == SERVER_ID:
        add_event(member.id, "join", f"{member} joined the server")
        add_alert(f"Member joined: {member}", member.id)


@bot.event
async def on_member_remove(member):
    if member.guild.id == SERVER_ID:
        add_event(member.id, "leave", f"{member} left the server")
        add_alert(f"Member left: {member}", member.id)


# =========================================================
# ROLE MONITORING
# =========================================================

@bot.event
async def on_guild_role_create(role):
    if role.guild.id == SERVER_ID:
        add_alert(f"New role created: {role.name}")


@bot.event
async def on_guild_role_delete(role):
    if role.guild.id == SERVER_ID:
        add_alert(f"Role deleted: {role.name}")


@bot.event
async def on_guild_role_update(before, after):
    if after.guild.id == SERVER_ID and before.permissions != after.permissions:
        add_alert(f"Permissions changed for role: {after.name}")


# =========================================================
# CHANNEL MONITORING
# =========================================================

@bot.event
async def on_guild_channel_create(channel):
    if channel.guild.id == SERVER_ID:
        add_alert(f"Channel created: {channel.name}")


@bot.event
async def on_guild_channel_delete(channel):
    if channel.guild.id == SERVER_ID:
        add_alert(f"Channel deleted: {channel.name}")


@bot.event
async def on_guild_channel_update(before, after):
    if after.guild.id != SERVER_ID:
        return

    if before.name != after.name:
        add_alert(f"Channel renamed: {before.name} -> {after.name}")

    if before.overwrites != after.overwrites:
        add_alert(f"Channel permission changes: {after.name}")


# =========================================================
# WEBHOOK MONITORING
# =========================================================

@bot.event
async def on_webhooks_update(channel):
    if channel.guild.id == SERVER_ID:
        add_alert(f"Webhook configuration changed in #{channel.name}")


# =========================================================
# MESSAGE DELETION MONITORING
# =========================================================

@bot.event
async def on_message_delete(message):
    if message.guild and message.guild.id == SERVER_ID and not message.author.bot:
        add_alert(
            f"Message deleted in #{getattr(message.channel, 'name', 'unknown')}",
            message.author.id
        )


@bot.event
async def on_bulk_message_delete(messages):
    for message in messages:
        if message.guild and message.guild.id == SERVER_ID and not message.author.bot:
            add_alert(
                f"Bulk message deletion in #{getattr(message.channel, 'name', 'unknown')}",
                message.author.id
            )


# =========================================================
# ERROR HANDLING
# =========================================================

@bot.event
async def on_error(event, *args, **kwargs):
    print(f"Event error: {event}")


# =========================================================
# RUN THE WATCHER
# =========================================================

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "Set DISCORD_TOKEN in Render Environment before starting The Watcher."
        )

    bot.run(TOKEN)
