
import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

# =====================================================
# CONFIGURATION
# =====================================================

TOKEN = os.getenv("DISCORD_TOKEN")

GUILD_ID = 1108961244880314508
AUTHORIZED_ROLE_ID = 1567599630030610502
WORK_LOG_CHANNEL_ID = 1567186504839860234

IST = ZoneInfo("Asia/Kolkata")
DATA_FILE = "watcher_activity.json"

DAILY_DEADLINE_HOURS = 3
INACTIVITY_MESSAGE_LIMIT = 20
INACTIVITY_PERIOD_DAYS = 3
WARNING_COOLDOWN_DAYS = 3

PORT = int(os.environ.get("PORT", 10000))

STAFF_ROLE_IDS = {
    1571062526232236223,
    1571065103413944401,
    1571065681670180975,
    1571065040390586409,
    1571063896977055855,
    1571065144010743818,
    1571063939213566044,
    1571064979686432869,
    1571065232778989679,
    1571071998086611064,
    1571068709454348328,
    1571068590235328563,
    1571078210907668480,
    1571068770112512000,
    1571068769978163250,
    1571068771911602196,
    1571074678481485954,
    1571071574751313972,
    1571071230545625098,
    1571071267723939941,
    1571071401249472512,
    1571073021630881833,
}

# =====================================================
# DATA STORAGE
# =====================================================

data = {
    "daily_logs": {},
    "activity": {},
    "warning_history": {},
    "deadlines": {},
    "sent_days": [],
}


def load_data():
    global data

    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as file:
                loaded = json.load(file)

            for key in data:
                if key in loaded:
                    data[key] = loaded[key]

        except Exception as error:
            print(f"[DATA] Could not load: {error}")


def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4)
    except Exception as error:
        print(f"[DATA] Could not save: {error}")


def now_ist():
    return datetime.now(IST)


def today_key():
    return now_ist().strftime("%Y-%m-%d")


def is_staff(member):
    if not isinstance(member, discord.Member):
        return False

    return any(role.id in STAFF_ROLE_IDS for role in member.roles)


def get_staff_roles(member):
    return [
        role.name
        for role in member.roles
        if role.id in STAFF_ROLE_IDS
    ]


def has_authorized_role(member):
    return (
        isinstance(member, discord.Member)
        and any(role.id == AUTHORIZED_ROLE_ID for role in member.roles)
    )


def get_daily_record(date_key, user_id):
    return data["daily_logs"].get(date_key, {}).get(str(user_id))


def create_daily_record(date_key, member):
    data["daily_logs"].setdefault(date_key, {})

    user_id = str(member.id)

    if user_id not in data["daily_logs"][date_key]:
        data["daily_logs"][date_key][user_id] = {
            "status": "pending",
            "clicked": False,
            "modal_opened": False,
            "work": "",
            "role": ", ".join(get_staff_roles(member)),
            "created_at": now_ist().isoformat(),
            "dm_sent": False,
        }

    return data["daily_logs"][date_key][user_id]


# =====================================================
# DISCORD BOT
# =====================================================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
intents.messages = True
intents.typing = True

bot = commands.Bot(command_prefix="!", intents=intents)


# =====================================================
# RENDER WEB SERVER
# =====================================================

async def homepage(request):
    return web.Response(text="anshuman hi")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", homepage)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=PORT,
    )

    await site.start()

    print(f"[WEB] Keepalive server running on port {PORT}")


# =====================================================
# WORK LOG CHANNEL
# =====================================================

async def get_work_channel():
    channel = bot.get_channel(WORK_LOG_CHANNEL_ID)

    if channel is None:
        try:
            channel = await bot.fetch_channel(WORK_LOG_CHANNEL_ID)
        except Exception as error:
            print(f"[CHANNEL] Could not access work log channel: {error}")

    return channel


async def post_work_log(user, record, date_key, status):
    channel = await get_work_channel()

    if channel is None:
        print("[LOG] Work-log channel not found.")
        return

    embed = discord.Embed(
        title="Daily Work Report",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )

    embed.add_field(
        name="Member",
        value=f"{user.mention} (`{user.id}`)",
        inline=False,
    )

    embed.add_field(name="Date", value=date_key, inline=True)
    embed.add_field(name="Status", value=status, inline=True)

    embed.add_field(
        name="Role",
        value=record.get("role", "Unknown"),
        inline=False,
    )

    if record.get("work"):
        embed.add_field(
            name="Work Completed",
            value=record["work"][:1024],
            inline=False,
        )

    if record.get("submitted_at"):
        submitted = datetime.fromisoformat(record["submitted_at"])
        timestamp = int(submitted.timestamp())

        embed.add_field(
            name="Submitted",
            value=f"<t:{timestamp}:F> (<t:{timestamp}:R>)",
            inline=False,
        )

    try:
        await channel.send(embed=embed)
        print(f"[LOG] Posted report for {user.id}")

    except Exception as error:
        print(f"[LOG] Could not post report: {error}")


