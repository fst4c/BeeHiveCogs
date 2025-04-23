import discord
from redbot.core import commands, Config
import asyncio
import time
import requests
from bs4 import BeautifulSoup

COOKIE_URL = 'https://polldaddy.com/n'
POLL_URL = 'https://polls.polldaddy.com/vote-js.php'

def get_cookie(url: str, vote_info: dict, hdrs: dict) -> str:
    pollid = vote_info['poll_uid']
    pollnum = vote_info['poll']
    uri = f'{url}/{pollid}/{pollnum}?{int(time.time())}'
    try:
        req = requests.get(uri, headers=hdrs, timeout=60)
        req.raise_for_status()
    except requests.exceptions.RequestException as err:
        print(f'Failed to get cookie. Error: {err}\n {getattr(req, "text", "")}')
        raise
    end_string = req.text.index(';') - 1
    start_string = req.text.index('=') + 2
    return req.text[start_string:end_string]

def cast_vote(url: str, vote_info: dict, cookie_id: str, hdrs: dict) -> dict:
    """
    Casts a vote and returns the votes and percent for the chosen option.
    Returns: dict with keys "votes" (int) and "percent" (float)
    """
    name = vote_info['name']
    uri = (
        f"{url}?p={vote_info['poll']}&b=0&a={vote_info['selection']},&o=&va=16&cookie=0"
        f"&tags={vote_info['poll']}-src:poll-embed&n={cookie_id}&url={vote_info['referer']}"
    )
    try:
        req = requests.get(uri, headers=hdrs, timeout=60)
        req.raise_for_status()
    except requests.exceptions.RequestException as err:
        print(f'Failed to cast vote. Error: {err}\n {getattr(req, "text", "")}')
        raise
    votes = 0
    percent = 0.0
    soup = BeautifulSoup(req.text, 'lxml')
    noms = soup.find_all('li')
    for info in noms:
        if info.find('span', {'title': name}):
            try:
                votes_text = info.find('span', {'class': 'pds-feedback-votes'}).text.strip()
                space = votes_text.find(' ')
                votes = votes_text[1:space].replace(',', '').strip()
                votes = int(votes)
                pct_text = info.find('span', {'class': 'pds-feedback-per'}).text
                percent = float(pct_text.replace('%', '').strip())
            except Exception:
                print(f"Error getting proper values. Resetting votes/percent to 0")
                votes = 0
                percent = 0.0
            break
    return {"votes": votes, "percent": percent}

