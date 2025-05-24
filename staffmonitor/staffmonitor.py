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

    # --- Master Command Group: staff ---

    @commands.group()
    async def staff(self, ctx):
        """Staff monitoring, analytics, notes, feedback, and configuration."""

    # --- Subgroup: staff notes ---

    @staff.group(name="notes")
    @checks.mod_or_permissions(manage_guild=True)
    async def staff_notes(self, ctx):
        """Staff notes system."""

    @staff_notes.command(name="add")
    async def staff_notes_add(self, ctx, member: discord.Member, *, note: str):
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

    @staff_notes.command(name="view")
    async def staff_notes_view(self, ctx, member: discord.Member):
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

    # --- Subgroup: staff feedback ---

    @staff.group(name="feedback")
    async def staff_feedback(self, ctx):
        """Feedback system for staff."""

    @staff_feedback.command(name="rate")
    async def staff_feedback_rate(self, ctx, member: discord.Member, rating: int, *, feedback: str = ""):
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

    @staff_feedback.command(name="view")
    @checks.mod_or_permissions(manage_guild=True)
    async def staff_feedback_view(self, ctx, member: discord.Member):
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

    # --- Subgroup: staff stats ---

    @staff.group(name="stats")
    @checks.mod_or_permissions(manage_guild=True)
    async def staff_stats(self, ctx):
        """Staff activity and analytics."""

    @staff_stats.command(name="profile")
    async def staff_stats_profile(self, ctx, member: discord.Member = None):
        """
        Show a comprehensive profile for a staff member, including punishments, activity, and command usage.
        """
        member = member or ctx.author
        if not await self.is_staff(member, ctx.guild):
            await ctx.send("That user is not a staff member.")
            return

        # Gather punishments
        punishments = await self.config.member(member).punishments()
        punishments_count = len(punishments)
        last_punishments = punishments[-5:] if punishments else []

        # Gather activity
        voice_sessions = await self.config.member(member).voice_sessions()
        total_voice = sum(s["duration"] for s in voice_sessions)
        total_voice_minutes = int(total_voice // 60)
        interactions = await self.config.member(member).interactions()
        total_text = len([i for i in interactions if i["type"] == "message"])

        # Gather command usage
        command_usage = await self.config.member(member).command_usage()
        command_counter = {}
        for u in command_usage:
            command_counter[u["command"]] = command_counter.get(u["command"], 0) + 1
        top_commands = sorted(command_counter.items(), key=lambda x: x[1], reverse=True)[:5]

        # Gather feedback
        feedbacks = await self.config.member(member).feedback()
        avg_rating = None
        if feedbacks:
            avg_rating = sum(f["rating"] for f in feedbacks) / len(feedbacks)

        embed = discord.Embed(
            title=f"Staff Profile: {member.display_name}",
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow(),
        )
        embed.set_thumbnail(url=getattr(member.avatar, "url", None) or getattr(member.default_avatar, "url", None))
        embed.add_field(
            name="Punishments Issued",
            value=f"{punishments_count}\n" +
                  "\n".join(
                      f"`{p['timestamp']}`: {p['action']} -> <@{p['target_id']}>"
                      for p in last_punishments
                  ) if last_punishments else "No punishments found.",
            inline=False,
        )
        embed.add_field(
            name="Activity",
            value=f"Text messages: {total_text}\nVoice time: {humanize_number(total_voice_minutes)} minutes",
            inline=False,
        )
        embed.add_field(
            name="Top Commands Used",
            value="\n".join(f"{cmd}: {count}" for cmd, count in top_commands) if top_commands else "No commands used.",
            inline=False,
        )
        if avg_rating is not None:
            embed.add_field(
                name="Feedback",
                value=f"Average rating: {avg_rating:.2f}/5 ({len(feedbacks)} ratings)",
                inline=False,
            )
        await ctx.send(embed=embed)

    @staff_stats.command(name="export")
    async def staff_stats_export(self, ctx, member: discord.Member = None, format: str = "csv"):
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

    # --- Subgroup: staff set (configuration) ---

    @staff.group(name="set")
    @checks.admin_or_permissions(administrator=True)
    async def staff_set(self, ctx):
        """StaffMonitor configuration."""

    @staff_set.command(name="modlog")
    async def staff_set_modlog(self, ctx, channel: discord.TextChannel = None):
        """Set the modlog channel for staff actions."""
        if channel is None:
            await self.config.guild(ctx.guild).modlog_channel.set(None)
            await ctx.send("Modlog channel unset.")
        else:
            await self.config.guild(ctx.guild).modlog_channel.set(channel.id)
            await ctx.send(f"Modlog channel set to {channel.mention}.")

    @staff_set.command(name="modchannels")
    async def staff_set_modchannels(self, ctx, *channels: discord.TextChannel):
        """Set channels considered as mod channels."""
        ids = [c.id for c in channels]
        await self.config.guild(ctx.guild).mod_channels.set(ids)
        await ctx.send(f"Mod channels set: {', '.join(c.mention for c in channels)}")

    @staff_set.command(name="staffroles")
    async def staff_set_staffroles(self, ctx, *roles: discord.Role):
        """Set staff roles for monitoring."""
        ids = [r.id for r in roles]
        await self.config.guild(ctx.guild).staff_roles.set(ids)
        await ctx.send(f"Staff roles set: {', '.join(r.mention for r in roles)}")

    @staff_set.command(name="privacyroles")
    async def staff_set_privacyroles(self, ctx, *roles: discord.Role):
        """Set roles that can view logs/reports."""
        ids = [r.id for r in roles]
        await self.config.guild(ctx.guild).privacy_roles.set(ids)
        await ctx.send(f"Privacy roles set: {', '.join(r.mention for r in roles)}")

    @staff_set.command(name="dmlogging")
    async def staff_set_dmlogging(self, ctx, enabled: bool):
        """Enable/disable DM logging for staff."""
        await self.config.guild(ctx.guild).dm_logging.set(enabled)
        await ctx.send(f"DM logging {'enabled' if enabled else 'disabled'}.")

    @staff_set.command(name="alertchannel")
    async def staff_set_alertchannel(self, ctx, channel: discord.TextChannel = None):
        """Set the alert channel for suspicious staff activity."""
        if channel is None:
            await self.config.guild(ctx.guild).alerts.alert_channel.set(None)
            await ctx.send("Alert channel unset.")
        else:
            await self.config.guild(ctx.guild).alerts.alert_channel.set(channel.id)
            await ctx.send(f"Alert channel set to {channel.mention}.")

    @staff_set.command(name="alertthresholds")
    async def staff_set_alertthresholds(self, ctx, mass_ban: int = 3, excessive_punish: int = 5):
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

    @staff.command(name="info")
    async def staff_info(self, ctx):
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
