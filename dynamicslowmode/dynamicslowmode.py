import discord
from redbot.core import commands, Config, checks, tasks
from datetime import datetime, timedelta
import asyncio
from collections import deque, defaultdict

class DynamicSlowmode(commands.Cog):
    """
    Dynamically adjust channel slowmode in 1-second increments based on activity to keep chat readable and moderatable.
    """

    DEFAULTS = {
        "enabled": False,
        "min_slowmode": 0,
        "max_slowmode": 120,
        "target_msgs_per_min": 20,
        "channels": []
    }

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xBEEBEE01, force_registration=True)
        self.config.register_guild(**self.DEFAULTS)
        self._message_cache = defaultdict(lambda: deque(maxlen=100))
        self._lock = asyncio.Lock()
        self.slowmode_task.start()

    def cog_unload(self):
        self.slowmode_task.cancel()

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def dynamicslowmode(self, ctx):
        """Dynamic slowmode configuration."""
        pass

    @dynamicslowmode.command()
    async def enable(self, ctx):
        """Enable dynamic slowmode for this server."""
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("Dynamic slowmode enabled.")

    @dynamicslowmode.command()
    async def disable(self, ctx):
        """Disable dynamic slowmode for this server."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Dynamic slowmode disabled.")

    @dynamicslowmode.command()
    async def setmin(self, ctx, seconds: int):
        """Set minimum slowmode (in seconds)."""
        await self.config.guild(ctx.guild).min_slowmode.set(seconds)
        await ctx.send(f"Minimum slowmode set to {seconds} seconds.")

    @dynamicslowmode.command()
    async def setmax(self, ctx, seconds: int):
        """Set maximum slowmode (in seconds)."""
        await self.config.guild(ctx.guild).max_slowmode.set(seconds)
        await ctx.send(f"Maximum slowmode set to {seconds} seconds.")

    @dynamicslowmode.command()
    async def settarget(self, ctx, msgs_per_min: int):
        """Set target messages per minute for a channel."""
        await self.config.guild(ctx.guild).target_msgs_per_min.set(msgs_per_min)
        await ctx.send(f"Target messages per minute set to {msgs_per_min}.")

    @dynamicslowmode.command()
    async def addchannel(self, ctx, channel: discord.TextChannel):
        """Add a channel to dynamic slowmode."""
        async with self.config.guild(ctx.guild).channels() as chans:
            if channel.id not in chans:
                chans.append(channel.id)
        await ctx.send(f"{channel.mention} added to dynamic slowmode.")

    @dynamicslowmode.command()
    async def removechannel(self, ctx, channel: discord.TextChannel):
        """Remove a channel from dynamic slowmode."""
        async with self.config.guild(ctx.guild).channels() as chans:
            if channel.id in chans:
                chans.remove(channel.id)
        await ctx.send(f"{channel.mention} removed from dynamic slowmode.")

    @dynamicslowmode.command(name="list")
    async def _list(self, ctx):
        """List channels with dynamic slowmode enabled."""
        chans = await self.config.guild(ctx.guild).channels()
        if not chans:
            await ctx.send("No channels are set for dynamic slowmode.")
            return
        channels = [ctx.guild.get_channel(cid) for cid in chans]
        channels = [c.mention for c in channels if c]
        await ctx.send("Dynamic slowmode channels:\n" + "\n".join(channels))

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
        now = datetime.utcnow()
        async with self._lock:
            self._message_cache[message.channel.id].append(now)

    @tasks.loop(seconds=60)
    async def slowmode_task(self):
        await self.bot.wait_until_ready()
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
                    now = datetime.utcnow()
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
                    except Exception:
                        pass

    @slowmode_task.before_loop
    async def before_slowmode_task(self):
        await self.bot.wait_until_ready()