def get_poll_tally(url: str, vote_info: dict, hdrs: dict) -> dict:
    """
    Fetches the current tally for all options in the poll.
    Returns: dict of {option_name: {"votes": int, "percent": float}}
    """
    pollid = vote_info['poll']
    uri = f"https://poll.fm/{pollid}"
    try:
        req = requests.get(uri, headers=hdrs, timeout=60)
        req.raise_for_status()
    except requests.exceptions.RequestException as err:
        print(f'Failed to fetch poll tally. Error: {err}\n {getattr(req, "text", "")}')
        return {}
    soup = BeautifulSoup(req.text, 'lxml')
    noms = soup.find_all('li')
    tally = {}
    for info in noms:
        name_span = info.find('span', {'class': 'pds-answer-text'})
        if not name_span:
            continue
        option_name = name_span.text.strip()
        votes = 0
        percent = 0.0
        try:
            votes_text = info.find('span', {'class': 'pds-feedback-votes'}).text.strip()
            space = votes_text.find(' ')
            votes = votes_text[1:space].replace(',', '').strip()
            votes = int(votes)
            pct_text = info.find('span', {'class': 'pds-feedback-per'}).text
            percent = float(pct_text.replace('%', '').strip())
        except Exception:
            votes = 0
            percent = 0.0
        tally[option_name] = {"votes": votes, "percent": percent}
    return tally

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
            "debug": False,  # Debug mode for sending every vote result
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
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, cast_vote, POLL_URL, vote_info, cookie, headers
                    )
                    # Debug mode: send every vote result to the vote tracking channel if enabled
                    if conf.get("debug", False):
                        channel_id = conf.get("vote_tracking_channel")
                        if channel_id:
                            # Try both get_channel and fetch_channel for reliability
                            channel = guild.get_channel(channel_id)
                            if channel is None:
                                try:
                                    channel = await self.bot.fetch_channel(channel_id)
                                except Exception as fetch_exc:
                                    print(f"Failed to fetch channel {channel_id}: {fetch_exc}")
                                    channel = None
                            if channel:
                                selection_name = conf["name"] or conf["selection"]
                                count = result.get("votes", 0)
                                percent = result.get("percent", 0.0)
                                try:
                                    await channel.send(
                                        f"🛠️ **[DEBUG] Vote Cast for '{selection_name}':**\n"
                                        f"Votes: **{count}**\n"
                                        f"Percent: **{percent:.2f}%**"
                                    )
                                except Exception as send_exc:
                                    print(f"Failed to send debug message: {send_exc}")
                            else:
                                print(f"Debug: Could not find channel with ID {channel_id} in guild {guild.name} ({guild.id})")
                        else:
                            print(f"Debug: No vote_tracking_channel set for guild {guild.name} ({guild.id})")
                except Exception as e:
                    print(f"Exception in vote_loop for guild {guild.name} ({guild.id}): {e}")
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
                # Try both get_channel and fetch_channel for reliability
                channel = guild.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(channel_id)
                    except Exception as fetch_exc:
                        print(f"Failed to fetch channel {channel_id}: {fetch_exc}")
                        channel = None
                if not channel:
                    print(f"Announce: Could not find channel with ID {channel_id} in guild {guild.name} ({guild.id})")
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
                    # Get the current tally for the configured option
                    tally = await asyncio.get_event_loop().run_in_executor(
                        None, get_poll_tally, POLL_URL, vote_info, headers
                    )
                    selection_name = conf["name"]
                    if not selection_name:
                        selection_name = conf["selection"]
                    # Try to find the tally for the configured option name
                    option_tally = None
                    for opt_name, data in tally.items():
                        if opt_name == selection_name:
                            option_tally = data
                            break
                    if option_tally:
                        count = option_tally.get("votes", 0)
                        percent = option_tally.get("percent", 0.0)
                        try:
                            await channel.send(
                                f"🗳️ **Vote Update for '{selection_name}':**\n"
                                f"Votes: **{count}**\n"
                                f"Percent: **{percent:.2f}%**"
                            )
                        except Exception as send_exc:
                            print(f"Failed to send announce message: {send_exc}")
                    else:
                        print(f"Announce: Could not find tally for option '{selection_name}' in guild {guild.name} ({guild.id})")
                except Exception as e:
                    print(f"Exception in announce_votes_loop for guild {guild.name} ({guild.id}): {e}")
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
    async def debug(self, ctx, enabled: bool = None):
        """
        Enable or disable debug mode (send every vote result to the vote tracking channel).
        Usage: [p]pollshaddy debug [true|false]
        """
        if enabled is None:
            conf = await self.config.guild(ctx.guild).all()
            await ctx.send(f"Debug mode is currently **{'enabled' if conf.get('debug', False) else 'disabled'}**.")
            return
        await self.config.guild(ctx.guild).debug.set(enabled)
        await ctx.send(f"Debug mode {'enabled' if enabled else 'disabled'}.")

    @pollshaddy.command()
    async def status(self, ctx):
        """Show current voting configuration."""
        conf = await self.config.guild(ctx.guild).all()
        channel = None
        if conf.get("vote_tracking_channel"):
            channel = ctx.guild.get_channel(conf["vote_tracking_channel"])
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(conf["vote_tracking_channel"])
                except Exception as fetch_exc:
                    print(f"Failed to fetch channel {conf['vote_tracking_channel']}: {fetch_exc}")
                    channel = None
        msg = (
            f"**Poll UID:** {conf['poll_uid']}\n"
            f"**Poll ID:** {conf['poll']}\n"
            f"**Selection:** {conf['selection']}\n"
            f"**Name:** {conf['name']}\n"
            f"**Referer:** {conf['referer']}\n"
            f"**Chrome Version:** {conf['version']}\n"
            f"**Interval:** {conf['interval']} seconds\n"
            f"**Enabled:** {conf['enabled']}\n"
            f"**Vote Tracking Channel:** {channel.mention if channel else 'Not set'}\n"
            f"**Debug Mode:** {'Enabled' if conf.get('debug', False) else 'Disabled'}"
        )
        await ctx.send(msg)
