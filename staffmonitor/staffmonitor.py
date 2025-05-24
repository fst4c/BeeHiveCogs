import discord
from red_commons.logging import getLogger
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import humanize_list, humanize_number, pagify
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS
from datetime import datetime, timedelta
import asyncio
import csv
import io
import json

log = getLogger("red.BeeHive.staffmonitor")

DEFAULT_GUILD = {
    "modlog_channel": None,
    "mod_channels": [],
    "staff_roles": [],
    "dm_logging": False,
    "privacy_roles": [],
    "notes": {},
    "feedback": {},
    "alerts": {
        "mass_ban_threshold": 3,
        "excessive_punish_threshold": 5,
        "alert_channel": None,
    },
}

DEFAULT_MEMBER = {
    "punishments": [],
    "interactions": [],
    "voice_sessions": [],
    "command_usage": [],
    "last_active": None,
    "afk": False,
    "notes": [],
    "feedback": [],
}

PUNISHMENT_TYPES = ["ban", "kick", "mute", "warn", "timeout", "unban", "unmute"]

class StaffMonitor(commands.Cog):
    """
    Staff Monitoring & Analytics Cog
    """

    __version__ = "1.0.0"
    __author__ = "aikaterna, max, etc."

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xBEE123456789, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)
        self.config.register_member(**DEFAULT_MEMBER)
        self._voice_states = {}  # member_id: (channel_id, join_time)
        self._afk_check_task = self.bot.loop.create_task(self.afk_check_loop())

    def cog_unload(self):
        self._afk_check_task.cancel()

    # --- Utility Functions ---

    async def is_staff(self, member: discord.Member, guild: discord.Guild = None):
        guild = guild or member.guild
        staff_roles = await self.config.guild(guild).staff_roles()
        if not staff_roles:
            return member.guild_permissions.kick_members or member.guild_permissions.ban_members
        return any(r.id in staff_roles for r in member.roles)

    async def get_mod_channels(self, guild: discord.Guild):
        chans = await self.config.guild(guild).mod_channels()
        return [guild.get_channel(cid) for cid in chans if guild.get_channel(cid)]

    async def get_privacy_roles(self, guild: discord.Guild):
        return await self.config.guild(guild).privacy_roles()

    # --- Punishment Tracking ---

    async def log_punishment(
        self, guild: discord.Guild, staff: discord.Member, target: discord.Member, action: str, reason: str = None
    ):
        now = datetime.utcnow().isoformat()
        entry = {
            "action": action,
            "staff_id": staff.id,
            "staff_name": str(staff),
            "target_id": target.id,
            "target_name": str(target),
            "reason": reason or "",
            "timestamp": now,
        }
        async with self.config.member(target).punishments() as punishments:
            punishments.append(entry)
        async with self.config.member(staff).punishments() as staff_punishments:
            staff_punishments.append(entry)
        # Alert if needed
        await self.check_alerts(guild, staff, action)

    async def check_alerts(self, guild: discord.Guild, staff: discord.Member, action: str):
        alerts = await self.config.guild(guild).alerts()
        alert_channel_id = alerts.get("alert_channel")
        if not alert_channel_id:
            return
        alert_channel = guild.get_channel(alert_channel_id)
        if not alert_channel:
            return
        # Mass ban/kick detection
        if action in ("ban", "kick"):
            async with self.config.member(staff).punishments() as staff_punishments:
                recent = [
                    p for p in staff_punishments
                    if p["action"] == action and
                    datetime.fromisoformat(p["timestamp"]) > datetime.utcnow() - timedelta(minutes=10)
                ]
                threshold = alerts.get("mass_ban_threshold", 3)
                if len(recent) >= threshold:
                    await alert_channel.send(
                        f":warning: **{staff.mention}** has performed {len(recent)} `{action}` actions in the last 10 minutes!"
                    )
        # Excessive punishments
        async with self.config.member(staff).punishments() as staff_punishments:
            recent = [
                p for p in staff_punishments
                if datetime.fromisoformat(p["timestamp"]) > datetime.utcnow() - timedelta(hours=1)
            ]
            threshold = alerts.get("excessive_punish_threshold", 5)
            if len(recent) >= threshold:
                await alert_channel.send(
                    f":warning: **{staff.mention}** has issued {len(recent)} punishments in the last hour!"
                )

    # --- Interaction History ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        if not await self.is_staff(message.author, message.guild):
            return
        # Log interaction
        entry = {
            "type": "message",
            "channel_id": message.channel.id,
            "channel_name": str(message.channel),
            "content": message.content,
            "timestamp": datetime.utcnow().isoformat(),
            "message_id": message.id,
        }
        async with self.config.member(message.author).interactions() as interactions:
            interactions.append(entry)
        # Update last active
        await self.config.member(message.author).last_active.set(datetime.utcnow().isoformat())

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if after.guild is None or after.author.bot:
            return
        if not await self.is_staff(after.author, after.guild):
            return
        entry = {
            "type": "edit",
            "channel_id": after.channel.id,
            "channel_name": str(after.channel),
            "before": before.content,
            "after": after.content,
            "timestamp": datetime.utcnow().isoformat(),
            "message_id": after.id,
        }
        async with self.config.member(after.author).interactions() as interactions:
            interactions.append(entry)

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.guild is None or message.author.bot:
            return
        if not await self.is_staff(message.author, message.guild):
            return
        entry = {
            "type": "delete",
            "channel_id": message.channel.id,
            "channel_name": str(message.channel),
            "content": message.content,
            "timestamp": datetime.utcnow().isoformat(),
            "message_id": message.id,
        }
        async with self.config.member(message.author).interactions() as interactions:
            interactions.append(entry)

    # --- Voice/Activity Tracking ---

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not await self.is_staff(member, member.guild):
            return
        now = datetime.utcnow()
        if before.channel is None and after.channel is not None:
            # Joined voice
            self._voice_states[member.id] = (after.channel.id, now)
        elif before.channel is not None and after.channel is None:
            # Left voice
            join_info = self._voice_states.pop(member.id, None)
            if join_info:
                channel_id, join_time = join_info
                duration = (now - join_time).total_seconds()
                entry = {
                    "channel_id": channel_id,
                    "channel_name": str(before.channel),
                    "join_time": join_time.isoformat(),
                    "leave_time": now.isoformat(),
                    "duration": duration,
                }
                async with self.config.member(member).voice_sessions() as sessions:
                    sessions.append(entry)
        elif before.channel != after.channel:
            # Switched channels
            join_info = self._voice_states.pop(member.id, None)
            if join_info:
                channel_id, join_time = join_info
                duration = (now - join_time).total_seconds()
                entry = {
                    "channel_id": channel_id,
                    "channel_name": str(before.channel),
                    "join_time": join_time.isoformat(),
                    "leave_time": now.isoformat(),
                    "duration": duration,
                }
                async with self.config.member(member).voice_sessions() as sessions:
                    sessions.append(entry)
            self._voice_states[member.id] = (after.channel.id, now)

    async def afk_check_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    for member in guild.members:
                        if not await self.is_staff(member, guild):
                            continue
                        last_active = await self.config.member(member).last_active()
                        if last_active:
                            last_active_dt = datetime.fromisoformat(last_active)
                            idle = (datetime.utcnow() - last_active_dt).total_seconds() > 1800
                            await self.config.member(member).afk.set(idle)
            except Exception as e:
                log.error("AFK check error: %s", e)
            await asyncio.sleep(600)

    # --- Command Usage Analytics ---

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context):
        if ctx.guild is None or ctx.author.bot:
            return
        if not await self.is_staff(ctx.author, ctx.guild):
            return
        entry = {
            "command": ctx.command.qualified_name if ctx.command else "unknown",
            "timestamp": datetime.utcnow().isoformat(),
            "channel_id": ctx.channel.id,
            "channel_name": str(ctx.channel),
        }
        async with self.config.member(ctx.author).command_usage() as usage:
            usage.append(entry)
        # Update last active
        await self.config.member(ctx.author).last_active.set(datetime.utcnow().isoformat())

    # --- Notes System ---

    @commands.group()
    @checks.mod_or_permissions(manage_guild=True)
    async def staffnotes(self, ctx):
        """Staff notes system."""

    @staffnotes.command()
    async def add(self, ctx, member: discord.Member, *, note: str):
        """Add a note about a staff member."""
        if not await self.is_staff(member, ctx.guild):
            await ctx.send("That user is not a staff member.")
            return
        entry = {
            "author_id": ctx.author.id,
            "author_name": str(ctx.author),
            "note": note,
            "timestamp": datetime.utcnow().isoformat(),
        }
        async with self.config.member(member).notes() as notes:
            notes.append(entry)
        await ctx.send(f"Note added for {member.mention}.")

    @staffnotes.command()
    async def view(self, ctx, member: discord.Member):
        """View notes about a staff member."""
        notes = await self.config.member(member).notes()
        if not notes:
            await ctx.send("No notes for this staff member.")
            return
        pages = []
        for i, n in enumerate(notes, 1):
            pages.append(
                f"**{i}.** *By:* <@{n['author_id']}> at {n['timestamp']}\n> {n['note']}"
            )
        for page in pagify("\n\n".join(pages), delims=["\n"], page_length=1800):
            await ctx.send(page)

    # --- Feedback System ---

    @commands.group()
    async def stafffeedback(self, ctx):
        """Feedback system for staff."""

    @stafffeedback.command()
    async def rate(self, ctx, member: discord.Member, rating: int, *, feedback: str = ""):
        """Rate a staff member (1-5 stars) and leave feedback."""
        if not await self.is_staff(member, ctx.guild):
            await ctx.send("That user is not a staff member.")
            return
        if rating < 1 or rating > 5:
            await ctx.send("Rating must be between 1 and 5.")
            return
        entry = {
            "user_id": ctx.author.id,
            "user_name": str(ctx.author),
            "rating": rating,
            "feedback": feedback,
            "timestamp": datetime.utcnow().isoformat(),
        }
        async with self.config.member(member).feedback() as feedbacks:
            feedbacks.append(entry)
        await ctx.send(f"Feedback submitted for {member.mention}.")

    @stafffeedback.command()
    @checks.mod_or_permissions(manage_guild=True)
    async def view(self, ctx, member: discord.Member):
        """View feedback for a staff member."""
        feedbacks = await self.config.member(member).feedback()
        if not feedbacks:
            await ctx.send("No feedback for this staff member.")
            return
        avg = sum(f["rating"] for f in feedbacks) / len(feedbacks)
        msg = f"**Average rating:** {avg:.2f}/5 ({len(feedbacks)} ratings)\n"
        for i, f in enumerate(feedbacks, 1):
            msg += f"\n**{i}.** {f['rating']}★ by <@{f['user_id']}> at {f['timestamp']}\n> {f['feedback']}"
        for page in pagify(msg, page_length=1800):
            await ctx.send(page)

    # --- Reports & Analytics ---

    @commands.group()
    @checks.mod_or_permissions(manage_guild=True)
    async def staffstats(self, ctx):
        """Staff activity and analytics."""

    @staffstats.command()
    async def punishments(self, ctx, member: discord.Member = None):
        """Show punishment logs for a staff member or all staff."""
        if member:
            logs = await self.config.member(member).punishments()
            if not logs:
                await ctx.send("No punishments found for this member.")
                return
            msg = f"**Punishments for {member.mention}:**\n"
            for p in logs[-20:]:
                msg += (
                    f"\n`{p['timestamp']}`: {p['action']} -> <@{p['target_id']}>"
                    f" (by <@{p['staff_id']}>) Reason: {p['reason']}"
                )
            for page in pagify(msg, page_length=1800):
                await ctx.send(page)
        else:
            # Top 5 staff by punishments this week
            now = datetime.utcnow()
            week_ago = now - timedelta(days=7)
            staff_counts = {}
            for m in ctx.guild.members:
                if not await self.is_staff(m, ctx.guild):
                    continue
                logs = await self.config.member(m).punishments()
                count = sum(
                    1
                    for p in logs
                    if datetime.fromisoformat(p["timestamp"]) > week_ago
                )
                if count:
                    staff_counts[m] = count
            if not staff_counts:
                await ctx.send("No punishments found for any staff this week.")
                return
            top = sorted(staff_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            msg = "**Top 5 staff by punishments this week:**\n"
            for i, (m, c) in enumerate(top, 1):
                msg += f"{i}. {m.mention}: {c}\n"
            await ctx.send(msg)

    @staffstats.command()
    async def activity(self, ctx, member: discord.Member = None):
        """Show text/voice activity for a staff member or all staff."""
        if member:
            voice = await self.config.member(member).voice_sessions()
            text = await self.config.member(member).interactions()
            total_voice = sum(s["duration"] for s in voice)
            total_text = len([i for i in text if i["type"] == "message"])
            msg = (
                f"**Activity for {member.mention}:**\n"
                f"Text messages: {total_text}\n"
                f"Voice time: {humanize_number(int(total_voice // 60))} minutes"
            )
            await ctx.send(msg)
        else:
            # Leaderboard
            leaderboard = []
            for m in ctx.guild.members:
                if not await self.is_staff(m, ctx.guild):
                    continue
                voice = await self.config.member(m).voice_sessions()
                text = await self.config.member(m).interactions()
                total_voice = sum(s["duration"] for s in voice)
                total_text = len([i for i in text if i["type"] == "message"])
                leaderboard.append((m, total_text, total_voice))
            if not leaderboard:
                await ctx.send("No staff activity found.")
                return
            leaderboard.sort(key=lambda x: (x[1], x[2]), reverse=True)
            msg = "**Staff Activity Leaderboard:**\n"
            for i, (m, t, v) in enumerate(leaderboard[:10], 1):
                msg += f"{i}. {m.mention}: {t} messages, {humanize_number(int(v // 60))} min voice\n"
            await ctx.send(msg)

    @staffstats.command()
    async def commands(self, ctx, member: discord.Member = None):
        """Show command usage stats for a staff member or all staff."""
        if member:
            usage = await self.config.member(member).command_usage()
            if not usage:
                await ctx.send("No command usage found for this member.")
                return
            counter = {}
            for u in usage:
                counter[u["command"]] = counter.get(u["command"], 0) + 1
            msg = f"**Command usage for {member.mention}:**\n"
            for cmd, count in sorted(counter.items(), key=lambda x: x[1], reverse=True):
                msg += f"{cmd}: {count}\n"
            await ctx.send(msg)
        else:
            # Most/least used commands by staff
            counter = {}
            for m in ctx.guild.members:
                if not await self.is_staff(m, ctx.guild):
                    continue
                usage = await self.config.member(m).command_usage()
                for u in usage:
                    counter[u["command"]] = counter.get(u["command"], 0) + 1
            if not counter:
                await ctx.send("No command usage found for any staff.")
                return
            most = sorted(counter.items(), key=lambda x: x[1], reverse=True)[:5]
            least = sorted(counter.items(), key=lambda x: x[1])[:5]
            msg = "**Most used staff commands:**\n"
            for cmd, count in most:
                msg += f"{cmd}: {count}\n"
            msg += "\n**Least used staff commands:**\n"
            for cmd, count in least:
                msg += f"{cmd}: {count}\n"
            await ctx.send(msg)

    @staffstats.command()
    async def export(self, ctx, member: discord.Member = None, format: str = "csv"):
        """Export staff logs to CSV or JSON."""
        if member:
            data = {
                "punishments": await self.config.member(member).punishments(),
                "interactions": await self.config.member(member).interactions(),
                "voice_sessions": await self.config.member(member).voice_sessions(),
                "command_usage": await self.config.member(member).command_usage(),
                "notes": await self.config.member(member).notes(),
                "feedback": await self.config.member(member).feedback(),
            }
            filename = f"{member.id}_stafflogs.{format}"
        else:
            data = {}
            for m in ctx.guild.members:
                if not await self.is_staff(m, ctx.guild):
                    continue
                data[m.id] = {
                    "punishments": await self.config.member(m).punishments(),
                    "interactions": await self.config.member(m).interactions(),
                    "voice_sessions": await self.config.member(m).voice_sessions(),
                    "command_usage": await self.config.member(m).command_usage(),
                    "notes": await self.config.member(m).notes(),
                    "feedback": await self.config.member(m).feedback(),
                }
            filename = f"{ctx.guild.id}_stafflogs.{format}"
        if format.lower() == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            if member:
                for k, v in data.items():
                    writer.writerow([k, json.dumps(v)])
            else:
                for mid, logs in data.items():
                    for k, v in logs.items():
                        writer.writerow([mid, k, json.dumps(v)])
            buf.seek(0)
            file = discord.File(io.BytesIO(buf.getvalue().encode()), filename=filename)
        else:
            file = discord.File(io.BytesIO(json.dumps(data, indent=2).encode()), filename=filename)
        await ctx.send("Here are the logs:", file=file)

    # --- Config Commands ---

    @commands.group()
    @checks.admin_or_permissions(administrator=True)
    async def staffmonitorset(self, ctx):
        """StaffMonitor configuration."""

    @staffmonitorset.command()
    async def modlog(self, ctx, channel: discord.TextChannel = None):
        """Set the modlog channel for staff actions."""
        if channel is None:
            await self.config.guild(ctx.guild).modlog_channel.set(None)
            await ctx.send("Modlog channel unset.")
        else:
            await self.config.guild(ctx.guild).modlog_channel.set(channel.id)
            await ctx.send(f"Modlog channel set to {channel.mention}.")

    @staffmonitorset.command()
    async def modchannels(self, ctx, *channels: discord.TextChannel):
        """Set channels considered as mod channels."""
        ids = [c.id for c in channels]
        await self.config.guild(ctx.guild).mod_channels.set(ids)
        await ctx.send(f"Mod channels set: {', '.join(c.mention for c in channels)}")

    @staffmonitorset.command()
    async def staffroles(self, ctx, *roles: discord.Role):
        """Set staff roles for monitoring."""
        ids = [r.id for r in roles]
        await self.config.guild(ctx.guild).staff_roles.set(ids)
        await ctx.send(f"Staff roles set: {', '.join(r.mention for r in roles)}")

    @staffmonitorset.command()
    async def privacyroles(self, ctx, *roles: discord.Role):
        """Set roles that can view logs/reports."""
        ids = [r.id for r in roles]
        await self.config.guild(ctx.guild).privacy_roles.set(ids)
        await ctx.send(f"Privacy roles set: {', '.join(r.mention for r in roles)}")

    @staffmonitorset.command()
    async def dmlogging(self, ctx, enabled: bool):
        """Enable/disable DM logging for staff."""
        await self.config.guild(ctx.guild).dm_logging.set(enabled)
        await ctx.send(f"DM logging {'enabled' if enabled else 'disabled'}.")

    @staffmonitorset.command()
    async def alertchannel(self, ctx, channel: discord.TextChannel = None):
        """Set the alert channel for suspicious staff activity."""
        if channel is None:
            await self.config.guild(ctx.guild).alerts.alert_channel.set(None)
            await ctx.send("Alert channel unset.")
        else:
            await self.config.guild(ctx.guild).alerts.alert_channel.set(channel.id)
            await ctx.send(f"Alert channel set to {channel.mention}.")

    @staffmonitorset.command()
    async def alertthresholds(self, ctx, mass_ban: int = 3, excessive_punish: int = 5):
        """Set thresholds for mass ban/kick and excessive punishments."""
        await self.config.guild(ctx.guild).alerts.mass_ban_threshold.set(mass_ban)
        await self.config.guild(ctx.guild).alerts.excessive_punish_threshold.set(excessive_punish)
        await ctx.send(f"Thresholds set: mass ban={mass_ban}, excessive punish={excessive_punish}")

    # --- Integration with Other Cogs (Basic) ---

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        # Try to find who banned
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
            if entry.target.id == user.id:
                staff = entry.user
                if staff and await self.is_staff(staff, guild):
                    await self.log_punishment(guild, staff, user, "ban", entry.reason)
                break

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.unban):
            if entry.target.id == user.id:
                staff = entry.user
                if staff and await self.is_staff(staff, guild):
                    await self.log_punishment(guild, staff, user, "unban", entry.reason)
                break

    @commands.Cog.listener()
    async def on_member_kick(self, guild, user):
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
            if entry.target.id == user.id:
                staff = entry.user
                if staff and await self.is_staff(staff, guild):
                    await self.log_punishment(guild, staff, user, "kick", entry.reason)
                break

    # --- Help/Info ---

    @commands.command()
    async def staffmonitorinfo(self, ctx):
        """Show info about StaffMonitor cog."""
        embed = discord.Embed(
            title="StaffMonitor",
            description="Staff monitoring, analytics, and reporting for your server.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Version", value=self.__version__)
        embed.add_field(name="Features", value=(
            "- Punishment tracking\n"
            "- Staff interaction history\n"
            "- Time spent in chat/voice\n"
            "- Command usage analytics\n"
            "- Customizable alerts & reports\n"
            "- Dashboard/stats commands\n"
            "- Notes & feedback system\n"
            "- Integration with mod cogs\n"
            "- Export logs to CSV/JSON"
        ), inline=False)
        embed.set_footer(text=f"By {self.__author__}")
        await ctx.send(embed=embed)
