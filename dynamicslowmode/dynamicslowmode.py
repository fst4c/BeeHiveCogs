import discord
from redbot.core import commands, Config, checks
from datetime import datetime, timezone
import asyncio
from collections import deque, defaultdict

def loop(*, seconds=0, minutes=0, hours=0):
    """A simple replacement for tasks.loop for Red 3.5+ compatibility."""
    def decorator(func):
        async def loop_runner(self, *args, **kwargs):
            await self.bot.wait_until_ready()
            while True:
                await func(self, *args, **kwargs)
                await asyncio.sleep(seconds + minutes * 60 + hours * 3600)
        func._loop_runner = loop_runner
        return func
    return decorator

class DynamicSlowmode(commands.Cog):
    """
    Dynamically adjust channel slowmode in 1-second increments based on activity to keep chat readable and moderatable.
    """

    DEFAULTS = {
        "enabled": False,
        "min_slowmode": 0,
        "max_slowmode": 120,
        "target_msgs_per_min": 20,
        "channels": [],
        "log_channel": None,
    }

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xBEEBEE01, force_registration=True)
        self.config.register_guild(**self.DEFAULTS)
        self._message_cache = defaultdict(lambda: deque(maxlen=100))
        self._lock = asyncio.Lock()
        self._slowmode_task = self.bot.loop.create_task(self._run_slowmode_task())

    def cog_unload(self):
        if hasattr(self, "_slowmode_task"):
            self._slowmode_task.cancel()

    @commands.group(aliases=["dsm"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def dynamicslowmode(self, ctx):
        """Dynamic slowmode configuration."""
        pass

    @dynamicslowmode.command()
    async def enable(self, ctx):
        """Enable dynamic slowmode for this server."""
        await self.config.guild(ctx.guild).enabled.set(True)
        embed = discord.Embed(
            title="Dynamic Slowmode",
            description="Dynamic slowmode enabled.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @dynamicslowmode.command()
    async def disable(self, ctx):
        """Disable dynamic slowmode for this server."""
        await self.config.guild(ctx.guild).enabled.set(False)
        embed = discord.Embed(
            title="Dynamic Slowmode",
            description="Dynamic slowmode disabled.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

    @dynamicslowmode.command()
    async def setmin(self, ctx, seconds: int):
        """Set minimum slowmode (in seconds)."""
        await self.config.guild(ctx.guild).min_slowmode.set(seconds)
        embed = discord.Embed(
            title="Dynamic Slowmode",
            description=f"Minimum slowmode set to {seconds} seconds.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @dynamicslowmode.command()
    async def setmax(self, ctx, seconds: int):
        """Set maximum slowmode (in seconds)."""
        await self.config.guild(ctx.guild).max_slowmode.set(seconds)
        embed = discord.Embed(
            title="Dynamic Slowmode",
            description=f"Maximum slowmode set to {seconds} seconds.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @dynamicslowmode.command()
    async def settarget(self, ctx, msgs_per_min: int):
        """Set target messages per minute for a channel."""
        await self.config.guild(ctx.guild).target_msgs_per_min.set(msgs_per_min)
        embed = discord.Embed(
            title="Dynamic Slowmode",
            description=f"Target messages per minute set to {msgs_per_min}.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @dynamicslowmode.command()
    async def addchannel(self, ctx, channel: discord.TextChannel):
        """Add a channel to dynamic slowmode."""
        async with self.config.guild(ctx.guild).channels() as chans:
            if channel.id not in chans:
                chans.append(channel.id)
        embed = discord.Embed(
            title="Dynamic Slowmode",
            description=f"{channel.mention} added to dynamic slowmode.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @dynamicslowmode.command()
    async def removechannel(self, ctx, channel: discord.TextChannel):
        """Remove a channel from dynamic slowmode."""
        async with self.config.guild(ctx.guild).channels() as chans:
            if channel.id in chans:
                chans.remove(channel.id)
        embed = discord.Embed(
            title="Dynamic Slowmode",
            description=f"{channel.mention} removed from dynamic slowmode.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

    @dynamicslowmode.command(name="settings")
    async def _settings(self, ctx):
        """Show current dynamic slowmode settings and status."""
        conf = await self.config.guild(ctx.guild).all()
        enabled = conf["enabled"]
        min_slowmode = conf["min_slowmode"]
        max_slowmode = conf["max_slowmode"]
        target_mpm = conf["target_msgs_per_min"]
        log_channel_id = conf["log_channel"]
        log_channel = ctx.guild.get_channel(log_channel_id) if log_channel_id else None
        chans = conf["channels"]
        channels = [ctx.guild.get_channel(cid) for cid in chans]
        channels = [c.mention for c in channels if c]

        embed = discord.Embed(
            title="Dynamic Slowmode Settings",
            color=discord.Color.blue()
        )
        embed.add_field(name="Enabled", value=str(enabled), inline=False)
        embed.add_field(name="Min Slowmode", value=f"{min_slowmode} seconds", inline=True)
        embed.add_field(name="Max Slowmode", value=f"{max_slowmode} seconds", inline=True)
        embed.add_field(name="Target Messages/Minute", value=str(target_mpm), inline=True)
        embed.add_field(name="Log Channel", value=log_channel.mention if log_channel else "None", inline=False)
        embed.add_field(name="Channels", value="\n".join(channels) if channels else "No channels set", inline=False)

        await ctx.send(embed=embed)

    @dynamicslowmode.command()
    async def logs(self, ctx, channel: discord.TextChannel = None):
        """
        Set the logging channel for dynamic slowmode events.
        Use without a channel to clear the log channel.
        """
        if channel is None:
            await self.config.guild(ctx.guild).log_channel.set(None)
            embed = discord.Embed(
                title="Dynamic Slowmode",
                description="Dynamic slowmode log channel cleared.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
        else:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            embed = discord.Embed(
                title="Dynamic Slowmode",
                description=f"Dynamic slowmode log channel set to {channel.mention}.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)

    async def _send_log(self, guild: discord.Guild, message: str):
        log_channel_id = await self.config.guild(guild).log_channel()
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel and log_channel.permissions_for(guild.me).send_messages:
                try:
                    embed = discord.Embed(
                        title="Dynamic slowmode",
                        description=message,
                        color=discord.Color.green()
                    )
                    await log_channel.send(embed=embed)
                except Exception as e:
                    print(f"Failed to send log message: {e}")

    @dynamicslowmode.command()
    async def survey(self, ctx, channel: discord.TextChannel = None):
        """
        Calibrate dynamic slowmode for a channel by measuring 5 minutes of activity.
        Sets the target messages per minute and suggests min/max slowmode.
        """
        channel = channel or ctx.channel
        embed = discord.Embed(
            title="Dynamic Slowmode Survey",
            description=f"🕒 Survey started for {channel.mention}. I'll be back soon with results...",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

        # Record the start time and count messages after
        start_time = datetime.now(timezone.utc)
        msg_count_5min = 0

        def check(m):
            return m.channel == channel and m.created_at >= start_time

        try:
            while (datetime.now(timezone.utc) - start_time).total_seconds() < 300:
                msg = await self.bot.wait_for('message', timeout=300, check=check)
                if msg:
                    msg_count_5min += 1
        except asyncio.TimeoutError:
            pass

        # Calculate messages per minute
        msgs_per_min = msg_count_5min / 5

        # Suggest min/max slowmode based on activity
        if msgs_per_min > 60:
            min_slow = 2
            max_slow = 10
        elif msgs_per_min > 20:
            min_slow = 0
            max_slow = 10
        else:
            min_slow = 0
            max_slow = 5

        # Save these as the new config for the guild
        await self.config.guild(ctx.guild).target_msgs_per_min.set(int(msgs_per_min))
        await self.config.guild(ctx.guild).min_slowmode.set(min_slow)
        await self.config.guild(ctx.guild).max_slowmode.set(max_slow)
        async with self.config.guild(ctx.guild).channels() as chans:
            if channel.id not in chans:
                chans.append(channel.id)

        survey_result = (
            f"✅ Calibration complete for {channel.mention}!\n"
            f"Messages in last 5 minutes: **{msg_count_5min}**\n"
            f"Average messages/minute: **{msgs_per_min:.2f}**\n"
            f"Set target messages/minute to **{int(msgs_per_min)}**\n"
            f"Set min slowmode to **{min_slow}**s, max slowmode to **{max_slow}**s.\n"
            f"{channel.mention} is now enabled for dynamic slowmode."
        )

        embed = discord.Embed(
            title="Dynamic Slowmode Survey Results",
            description=survey_result,
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        await self._send_log(ctx.guild, f"[DynamicSlowmode] Survey results for {channel.mention}:\n{survey_result}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        guild = message.guild
        conf = await self.config.guild(guild).all()
        if not conf["enabled"]:
            return
        if message.channel.id not in conf["channels"]:
            return
        now = datetime.now(timezone.utc)
        async with self._lock:
            self._message_cache[message.channel.id].append(now)

    async def _run_slowmode_task(self):
        await self.bot.wait_until_ready()
        while True:
            await self.slowmode_task()
            await asyncio.sleep(60)

    async def slowmode_task(self):
        for guild in self.bot.guilds:
            conf = await self.config.guild(guild).all()
            if not conf["enabled"]:
                continue
            min_slow = conf["min_slowmode"]
            max_slow = conf["max_slowmode"]
            target_mpm = conf["target_msgs_per_min"]
            for cid in conf["channels"]:
                channel = guild.get_channel(cid)
                if not channel or not isinstance(channel, discord.TextChannel):
                    continue
                async with self._lock:
                    now = datetime.now(timezone.utc)
                    # Remove messages older than 60 seconds
                    cache = self._message_cache[cid]
                    while cache and (now - cache[0]).total_seconds() > 60:
                        cache.popleft()
                    msg_count = len(cache)
                # Calculate new slowmode in 1-second increments
                current = channel.slowmode_delay
                if msg_count > target_mpm:
                    # Too fast, increase slowmode by 1 second
                    new_slowmode = min(current + 1, max_slow)
                elif msg_count < target_mpm // 2:
                    # Too slow, decrease slowmode by 1 second
                    new_slowmode = max(current - 1, min_slow)
                else:
                    # Within target, keep current
                    new_slowmode = current
                if new_slowmode != current:
                    try:
                        await channel.edit(slowmode_delay=new_slowmode, reason="Dynamic slowmode adjustment")
                        # Log the slowmode adjustment
                        log_msg = (
                            f"[DynamicSlowmode] Slowmode for {channel.mention} adjusted: "
                            f"{current}s → {new_slowmode}s "
                            f"(messages in last 60s: {msg_count}, target: {target_mpm})"
                        )
                        await self._send_log(guild, log_msg)
                    except discord.Forbidden:
                        print(f"Permission error: Cannot adjust slowmode for {channel.mention}.")
                    except discord.HTTPException as e:
                        print(f"HTTP error: Failed to adjust slowmode for {channel.mention}: {e}")
                    except Exception as e:
                        print(f"Unexpected error: Failed to adjust slowmode for {channel.mention}: {e}")
                else:
                    # Log the current message rate vs slowmode trigger rate
                    log_msg = (
                        f"[DynamicSlowmode] Monitoring {channel.mention}: "
                        f"Current message rate: {msg_count} messages/60s, "
                        f"Target message rate: {target_mpm} messages/60s."
                    )
                    await self._send_log(guild, log_msg)