# =====================================================
# MEMBERSHIP VERIFICATION
# =====================================================

async def verify_real_staff(user_id):
    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        print("[VERIFY] Guild unavailable.")
        return None

    try:
        member = await guild.fetch_member(int(user_id))

    except discord.NotFound:
        print(f"[VERIFY] User {user_id} is not in the server.")
        return None

    except discord.Forbidden:
        print("[VERIFY] Missing permission to fetch members.")
        return None

    except discord.HTTPException as error:
        print(f"[VERIFY] Discord API error: {error}")
        return None

    if not is_staff(member):
        print(f"[VERIFY] User {user_id} is not staff.")
        return None

    return member


# =====================================================
# WORK REPORT MODAL
# =====================================================

class WorkLogModal(discord.ui.Modal):

    def __init__(self, date_key):
        super().__init__(
            title="Daily Work Report",
            timeout=300,
        )

        self.date_key = date_key

        self.work_input = discord.ui.TextInput(
            label="What did you work on today?",
            placeholder="Briefly describe the work you completed...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
        )

        self.add_item(self.work_input)

    async def on_submit(self, interaction: discord.Interaction):

        member = await verify_real_staff(interaction.user.id)

        if member is None:
            await interaction.response.send_message(
                "Unable to verify your membership or staff role. "
                "Please contact an administrator."
            )
            return

        record = get_daily_record(self.date_key, member.id)

        if record is None:
            await interaction.response.send_message(
                "This check-in has expired."
            )
            return

        record["status"] = "work"
        record["clicked"] = True
        record["work"] = str(self.work_input.value)
        record["submitted_at"] = now_ist().isoformat()

        save_data()

        await interaction.response.send_message(
            "Your work report has been submitted successfully."
        )

        await post_work_log(
            member,
            record,
            self.date_key,
            "Work Submitted",
        )


# =====================================================
# DAILY CHECK-IN BUTTONS
# =====================================================

class DailyCheckinView(discord.ui.View):

    def __init__(self, date_key):
        super().__init__(timeout=None)

        self.date_key = date_key

        self.work_button.custom_id = f"watcher:work:{date_key}"
        self.no_work_button.custom_id = f"watcher:nowork:{date_key}"

    @discord.ui.button(
        label="Log My Work",
        style=discord.ButtonStyle.success,
    )
    async def work_button(self, interaction, button):

        member = await verify_real_staff(interaction.user.id)

        if member is None:
            await interaction.response.send_message(
                "Unable to verify your membership or staff role."
            )
            return

        record = get_daily_record(self.date_key, member.id)

        if record is None:
            await interaction.response.send_message(
                "This check-in has expired."
            )
            return

        if record["status"] in ("work", "no_work"):
            await interaction.response.send_message(
                "You have already submitted your daily report."
            )
            return

        record["clicked"] = True
        record["modal_opened"] = True
        record["status"] = "incomplete"

        save_data()

        await interaction.response.send_modal(
            WorkLogModal(self.date_key)
        )

    @discord.ui.button(
        label="No Work Done",
        style=discord.ButtonStyle.secondary,
    )
    async def no_work_button(self, interaction, button):

        member = await verify_real_staff(interaction.user.id)

        if member is None:
            await interaction.response.send_message(
                "Unable to verify your membership or staff role."
            )
            return

        record = get_daily_record(self.date_key, member.id)

        if record is None:
            await interaction.response.send_message(
                "This check-in has expired."
            )
            return

        if record["status"] in ("work", "no_work"):
            await interaction.response.send_message(
                "You have already submitted your daily report."
            )
            return

        record["clicked"] = True
        record["status"] = "no_work"
        record["work"] = "No work completed."
        record["submitted_at"] = now_ist().isoformat()

        save_data()

        await interaction.response.send_message(
            "Your response has been recorded."
        )

        await post_work_log(
            member,
            record,
            self.date_key,
            "No Work Done",
        )


