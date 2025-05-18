import contextlib
import datetime
import re
from typing import List, Optional
from urllib.parse import urlparse
import aiohttp  # type: ignore
import discord  # type: ignore
from discord.ext import tasks  # type: ignore
from redbot.core import Config, commands, modlog  # type: ignore
from redbot.core.bot import Red  # type: ignore
from redbot.core.commands import Context  # type: ignore

URL_REGEX_PATTERN = re.compile(
    r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"
)

class LinkSafety(commands.Cog):
    """
    Guard users from malicious links and phishing attempts with customizable protection options.
    """

    __version__ = "1.7.0"
    __last_updated__ = "May 7th, 2025"
    __quick_notes__ = "We've added a new `timeout` punishment to automatically time a user out for a predetermined amount of time if they share a known dangerous link."

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=73836)
        self.config.register_guild(
            action="notify",
            caught=0,
            notifications=0,
            deletions=0,
            kicks=0,
            bans=0,
            timeouts=0,
            last_updated=None,
            vendor_server_id=None,
            log_channel=None,
            timeout_duration=30,  # Default timeout duration in minutes
        )
        self.config.register_member(caught=0)
        self.session = aiohttp.ClientSession()
        self.bot.loop.create_task(self.get_phishing_domains())
        self.domains = []

    def cog_unload(self):
        self.bot.loop.create_task(self.session.close())

    async def red_delete_data_for_user(self, **kwargs):
        return

    def format_help_for_context(self, ctx: Context) -> str:
        pre_processed = super().format_help_for_context(ctx)
        return f"{pre_processed}\n\nVersion {self.__version__}"

    def extract_urls(self, message: str) -> List[str]:
        """
        Extract URLs from a message.
        """
        matches = URL_REGEX_PATTERN.findall(message)
        urls = [match[0] for match in matches]
        return urls

    def get_links(self, message: str) -> Optional[List[str]]:
        """
        Get links from the message content.
        """
        zero_width_chars = ["\u200b", "\u200c", "\u200d", "\u2060", "\uFEFF"]
        for char in zero_width_chars:
            message = message.replace(char, "")
        if message:
            links = self.extract_urls(message)
            if links:
                return list(set(links))
        return None


    @commands.group()
    @commands.guild_only()
    async def linksafety(self, ctx: Context):
        """
        Scan links sent in your server's chats automatically to see if they're known malicious or not

        [Check the docs to learn more](<https://sentri.beehive.systems/features/link-scanning>)
        """

    @commands.admin_or_permissions()
    @linksafety.command()
    async def vendor(self, ctx: Context, server_id: int):
        """
        Specify the ID of a server to send phishing alerts to if the bot is in more than one server.
        """
        server = self.bot.get_guild(server_id)
        if not server:
            embed = discord.Embed(
                title='Invalid Server ID',
                description="The provided server ID is invalid or the bot is not in that server.",
                colour=0xff4545,
            )
            await ctx.send(embed=embed)
            return

        await self.config.guild(ctx.guild).vendor_server_id.set(server_id)
        embed = discord.Embed(
            title='Vendor Set',
            description=f"Phishing alerts will be sent to the server **{server.name}**.",
            colour=0x2bbd8e,
        )
        await ctx.send(embed=embed)

    @commands.admin_or_permissions()    
    @linksafety.command()
    async def settings(self, ctx: Context):
        """
        Show the current antiphishing settings.
        """
        guild_data = await self.config.guild(ctx.guild).all()
        vendor_server_id = guild_data.get('vendor_server_id', None)
        log_channel_id = guild_data.get('log_channel', None)
        timeout_duration = guild_data.get('timeout_duration', 30)
        vendor_status = "Not connected"
        if vendor_server_id:
            vendor_server = self.bot.get_guild(vendor_server_id)
            vendor_status = vendor_server.name if vendor_server else "Unknown Server"

        log_channel_status = f"<#{log_channel_id}>" if log_channel_id else "Not Set"

        embed = discord.Embed(
            title='Current settings',
            colour=0xfffffe,
        )
        embed.add_field(name="Action", value=f"{guild_data.get('action', 'Not set').title()}", inline=False)
        embed.add_field(name="Security vendor", value=vendor_status, inline=False)
        embed.add_field(name="Log channel", value=log_channel_status, inline=False)
        embed.add_field(name="Timeout duration", value=f"{timeout_duration} minute(s)", inline=False)
        await ctx.send(embed=embed)

    @commands.admin_or_permissions()
    @linksafety.command()
    async def action(self, ctx: Context, action: str):
        """
        Choose the action that occurs when a user sends a phishing scam.

        Options:
        **`ignore`** - Disables phishing protection
        **`notify`** - Alerts in channel when malicious links detected (default)
        **`delete`** - Deletes the message
        **`kick`** - Delete message and kick sender
        **`ban`** - Delete message and ban sender (recommended)
        **`timeout`** - Temporarily mute the user
        """
        valid_actions = ["ignore", "notify", "delete", "kick", "ban", "timeout"]
        if action not in valid_actions:
            embed = discord.Embed(
                title='Error: Invalid action',
                description=(
                    "You provided an invalid action. You are able to choose any of the following actions to occur when a malicious link is detected...\n\n"
                    "`ignore` - Disables phishing protection\n"
                    "`notify` - Alerts in channel when malicious links detected (default)\n"
                    "`delete` - Deletes the message\n"
                    "`kick` - Delete message and kick sender\n"
                    "`ban` - Delete message and ban sender (recommended)\n"
                    "`timeout` - Temporarily mute the user\n\n"
                    "Retry that command with one of the above options."
                ),
                colour=16729413,
            )
            await ctx.send(embed=embed)
            return

        await self.config.guild(ctx.guild).action.set(action)
        descriptions = {
            "ignore": "Phishing protection is now **disabled**. Malicious links will not trigger any actions.",
            "notify": "Malicious links will now trigger a **notification** in the channel when detected.",
            "delete": "Malicious links will now be **deleted** from conversation when detected.",
            "kick": "Malicious links will be **deleted** and the sender will be **kicked** when detected.",
            "ban": "Malicious links will be **deleted** and the sender will be **banned** when detected.",
            "timeout": "Malicious links will result in the user being **temporarily muted**."
        }
        colours = {
            "ignore": 0xffd966,  # Yellow
            "notify": 0xffd966,  # Yellow
            "delete": 0xff4545,  # Red
            "kick": 0xff4545,  # Red
            "ban": 0xff4545,  # Red
            "timeout": 0xffd966  # Yellow
        }

        description = descriptions[action]
        colour = colours[action]

        embed = discord.Embed(title='Settings changed', description=description, colour=colour)
        await ctx.send(embed=embed)

    @commands.admin_or_permissions()
    @linksafety.command()
    async def timeoutduration(self, ctx: Context, minutes: int):
        """
        Set the timeout duration (in minutes) for the timeout action.

        Example: `[p]linksafety timeoutduration 60` will timeout users for 60 minutes.
        """
        if minutes < 1 or minutes > 10080:  # 1 minute to 7 days
            await ctx.send("Timeout duration must be between 1 and 10080 minutes (7 days).")
            return
        await self.config.guild(ctx.guild).timeout_duration.set(minutes)
        await ctx.send(f"Timeout duration set to **{minutes}** minute(s).")

    @linksafety.command()
    async def stats(self, ctx: Context):
        """
        Check protection statistics for this server
        """
        caught = await self.config.guild(ctx.guild).caught()
        notifications = await self.config.guild(ctx.guild).notifications()
        deletions = await self.config.guild(ctx.guild).deletions()
        kicks = await self.config.guild(ctx.guild).kicks()
        bans = await self.config.guild(ctx.guild).bans()
        timeouts = await self.config.guild(ctx.guild).timeouts()  # Added timeout statistic retrieval
        last_updated = self.__last_updated__
        patch_notes = self.__quick_notes__
        total_domains = len(self.domains)

        s_caught = "s" if caught != 1 else ""
        s_notifications = "s" if notifications != 1 else ""
        s_deletions = "s" if deletions != 1 else ""
        s_kicks = "s" if kicks != 1 else ""
        s_bans = "s" if bans != 1 else ""
        s_timeouts = "s" if timeouts != 1 else ""  # Added pluralization for timeouts

        last_updated_str = f"{last_updated}"

        embed = discord.Embed(
            title='Link safety statistics', 
            colour=0xfffffe,
        )
        embed.add_field(name="Protection", value="", inline=False)
        embed.add_field(
            name="Detected",
            value=f"**{caught}** malicious link{s_caught}",
            inline=True
        )
        embed.add_field(
            name="Notifications",
            value=f"Warned you of danger **{notifications}** time{s_notifications}",
            inline=True
        )
        embed.add_field(
            name="Deletions",
            value=f"Removed **{deletions}** message{s_deletions}",
            inline=True
        )
        embed.add_field(
            name="Kicks",
            value=f"Kicked **{kicks}** user{s_kicks}",
            inline=True
        )
        embed.add_field(
            name="Bans",
            value=f"Banned **{bans}** user{s_bans}",
            inline=True
        )
        embed.add_field(
            name="Timeouts",
            value=f"Timed out **{timeouts}** user{s_timeouts}",  # Added timeout statistic display
            inline=True
        )
        embed.add_field(
            name="Blocklist count",
            value=f"There are **{total_domains:,}** domains on the [BeeHive](https://www.beehive.systems) blocklist",
            inline=False
        )
        embed.add_field(name="About this cog", value="", inline=False)
        embed.add_field(
            name="Version",
            value=f"You're running **v{self.__version__}**",
            inline=True
        )
        embed.add_field(name="Last updated", value=f"**{last_updated_str}**", inline=True)
        embed.add_field(name="Recent changes", value=f"*{patch_notes}*", inline=False)
        view = discord.ui.View()
        button = discord.ui.Button(label="Learn more about BeeHive", url="https://www.beehive.systems")
        view.add_item(button)
        await ctx.send(embed=embed, view=view)

    @commands.admin_or_permissions()
    @linksafety.command()
    async def logs(self, ctx: Context, channel: discord.TextChannel):
        """
        Set a logging channel
        """
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        embed = discord.Embed(
            title='Settings changed',
            description=f"The logging channel has been set to {channel.mention}.",
            colour=0x2bbd8e,
        )
        await ctx.send(embed=embed)

    @tasks.loop(minutes=2)
    async def get_phishing_domains(self) -> None:
        domains = []

        headers = {
            "X-Identity": f"BeeHive AntiPhishing v{self.__version__} (https://www.beehive.systems/)",
            "User-Agent": f"BeeHive AntiPhishing v{self.__version__} (https://www.beehive.systems/)"
        }

        async with self.session.get(
            "https://phish.sinking.yachts/v2/all", headers=headers
        ) as request:
            if request.status == 200:
                try:
                    data = await request.json()
                    domains.extend(data)
                except Exception as e:
                    print(f"Error parsing JSON from Sinking Yachts: {e}")
            else:
                print(f"Failed to fetch Sinking Yachts blacklist, status code: {request.status}")

        async with self.session.get(
            "https://www.beehive.systems/hubfs/blocklist/blocklist.json", headers=headers
        ) as request:
            if request.status == 200:
                try:
                    data = await request.json()
                    if isinstance(data, list):
                        domains.extend(data)
                    else:
                        print("Unexpected data format received from blocklist.")
                except Exception as e:
                    print(f"Error parsing JSON from blocklist: {e}")
            else:
                print(f"Failed to fetch blocklist, status code: {request.status}")
        self.domains = list(set(domains))

    async def follow_redirects(self, url: str) -> List[str]:
        """
        Follow redirects and return the final URL and any intermediate URLs.
        """
        urls = []
        headers = {
            "User-Agent": "BeeHive Security Intelligence (https://www.beehive.systems)"
        }
        try:
            async with self.session.head(url, allow_redirects=True, headers=headers) as response:
                urls.append(str(response.url))
                for history in response.history:
                    urls.append(str(history.url))
        except Exception as e:
            print(f"Error following redirects: {e}")
        return urls

    async def handle_phishing(self, message: discord.Message, domain: str, redirect_chain: List[str]) -> None:
        domain = domain[:250]
        action = await self.config.guild(message.guild).action()
        if action != "ignore":
            count = await self.config.guild(message.guild).caught()
            await self.config.guild(message.guild).caught.set(count + 1)
        member_count = await self.config.member(message.author).caught()
        await self.config.member(message.author).caught.set(member_count + 1)

        # Send URL to vendor server if set
        vendor_server_id = await self.config.guild(message.guild).vendor_server_id()
        if vendor_server_id:
            vendor_server = self.bot.get_guild(vendor_server_id)
            if vendor_server:
                vendor_channel = vendor_server.system_channel or vendor_server.text_channels[0]
                if vendor_channel:
                    redirect_chain_str = "\n".join(redirect_chain)
                    vendor_embed = discord.Embed(
                        title="Malicious URL detected",
                        description=f"A URL was detected in the server **{message.guild.name}**.",
                        color=0xffd966,
                    )
                    vendor_embed.add_field(name="User", value=message.author.mention)
                    vendor_embed.add_field(name="URL", value=domain)
                    vendor_embed.add_field(name="Redirect Chain", value=redirect_chain_str)
                    await vendor_channel.send(embed=vendor_embed)

        # Send URL to log channel if set
        log_channel_id = await self.config.guild(message.guild).log_channel()
        if log_channel_id:
            log_channel = message.guild.get_channel(log_channel_id)
            if log_channel:
                redirect_chain_str = "\n".join(redirect_chain)
                log_embed = discord.Embed(
                    title="Link safety",
                    description=f"A known dangerous website or link was detected in **{message.guild.name}**'s chat.",
                    color=0xff4545,
                )
                log_embed.add_field(name="Sender", value=message.author.mention)
                log_embed.add_field(name="Domain", value=domain)
                log_embed.add_field(name="Redirects", value=redirect_chain_str)
                await log_channel.send(embed=log_embed)

        if action == "notify":
            if message.channel.permissions_for(message.guild.me).send_messages:
                # Prevent double notification: only send notification if this is the original message, not an edit
                # We'll use a custom attribute to mark the message as already notified
                # This attribute is not persisted, but will prevent double notification in the same process
                if getattr(message, "_antiphishing_notified", False):
                    return
                setattr(message, "_antiphishing_notified", True)
                with contextlib.suppress(discord.NotFound):
                    mod_roles = await self.bot.get_mod_roles(message.guild)
                    mod_mentions = " ".join(role.mention for role in mod_roles) if mod_roles else ""

                    # Determine the status of each domain in the redirect chain
                    redirect_chain_status = []
                    for url in redirect_chain:
                        try:
                            domain = urlparse(url).netloc  # Extract domain from URL
                            status = "Malicious" if domain in self.domains else "Unknown"
                            redirect_chain_status.append(f"{url} ({status})")
                        except IndexError:
                            print(f"Error extracting domain from URL: {url}")
                            redirect_chain_status.append(f"{url} (Unknown)")

                    redirect_chain_str = "\n".join(redirect_chain_status)

                    embed = discord.Embed(
                        title="Dangerous link detected!",
                        description=(
                            f"Don't click any links in this message, and ask a staff member to remove this message for community safety.\n\n"
                            f"**Link trajectory**\n{redirect_chain_str}"
                        ),
                        color=0xff4545,
                    )
                    embed.set_thumbnail(url="https://www.beehive.systems/hubfs/Icon%20Packs/Red/warning.png")
                    embed.timestamp = datetime.datetime.utcnow()
                    if mod_mentions:
                        await message.channel.send(content=mod_mentions, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))
                    else:
                        await message.reply(embed=embed)

                notifications = await self.config.guild(message.guild).notifications()
                await self.config.guild(message.guild).notifications.set(notifications + 1)
        elif action == "delete":
            if message.channel.permissions_for(message.guild.me).manage_messages:
                with contextlib.suppress(discord.NotFound):
                    await message.delete()

                deletions = await self.config.guild(message.guild).deletions()
                await self.config.guild(message.guild).deletions.set(deletions + 1)
        elif action == "kick":
            if (
                message.channel.permissions_for(message.guild.me).kick_members
                and message.channel.permissions_for(message.guild.me).manage_messages
            ):
                with contextlib.suppress(discord.NotFound):
                    await message.delete()
                    if (
                        message.author.top_role >= message.guild.me.top_role
                        or message.author == message.guild.owner
                    ):
                        return

                    await message.author.kick()

                kicks = await self.config.guild(message.guild).kicks()
                await self.config.guild(message.guild).kicks.set(kicks + 1)
        elif action == "ban":
            if (
                message.channel.permissions_for(message.guild.me).ban_members
                and message.channel.permissions_for(message.guild.me).manage_messages
            ):
                with contextlib.suppress(discord.NotFound):
                    await message.delete()
                    if (
                        message.author.top_role >= message.guild.me.top_role
                        or message.author == message.guild.owner
                    ):
                        return

                    await message.author.ban()

                bans = await self.config.guild(message.guild).bans()
                await self.config.guild(message.guild).bans.set(bans + 1)
        elif action == "timeout":
            if message.channel.permissions_for(message.guild.me).moderate_members:
                with contextlib.suppress(discord.NotFound):
                    await message.delete()
                    if (
                        message.author.top_role >= message.guild.me.top_role
                        or message.author == message.guild.owner
                    ):
                        return

                    # Timeout the user for the configured duration
                    minutes = await self.config.guild(message.guild).timeout_duration()
                    if not isinstance(minutes, int) or minutes < 1:
                        minutes = 30  # fallback to default if not set or invalid
                    timeout_duration = datetime.timedelta(minutes=minutes)
                    await message.author.timeout_for(timeout_duration, reason="Shared a known dangerous link")

                timeouts = await self.config.guild(message.guild).timeouts()  # Retrieve current timeout count
                await self.config.guild(message.guild).timeouts.set(timeouts + 1)  # Increment timeout count

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """
        Handles the logic for checking URLs when a message is edited.
        """
        if not after.guild or after.author.bot:
            return
        if await self.bot.cog_disabled_in_guild(self, after.guild):
            return

        # Prevent double notification: only process if the message hasn't already been handled
        if getattr(after, "_antiphishing_notified", False):
            return

        links = self.get_links(after.content)
        if not links:
            return

        # Only handle the first malicious link per message to avoid double alerts
        for url in links:
            domains_to_check = await self.follow_redirects(url)
            for domain_url in domains_to_check:
                domain = urlparse(domain_url).netloc
                if domain in self.domains:
                    await self.handle_phishing(after, domain, domains_to_check)
                    return

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        """
        Handles the logic for checking URLs.
        """

        if not message.guild or message.author.bot:
            return
        if await self.bot.cog_disabled_in_guild(self, message.guild):
            return

        # Prevent double notification: only process if the message hasn't already been handled
        if getattr(message, "_antiphishing_notified", False):
            return

        links = self.get_links(message.content)
        if not links:
            return

        # Only handle the first malicious link per message to avoid double alerts
        for url in links:
            domains_to_check = await self.follow_redirects(url)
            for domain_url in domains_to_check:
                domain = urlparse(domain_url).netloc
                if domain in self.domains:
                    await self.handle_phishing(message, domain, domains_to_check)
                    return  # Stop after first malicious link to avoid double notification




