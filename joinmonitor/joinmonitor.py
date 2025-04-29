import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from datetime import datetime, timedelta, timezone
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
        """
        Join Monitor configuration commands.

        Use subcommands to configure join alerts, alert criteria, and surge detection.

        Example:
            `[p]joinmonitor alerts #channel`
            `[p]joinmonitor criteria min_account_age_days=2 flag_default_avatar=true`
            `[p]joinmonitor surge threshold=10 interval=60 level=high cooldown=600 enabled=true`
        """

    @joinmonitor.command()
    async def alerts(self, ctx, channel: discord.TextChannel = None):
        """
        Set or clear the channel for join alerts.

        If a channel is provided, join alerts will be sent there.
        If no channel is provided, join alerts will be disabled.

        **Examples:**
            `[p]joinmonitor alerts #alerts`
            `[p]joinmonitor alerts` (to clear)
        """
        await self.config.guild(ctx.guild).alerts_channel.set(channel.id if channel else None)
        if channel:
            await ctx.send(f"Alerts channel set to {channel.mention}.")
        else:
            await ctx.send("Alerts channel cleared.")

    @joinmonitor.command(name="criteria")
    async def criteria(self, ctx, *, criteria: str):
        """
        Set alert criteria for join alerts.

        This command allows you to specify which criteria should trigger a join alert.
        You can set multiple criteria at once, separated by spaces.

        **Available criteria:**
        - `min_account_age_days` (int): Minimum account age in days required to not trigger an alert. Default: 3
        - `flag_default_avatar` (bool): Flag users with the default avatar. Default: True
        - `flag_no_badges` (bool): Flag users with no Discord profile badges. Default: True
        - `flag_no_nitro` (bool): Flag users who do not have Nitro. Default: False
        - `flag_spammer` (bool): Flag users marked as "spammer" by Discord. Default: True

        **Boolean values** can be set as: true/false, yes/no, on/off

        **Examples:**
            `[p]joinmonitor criteria min_account_age_days=2 flag_default_avatar=true`
            `[p]joinmonitor criteria flag_no_badges=false`
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
    async def surge(
        self,
        ctx,
        threshold: int = None,
        interval: int = None,
        level: str = None,
        cooldown: int = None,
        enabled: bool = None,
    ):
        """
        Configure join surge detection.

        This command allows you to configure how the bot detects and responds to a surge of new joins.

        **Parameters:**
        - `threshold` (int): Number of joins within the interval to trigger a surge. (Default: 5)
        - `interval` (int): Time window in seconds to count joins for surge detection. (Default: 30)
        - `level` (str): Verification level to set during a surge. One of: none, low, medium, high, very_high. (Default: high)
        - `cooldown` (int): How long (in seconds) to keep the higher verification level after a surge. (Default: 300)
        - `enabled` (bool): Enable or disable surge detection. (Default: True)

        You can set one or more parameters at a time.

        **Examples:**
            `[p]joinmonitor surge threshold=10 interval=60`
            `[p]joinmonitor surge level=very_high cooldown=600 enabled=false`
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
        """
        Listener for member joins.

        Evaluates the new member against alert criteria and sends an alert if needed.
        Also checks for join surges and raises verification level if a surge is detected.
        """
        guild = member.guild
        conf = self.config.guild(guild)
        alert_criteria = await conf.alert_criteria()
        alerts_channel_id = await conf.alerts_channel()
        surge_conf = await conf.surge()
        join_timestamps = await conf.join_timestamps()

        now = datetime.now(timezone.utc).timestamp()
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
        account_age = (datetime.now(timezone.utc) - member.created_at).days
        if account_age < min_age:
            reasons.append(f"Account age: {account_age}d < {min_age}d")

        # 2. Default avatar
        if alert_criteria.get("flag_default_avatar", True):
            if member.avatar is None or member.avatar == member.default_avatar:
                reasons.append("Default avatar")

        # 3. No badges
        if alert_criteria.get("flag_no_badges", True):
            if not getattr(member, "public_flags", None) or not any(flag for flag in member.public_flags if flag):
                reasons.append("No badges")
            else:
                # Check for booster or nitro badges
                if not (member.premium_since or member.public_flags.booster or member.public_flags.nitro):
                    reasons.append("No booster or nitro badges")

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
                    title="Suspicious account joined the server",
                    description=f"{member.mention} (`{member.id}`) joined.",
                    color=0xff9144,
                    timestamp=datetime.now(timezone.utc)
                )
                embed.add_field(name="Flags", value="\n".join(reasons), inline=False)
                embed.add_field(name="Account created", value=f"<t:{int(member.created_at.timestamp())}:F>")
                embed.set_thumbnail(url=member.display_avatar.url if member.display_avatar else discord.Embed.Empty)
                await channel.send(embed=embed)

    async def _handle_surge(self, guild, conf, surge_conf, now_ts):
        """
        Internal: Handles raising the verification level during a join surge and notifying the alert channel.
        """
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
        """
        Internal: Lowers the verification level after the surge cooldown and notifies the alert channel.
        """
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