# =====================================================
# DAILY CHECK-IN EMBED
# =====================================================

def create_daily_embed(date_key):

    embed = discord.Embed(
        title="Daily Staff Check-In",
        description=(
            "Please submit your daily work report.\n\n"
            "**Log My Work** — Describe what you worked on.\n"
            "**No Work Done** — Select this if you completed no work today.\n\n"
            "You have **3 hours** to respond."
        ),
        color=discord.Color.blurple(),
    )

    embed.add_field(name="Date", value=date_key, inline=True)
    embed.add_field(name="Deadline", value="3 hours", inline=True)

    embed.set_footer(text="BloxBerry Labs • The Watcher")

    return embed


# =====================================================
# ACTIVITY WARNING EMBED
# =====================================================

def create_warning_embed():

    embed = discord.Embed(
        title="Staff Activity Warning",
        description=(
            "Our records indicate that your activity has been below "
            "the expected level over the past 3 days.\n\n"
            "Please remain active and communicate with the team if "
            "you are unavailable.\n\n"
            "Continued inactivity may result in a demotion."
        ),
        color=discord.Color.orange(),
    )

    embed.set_footer(text="BloxBerry Labs • The Watcher")

    return embed


# =====================================================
# SEND DAILY CHECK-INS
# =====================================================

async def send_real_daily_checkins():

    date_key = today_key()

    if date_key in data["sent_days"]:
        return

    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        print("[DAILY] Server not found.")
        return

    print("[DAILY] Sending staff check-in DMs...")

    members = []

    try:
        async for member in guild.fetch_members(limit=None):
            if is_staff(member):
                members.append(member)

    except Exception as error:
        print(f"[DAILY] Could not fetch members: {error}")
        return

    data["deadlines"][date_key] = (
        now_ist() + timedelta(hours=DAILY_DEADLINE_HOURS)
    ).isoformat()

    data["sent_days"].append(date_key)
    save_data()

    for member in members:

        record = create_daily_record(date_key, member)

        try:
            await member.send(
                embed=create_daily_embed(date_key),
                view=DailyCheckinView(date_key),
            )

            record["dm_sent"] = True
            print(f"[DAILY] Sent to {member} ({member.id})")

        except discord.Forbidden:
            record["dm_sent"] = False
            print(f"[DAILY] Could not DM {member} ({member.id})")

        except Exception as error:
            record["dm_sent"] = False
            print(f"[DAILY] Error DMing {member}: {error}")

        save_data()
        await asyncio.sleep(0.5)

    print(f"[DAILY] Finished sending to {len(members)} staff members.")


# =====================================================
# DEADLINE MONITOR
# =====================================================

async def check_daily_deadlines():

    current_time = now_ist()

    for date_key, deadline_string in list(data["deadlines"].items()):

        try:
            deadline = datetime.fromisoformat(deadline_string)
        except Exception:
            continue

        if current_time < deadline:
            continue

        daily_records = data["daily_logs"].get(date_key, {})

        for user_id, record in daily_records.items():

            if record.get("status") in (
                "work",
                "no_work",
                "no_response",
            ):
                continue

            if record.get("modal_opened"):
                record["status"] = "incomplete"
                status = "Incomplete Form"

            elif not record.get("clicked"):
                record["status"] = "no_response"
                status = "No Response"

            else:
                continue

            if record.get("deadline_reported"):
                continue

            record["deadline_reported"] = True

            try:
                user = await bot.fetch_user(int(user_id))
                channel = await get_work_channel()

                if channel is not None:
                    embed = discord.Embed(
                        title=status,
                        color=discord.Color.red(),
                        timestamp=datetime.now(timezone.utc),
                    )

                    embed.add_field(
                        name="Member",
                        value=f"{user.mention} (`{user_id}`)",
                        inline=False,
                    )

                    embed.add_field(
                        name="Role",
                        value=record.get("role", "Unknown"),
                        inline=False,
                    )

                    embed.add_field(name="Date", value=date_key)

                    await channel.send(embed=embed)

            except Exception as error:
                print(f"[DEADLINE] Could not report {user_id}: {error}")

        save_data()

        data["deadlines"].pop(date_key, None)
        save_data()


# =====================================================
# ACTIVITY TRACKING FOR LEADERBOARD
# =====================================================

def add_activity(user_id, activity_type):

    user_id = str(user_id)

    data["activity"].setdefault(user_id, {
        "messages": [],
        "typing": [],
    })

    data["activity"][user_id].setdefault(activity_type, [])

    data["activity"][user_id][activity_type].append(
        now_ist().isoformat()
    )

    save_data()


