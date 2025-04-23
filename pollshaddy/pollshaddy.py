import discord
from redbot.core import commands, Config
import asyncio

class PollShaddy(commands.Cog):
    """
    Vote automatically on Polldaddy.com
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "poll_uid": "",
            "poll": "",
            "selection": "",
            "name": "",
            "referer": "",
            "version": "124",  # Default Chrome version
            "enabled": False,
            "interval": 5,  # seconds between votes
            "vote_tracking_channel": None,  # Channel ID for vote tracking
        }
        self.config.register_guild(**default_guild)
        self._task = None
        self._announce_task = None

    async def cog_load(self):
        self._task = self.bot.loop.create_task(self.vote_loop())
        self._announce_task = self.bot.loop.create_task(self.announce_votes_loop())

    async def cog_unload(self):
        if self._task:
            self._task.cancel()
        if self._announce_task:
            self._announce_task.cancel()

    async def vote_loop(self):
        await self.bot.wait_until_ready()
        while True:
            for guild in self.bot.guilds:
                conf = await self.config.guild(guild).all()
                if not conf["enabled"]:
                    continue
                try:
                    vote_info = {
                        "poll_uid": conf["poll_uid"],
                        "poll": conf["poll"],
                        "selection": conf["selection"],
                        "name": conf["name"],
                        "referer": conf["referer"],
                    }
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/'
                        f'{conf["version"]}.0.0.0 Safari/537.36'
                    }
                    cookie = await asyncio.get_event_loop().run_in_executor(
                        None, get_cookie, COOKIE_URL, vote_info, headers
                    )
                    tally = await asyncio.get_event_loop().run_in_executor(
                        None, cast_vote, POLL_URL, vote_info, cookie, headers
                    )
                    # Optionally, you could store tally in config or send to a channel
                except Exception as e:
                    # Optionally log error
                    pass
                await asyncio.sleep(conf.get("interval", 5))
            await asyncio.sleep(1)

    async def announce_votes_loop(self):
        await self.bot.wait_until_ready()
        while True:
            for guild in self.bot.guilds:
                conf = await self.config.guild(guild).all()
                if not conf["enabled"]:
                    continue
                channel_id = conf.get("vote_tracking_channel")
                if not channel_id:
                    continue
                channel = guild.get_channel(channel_id)
                if not channel:
                    continue
                try:
                    # Fetch the latest tally for the poll
                    vote_info = {
                        "poll_uid": conf["poll_uid"],
                        "poll": conf["poll"],
                        "selection": conf["selection"],
                        "name": conf["name"],
                        "referer": conf["referer"],
                    }
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/'
                        f'{conf["version"]}.0.0.0 Safari/537.36'
                    }
                    # We assume get_poll_tally returns a dict of {option_id: {"count": int, "percent": float, "name": str}}
                    # and that conf["selection"] is the option_id we want to track
                    tally = await asyncio.get_event_loop().run_in_executor(
                        None, get_poll_tally, POLL_URL, vote_info, headers
                    )
                    selection_id = conf["selection"]
                    selection_name = conf["name"] or selection_id
                    if tally and selection_id in tally:
                        count = tally[selection_id].get("count", 0)
                        percent = tally[selection_id].get("percent", 0.0)
                        await channel.send(
                            f"🗳️ **Vote Update for '{selection_name}':**\n"
                            f"Votes: **{count}**\n"
                            f"Percent: **{percent:.2f}%**"
                        )
                except Exception as e:
                    # Optionally log error
                    pass
            await asyncio.sleep(30)

    @commands.group()
    @commands.guild_only()
    async def pollshaddy(self, ctx):
        """Polldaddy voting automation settings."""
        pass

    @pollshaddy.command()
    async def set_poll_uid(self, ctx, poll_uid: str):
        """Set the poll UID."""
        await self.config.guild(ctx.guild).poll_uid.set(poll_uid)
        await ctx.send("Poll UID set.")

    @pollshaddy.command()
    async def set_poll(self, ctx, poll: str):
        """Set the poll ID."""
        await self.config.guild(ctx.guild).poll.set(poll)
        await ctx.send("Poll ID set.")

    @pollshaddy.command()
    async def set_selection(self, ctx, selection: str):
        """Set the selection (option ID)."""
        await self.config.guild(ctx.guild).selection.set(selection)
        await ctx.send("Selection set.")

    @pollshaddy.command()
    async def set_name(self, ctx, *, name: str):
        """Set the name of the option to vote for."""
        await self.config.guild(ctx.guild).name.set(name)
        await ctx.send("Option name set.")

    @pollshaddy.command()
    async def set_referer(self, ctx, *, referer: str):
        """Set the referer URL."""
        await self.config.guild(ctx.guild).referer.set(referer)
        await ctx.send("Referer set.")

    @pollshaddy.command()
    async def set_version(self, ctx, version: str):
        """Set the Chrome version for the User-Agent."""
        await self.config.guild(ctx.guild).version.set(version)
        await ctx.send("Chrome version set.")

    @pollshaddy.command()
    async def set_interval(self, ctx, seconds: int):
        """Set the interval (in seconds) between votes."""
        if seconds < 1:
            await ctx.send("Interval must be at least 1 second.")
            return
        await self.config.guild(ctx.guild).interval.set(seconds)
        await ctx.send(f"Interval set to {seconds} seconds.")

    @pollshaddy.command()
    async def set_vote_tracking_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for vote tracking announcements."""
        await self.config.guild(ctx.guild).vote_tracking_channel.set(channel.id)
        await ctx.send(f"Vote tracking channel set to {channel.mention}.")

    @pollshaddy.command()
    async def clear_vote_tracking_channel(self, ctx):
        """Clear the vote tracking channel."""
        await self.config.guild(ctx.guild).vote_tracking_channel.set(None)
        await ctx.send("Vote tracking channel cleared.")

    @pollshaddy.command()
    async def enable(self, ctx):
        """Enable automatic voting."""
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("Automatic voting enabled.")

    @pollshaddy.command()
    async def disable(self, ctx):
        """Disable automatic voting."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Automatic voting disabled.")

    @pollshaddy.command()
    async def status(self, ctx):
        """Show current voting configuration."""
        conf = await self.config.guild(ctx.guild).all()
        channel = None
        if conf.get("vote_tracking_channel"):
            channel = ctx.guild.get_channel(conf["vote_tracking_channel"])
        msg = (
            f"**Poll UID:** {conf['poll_uid']}\n"
            f"**Poll ID:** {conf['poll']}\n"
            f"**Selection:** {conf['selection']}\n"
            f"**Name:** {conf['name']}\n"
            f"**Referer:** {conf['referer']}\n"
            f"**Chrome Version:** {conf['version']}\n"
            f"**Interval:** {conf['interval']} seconds\n"
            f"**Enabled:** {conf['enabled']}\n"
            f"**Vote Tracking Channel:** {channel.mention if channel else 'Not set'}"
        )
        await ctx.send(msg)
