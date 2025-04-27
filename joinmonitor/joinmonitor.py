import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from datetime import datetime, timedelta
import asyncio

class JoinMonitor(commands.Cog):
    """
    Monitors user joins, applies alert criteria, and responds to join surges.
    """

    __version__ = "1.0.0"

    DEFAULT_GUILD = {
        "alerts_channel": None,
        "alert_criteria": {
            "min_account_age_days": 3,
            "flag_default_avatar": True,
            "flag_no_badges": True,
            "flag_no_nitro": False,
            "flag_spammer": True,
        },
        "surge": {
            "enabled": True,
            "threshold": 5,  # Number of joins
            "interval_seconds": 30,  # In this many seconds
            "verification_level": "high",  # Level to set during surge
            "cooldown_seconds": 300,  # How long to keep the higher level
        },
        "last_verification_level": None,
        "surge_active_until": None,
        "join_timestamps": [],
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xAABBCCDD)
        self.config.register_guild(**self.DEFAULT_GUILD)
        self._surge_tasks = {}

    def cog_unload(self):
        for task in self._surge_tasks.values():
            task.cancel()

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        # No per-user data stored
        return

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def joinmonitor(self, ctx):
        """Join Monitor configuration commands."""

    @joinmonitor.command()
    async def setalertschannel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel for join alerts."""
        await self.config.guild(ctx.guild).alerts_channel.set(channel.id if channel else None)
        if channel:
            await ctx.send(f"Alerts channel set to {channel.mention}.")
        else:
            await ctx.send("Alerts channel cleared.")

    @joinmonitor.command()
    async def setcriteria(self, ctx, *, criteria: str):
        """
        Set alert criteria. Example: min_account_age_days=2 flag_default_avatar=true
        """
        current = await self.config.guild(ctx.guild).alert_criteria()
        updates = {}
        for part in criteria.split():
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = key.strip()
            value = value.strip().lower()
            if key in current:
                if value in ("true", "yes", "on"):
                    updates[key] = True
                elif value in ("false", "no", "off"):
                    updates[key] = False
                else:
                    try:
                        updates[key] = int(value)
                    except ValueError:
                        continue
        current.update(updates)
        await self.config.guild(ctx.guild).alert_criteria.set(current)
        await ctx.send(f"Updated alert criteria: `{current}`")

    @joinmonitor.command()
    async def setsurge(self, ctx, threshold: int = None, interval: int = None, level: str = None, cooldown: int = None, enabled: bool = None):
        """
        Configure join surge detection.
        """
        surge = await self.config.guild(ctx.guild).surge()
        if threshold is not None:
            surge["threshold"] = threshold
        if interval is not None:
            surge["interval_seconds"] = interval
        if level is not None:
            if level.lower() in ("none", "low", "medium", "high", "very_high"):
                surge["verification_level"] = level.lower()
            else:
                await ctx.send("Invalid verification level. Choose from: none, low, medium, high, very_high.")
                return
        if cooldown is not None:
            surge["cooldown_seconds"] = cooldown
        if enabled is not None:
            surge["enabled"] = enabled
        await self.config.guild(ctx.guild).surge.set(surge)
        await ctx.send(f"Surge config updated: `{surge}`")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        conf = self.config.guild(guild)
        alert_criteria = await conf.alert_criteria()
        alerts_channel_id = await conf.alerts_channel()
        surge_conf = await conf.surge()
        join_timestamps = await conf.join_timestamps()

        now = datetime.utcnow().timestamp()
        join_timestamps.append(now)
        # Keep only recent joins within the interval
        interval = surge_conf.get("interval_seconds", 30)
        join_timestamps = [ts for ts in join_timestamps if now - ts <= interval]
        await conf.join_timestamps.set(join_timestamps)

        # Check for surge
        if surge_conf.get("enabled", True):
            threshold = surge_conf.get("threshold", 5)
            if len(join_timestamps) >= threshold:
                await self._handle_surge(guild, conf, surge_conf, now)

        # Evaluate alert criteria
        reasons = []
        # 1. Account age
        min_age = alert_criteria.get("min_account_age_days", 3)
        account_age = (datetime.utcnow() - member.created_at).days
        if account_age < min_age:
            reasons.append(f"Account age: {account_age}d < {min_age}d")

        # 2. Default avatar
        if alert_criteria.get("flag_default_avatar", True):
            if member.avatar is None or member.avatar == member.default_avatar:
                reasons.append("Default avatar")

        # 3. No badges
        if alert_criteria.get("flag_no_badges", True):
            if not getattr(member, "public_flags", None) or not any(member.public_flags.all()):
                reasons.append("No badges")

        # 4. No Nitro
        if alert_criteria.get("flag_no_nitro", False):
            if not member.premium_since:
                reasons.append("No Nitro")

        # 5. Spammer flag (uses Discord's built-in flags)
        if alert_criteria.get("flag_spammer", True):
            if getattr(member, "flags", None) and getattr(member.flags, "verified_bot", False):
                # Not a spammer if verified bot
                pass
            elif getattr(member, "flags", None) and getattr(member.flags, "spammer", False):
                reasons.append("Spammer flag")

        if reasons and alerts_channel_id:
            channel = guild.get_channel(alerts_channel_id)
            if channel:
                embed = discord.Embed(
                    title="🚨 User Join Alert",
                    description=f"{member.mention} (`{member.id}`) joined.",
                    color=discord.Color.red(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Reasons", value="\n".join(reasons), inline=False)
                embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
                embed.set_thumbnail(url=member.display_avatar.url if member.display_avatar else discord.Embed.Empty)
                await channel.send(embed=embed)

    async def _handle_surge(self, guild, conf, surge_conf, now_ts):
        # Only act if not already in surge
        surge_active_until = await conf.surge_active_until()
        if surge_active_until and now_ts < surge_active_until:
            return
        # Save current verification level
        try:
            current_level = guild.verification_level
            await conf.last_verification_level.set(current_level.name)
            # Set new verification level
            new_level_str = surge_conf.get("verification_level", "high")
            new_level = getattr(discord.VerificationLevel, new_level_str, discord.VerificationLevel.high)
            if current_level != new_level:
                await guild.edit(verification_level=new_level, reason="JoinMonitor: Surge detected")
        except Exception:
            return  # Insufficient permissions or error

        cooldown = surge_conf.get("cooldown_seconds", 300)
        await conf.surge_active_until.set(now_ts + cooldown)
        # Schedule lowering verification level
        if guild.id in self._surge_tasks:
            self._surge_tasks[guild.id].cancel()
        self._surge_tasks[guild.id] = self.bot.loop.create_task(self._lower_verification_later(guild, conf, cooldown))

        # Alert channel
        alerts_channel_id = await conf.alerts_channel()
        if alerts_channel_id:
            channel = guild.get_channel(alerts_channel_id)
            if channel:
                await channel.send(
                    f"⚠️ **Join surge detected!** Raised verification level to `{new_level_str}` for {cooldown} seconds."
                )

    async def _lower_verification_later(self, guild, conf, cooldown):
        try:
            await asyncio.sleep(cooldown)
            last_level_str = await conf.last_verification_level()
            if last_level_str:
                last_level = getattr(discord.VerificationLevel, last_level_str, discord.VerificationLevel.medium)
                try:
                    await guild.edit(verification_level=last_level, reason="JoinMonitor: Surge cooldown ended")
                except Exception:
                    pass
            await conf.surge_active_until.set(None)
            # Alert channel
            alerts_channel_id = await conf.alerts_channel()
            if alerts_channel_id:
                channel = guild.get_channel(alerts_channel_id)
                if channel:
                    await channel.send(
                        f"✅ **Surge cooldown ended.** Restored verification level to `{last_level_str}`."
                    )
        except asyncio.CancelledError:
            pass