def get_recent_activity(user_id, activity_type, days=3):

    records = data["activity"].get(str(user_id), {})
    timestamps = records.get(activity_type, [])

    cutoff = now_ist() - timedelta(days=days)

    recent = []

    for timestamp in timestamps:
        try:
            parsed = datetime.fromisoformat(timestamp)

            if parsed >= cutoff:
                recent.append(timestamp)

        except Exception:
            continue

    return recent


@bot.event
async def on_message(message):

    if message.author.bot:
        return

    if message.guild and message.guild.id == GUILD_ID:

        if isinstance(message.author, discord.Member) and is_staff(message.author):
            add_activity(message.author.id, "messages")

        content = message.content.strip()
        lower = content.lower()

        # Both help formats work.
        if lower in ("watcherhelp", "watcher help", "watcher commands"):
            await handle_watcher_command(message, "help", content)
            return

        if lower == "watcher leaderboard":
            await handle_watcher_command(message, "leaderboard", content)
            return

        if lower == "watcher inactive":
            await handle_watcher_command(message, "inactive", content)
            return

        if lower.startswith("watcher"):
            await handle_watcher_command(message, "user", content)
            return

    await bot.process_commands(message)


@bot.event
async def on_typing(channel, user, when):

    if isinstance(user, discord.Member):
        if user.guild.id == GUILD_ID and is_staff(user):
            add_activity(user.id, "typing")


# =====================================================
# WATCHER COMMANDS
# =====================================================

async def handle_watcher_command(message, command, original_content):

    if not has_authorized_role(message.author):
        await message.reply(
            "You don't have permission to use The Watcher.",
            mention_author=False,
        )
        return

    # Save the original command before deleting it.
    content = original_content.strip()

    try:
        await message.delete()
    except Exception:
        pass

    # HELP
    if command == "help":

        embed = discord.Embed(
            title="The Watcher — Commands",
            description="Available staff management commands:",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="watcher @user",
            value="View a staff member's daily reports and submission times.",
            inline=False,
        )

        embed.add_field(
            name="watcher leaderboard",
            value="View the staff activity leaderboard.",
            inline=False,
        )

        embed.add_field(
            name="watcher inactive",
            value="View staff who have not clicked either daily check-in button.",
            inline=False,
        )

        embed.add_field(
            name="watcherhelp",
            value="Display this help menu. `watcher help` also works.",
            inline=False,
        )

        await message.channel.send(embed=embed)
        return

    # USER REPORT
    if command == "user":

        target = None

        if message.mentions:
            target = message.mentions[0]

        else:
            parts = content.split()

            if len(parts) >= 2:
                raw_id = (
                    parts[1]
                    .replace("<@", "")
                    .replace(">", "")
                    .replace("!", "")
                )

                if raw_id.isdigit():
                    try:
                        target = await message.guild.fetch_member(int(raw_id))
                    except Exception:
                        target = None

        if target is None:
            await message.channel.send(
                "Usage: `watcher @user`\n"
                "Example: `watcher @Anshuman`"
            )
            return

        embed = discord.Embed(
            title=f"Staff Report — {target.display_name}",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="Staff Roles",
            value=", ".join(get_staff_roles(target)) or "None",
            inline=False,
        )

        # Recent check-ins: no message or typing counts here.
        report_dates = sorted(
            [
                date for date in data["daily_logs"]
                if not date.startswith("TEST-")
            ],
            reverse=True,
        )[:7]

        report_lines = []

        for date_key in report_dates:

            record = get_daily_record(date_key, target.id)

            if not record:
                continue

            status = record.get("status", "Unknown").replace(
                "_", " "
            ).title()

            submitted_at = record.get("submitted_at")

            if submitted_at:
                try:
                    submitted = datetime.fromisoformat(submitted_at)
                    unix_time = int(submitted.timestamp())

                    time_text = (
                        f"<t:{unix_time}:F> "
                        f"(<t:{unix_time}:R>)"
                    )

                except Exception:
                    time_text = submitted_at

            else:
                time_text = "No submission time recorded."

            line = (
                f"**{date_key}** — {status}\n"
                f"Submitted: {time_text}"
            )

            if record.get("work"):
                line += f"\nWork: {record['work'][:250]}"

            report_lines.append(line)

        embed.add_field(
            name="Recent Check-ins",
            value="\n\n".join(report_lines) or "No check-ins found.",
            inline=False,
        )

        await message.channel.send(embed=embed)
        return

    # LEADERBOARD
    if command == "leaderboard":

        entries = []

        for member in message.guild.members:

            if not is_staff(member):
                continue

            messages = len(
                get_recent_activity(member.id, "messages")
            )

            typing = len(
                get_recent_activity(member.id, "typing")
            )

            entries.append((member, messages, typing))

        entries.sort(
            key=lambda item: (item[1], item[2]),
            reverse=True,
        )

        embed = discord.Embed(
            title="The Watcher — Staff Leaderboard",
            description="Activity recorded over the last 3 days.",
            color=discord.Color.blurple(),
        )

        if not entries:
            embed.description = "No staff activity found."

        else:
            lines = []

            for index, (member, messages, typing) in enumerate(
                entries[:20], 1
            ):
                lines.append(
                    f"**{index}.** {member.mention} — "
                    f"{messages} messages | {typing} typing events"
                )

            embed.description = "\n".join(lines)

        await message.channel.send(embed=embed)
        return

    # INACTIVE CHECK-INS
    if command == "inactive":

        date_key = today_key()
        daily_records = data["daily_logs"].get(date_key, {})

        inactive_members = []

        for member in message.guild.members:

            if not is_staff(member):
                continue

            record = daily_records.get(str(member.id))

            if record is None or not record.get("clicked", False):
                inactive_members.append(member)

        embed = discord.Embed(
            title="The Watcher — No Check-in",
            description=(
                "\n".join(member.mention for member in inactive_members)
                if inactive_members
                else "Everyone has clicked a check-in button."
            ),
            color=discord.Color.orange(),
        )

        embed.set_footer(text=f"Date: {date_key}")

        await message.channel.send(embed=embed)


# =====================================================
# INACTIVITY WARNING MONITOR
# =====================================================

@tasks.loop(hours=6)
async def inactivity_warning_monitor():

    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        return

    current_time = now_ist()

    for member in guild.members:

        if not is_staff(member):
            continue

        if member.joined_at:
            joined_at = member.joined_at.astimezone(IST)

            if current_time - joined_at < timedelta(days=3):
                continue

        messages = get_recent_activity(
            member.id,
            "messages",
            days=INACTIVITY_PERIOD_DAYS,
        )

        if len(messages) >= INACTIVITY_MESSAGE_LIMIT:
            continue

        last_warning = data["warning_history"].get(str(member.id))

        if last_warning:
            try:
                previous = datetime.fromisoformat(last_warning)

                if current_time - previous < timedelta(
                    days=WARNING_COOLDOWN_DAYS
                ):
                    continue

            except Exception:
                pass

        try:
            await member.send(embed=create_warning_embed())

            data["warning_history"][str(member.id)] = (
                current_time.isoformat()
            )

            print(f"[WARNING] Sent to {member}")

        except discord.Forbidden:
            print(f"[WARNING] Could not DM {member}")

        except Exception as error:
            print(f"[WARNING] Error sending to {member}: {error}")

        save_data()
        await asyncio.sleep(0.5)


# =====================================================
# SCHEDULERS
# =====================================================

@tasks.loop(minutes=1)
async def daily_scheduler():

    current = now_ist()

    if current.hour == 0 and current.minute == 0:
        await send_real_daily_checkins()


@tasks.loop(minutes=1)
async def deadline_scheduler():
    await check_daily_deadlines()


# =====================================================
# STARTUP
# =====================================================

web_server_started = False


@bot.event
async def on_ready():

    global web_server_started

    print("------------------------------------")
    print(f"The Watcher is online as {bot.user}")
    print(f"Server ID: {GUILD_ID}")
    print("------------------------------------")

    # Restore persistent check-in buttons.
    for date_key in data["daily_logs"]:

        if date_key.startswith("TEST-"):
            continue

        bot.add_view(DailyCheckinView(date_key))

    if not daily_scheduler.is_running():
        daily_scheduler.start()

    if not deadline_scheduler.is_running():
        deadline_scheduler.start()

    if not inactivity_warning_monitor.is_running():
        inactivity_warning_monitor.start()

    if not web_server_started:
        web_server_started = True
        asyncio.create_task(start_web_server())


# =====================================================
# RUN
# =====================================================

load_data()

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing. Add it to your .env file."
    )

bot.run(TOKEN)
