import discord
from redbot.core import commands, Config, checks
import asyncio
import re
import time
from collections import defaultdict, deque
from difflib import SequenceMatcher

class AntiSpam(commands.Cog):
    """
    Heuristic-based anti-spam cog for Red-DiscordBot.
    Detects and mitigates message spam, flooding, copypasta, ascii art, and more.
    """

    __author__ = "aikaterna, max, etc"
    __version__ = "1.0.0"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xA17A5A9A)
        default_guild = {
            "enabled": True,
            "message_limit": 6,
            "interval": 7,
            "similarity_threshold": 0.85,
            "ascii_art_threshold": 12,
            "ascii_art_min_lines": 6,
            "punishment": "timeout",  # timeout, kick, ban, none
            "timeout_time": 60,  # seconds
            "ignored_channels": [],
            "ignored_roles": [],
            "ignored_users": [],
            "log_channel": None,
        }
        self.config.register_guild(**default_guild)
        self.user_message_cache = defaultdict(lambda: deque(maxlen=15))  # user_id: deque of (timestamp, content)
        self.user_last_action = {}  # user_id: timestamp of last punishment

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        # No persistent user data
        pass

    @commands.group(name="antispam", invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def antispam(self, ctx):
        """AntiSpam commands."""
        await ctx.send_help()

    @antispam.command()
    async def enable(self, ctx):
        """Enable AntiSpam in this server."""
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("AntiSpam enabled.")

    @antispam.command()
    async def disable(self, ctx):
        """Disable AntiSpam in this server."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("AntiSpam disabled.")

    @antispam.command()
    async def setlimit(self, ctx, messages: int, seconds: int):
        """Set message limit and interval (messages, seconds)."""
        await self.config.guild(ctx.guild).message_limit.set(messages)
        await self.config.guild(ctx.guild).interval.set(seconds)
        await ctx.send(f"Set to {messages} messages per {seconds} seconds.")

    @antispam.command()
    async def setpunishment(self, ctx, punishment: str):
        """Set punishment: timeout, kick, ban, none."""
        punishment = punishment.lower()
        if punishment not in ("timeout", "kick", "ban", "none"):
            await ctx.send("Invalid punishment. Choose from: timeout, kick, ban, none.")
            return
        await self.config.guild(ctx.guild).punishment.set(punishment)
        await ctx.send(f"Punishment set to: {punishment}")

    @antispam.command()
    async def settimeouttime(self, ctx, seconds: int):
        """Set timeout duration in seconds."""
        await self.config.guild(ctx.guild).timeout_time.set(seconds)
        await ctx.send(f"Timeout time set to {seconds} seconds.")

    @antispam.command(name="setlogchannel")
    async def set_log_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel where antispam logs are sent. Use without argument to clear."""
        if channel is None:
            await self.config.guild(ctx.guild).log_channel.set(None)
            await ctx.send("Antispam log channel cleared.")
        else:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            await ctx.send(f"Antispam log channel set to {channel.mention}.")

    @antispam.group(name="whitelist")
    async def whitelist(self, ctx):
        """Whitelist channels, roles, or users from antispam."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @whitelist.command(name="channel")
    async def whitelist_channel(self, ctx, channel: discord.TextChannel):
        """Whitelist a channel from antispam."""
        async with self.config.guild(ctx.guild).ignored_channels() as chans:
            if channel.id not in chans:
                chans.append(channel.id)
        await ctx.send(f"Whitelisted channel: {channel.mention}")

    @whitelist.command(name="removechannel")
    async def unwhitelist_channel(self, ctx, channel: discord.TextChannel):
        """Remove a channel from the whitelist."""
        async with self.config.guild(ctx.guild).ignored_channels() as chans:
            if channel.id in chans:
                chans.remove(channel.id)
        await ctx.send(f"Removed channel from whitelist: {channel.mention}")

    @whitelist.command(name="role")
    async def whitelist_role(self, ctx, role: discord.Role):
        """Whitelist a role from antispam."""
        async with self.config.guild(ctx.guild).ignored_roles() as roles:
            if role.id not in roles:
                roles.append(role.id)
        await ctx.send(f"Whitelisted role: {role.name}")

    @whitelist.command(name="removerole")
    async def unwhitelist_role(self, ctx, role: discord.Role):
        """Remove a role from the whitelist."""
        async with self.config.guild(ctx.guild).ignored_roles() as roles:
            if role.id in roles:
                roles.remove(role.id)
        await ctx.send(f"Removed role from whitelist: {role.name}")

    @whitelist.command(name="user")
    async def whitelist_user(self, ctx, user: discord.Member):
        """Whitelist a user from antispam."""
        async with self.config.guild(ctx.guild).ignored_users() as users:
            if user.id not in users:
                users.append(user.id)
        await ctx.send(f"Whitelisted user: {user.mention}")

    @whitelist.command(name="removeuser")
    async def unwhitelist_user(self, ctx, user: discord.Member):
        """Remove a user from the whitelist."""
        async with self.config.guild(ctx.guild).ignored_users() as users:
            if user.id in users:
                users.remove(user.id)
        await ctx.send(f"Removed user from whitelist: {user.mention}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        guild = message.guild
        conf = self.config.guild(guild)
        enabled = await conf.enabled()
        if not enabled:
            return

        # Ignore if in ignored channel/role/user
        ignored_channels = await conf.ignored_channels()
        ignored_roles = await conf.ignored_roles()
        ignored_users = await conf.ignored_users()
        if message.channel.id in ignored_channels:
            return
        if any(role.id in ignored_roles for role in getattr(message.author, "roles", [])):
            return
        if message.author.id in ignored_users:
            return

        # Cache message
        now = time.time()
        cache = self.user_message_cache[message.author.id]
        cache.append((now, message.content))

        # Heuristic 1: Message Frequency (Flooding)
        message_limit = await conf.message_limit()
        interval = await conf.interval()
        recent_msgs = [t for t, _ in cache if now - t < interval]
        if len(recent_msgs) >= message_limit:
            reason = f"Sent {len(recent_msgs)} messages in {interval} seconds."
            await self._punish(message, reason)
            return

        # Heuristic 2: Message Similarity (Copypasta/Repeat)
        similarity_threshold = await conf.similarity_threshold()
        if len(cache) >= 3:
            last = cache[-1][1]
            similar_count = 0
            for _, prev in list(cache)[-4:-1]:
                if self._similar(last, prev, similarity_threshold):
                    similar_count += 1
            if similar_count >= 2:
                reason = f"Sent {similar_count+1} highly similar messages."
                await self._punish(message, reason)
                return

        # Heuristic 3: ASCII Art / Large Block Messages
        ascii_art_threshold = await conf.ascii_art_threshold()
        ascii_art_min_lines = await conf.ascii_art_min_lines()
        if self._is_ascii_art(message.content, ascii_art_threshold, ascii_art_min_lines):
            reason = "Sent ASCII art or large block message."
            await self._punish(message, reason)
            return

        # Heuristic 4: Zalgo/Unicode Spam
        if self._is_zalgo(message.content):
            reason = "Sent Zalgo/unicode spam."
            await self._punish(message, reason)
            return

        # Heuristic 5: Mass Mentions
        if self._is_mass_mention(message):
            reason = "Mass mention spam."
            await self._punish(message, reason)
            return

    def _similar(self, a, b, threshold):
        if not a or not b:
            return False
        ratio = SequenceMatcher(None, a, b).ratio()
        return ratio > threshold

    def _is_ascii_art(self, content, threshold, min_lines):
        # Count lines with high ascii density or long lines
        lines = content.splitlines()
        if len(lines) < min_lines:
            return False
        ascii_lines = 0
        for line in lines:
            if len(line) > threshold and all(ord(c) < 128 for c in line if c.strip()):
                ascii_lines += 1
        return ascii_lines >= min_lines

    def _is_zalgo(self, content):
        # Zalgo = excessive combining unicode marks
        zalgo_re = re.compile(r'[\u0300-\u036F\u0489]')
        return len(zalgo_re.findall(content)) > 15

    def _is_mass_mention(self, message):
        # 5+ mentions in a message
        if len(message.mentions) >= 5:
            return True
        # @everyone or @here
        if "@everyone" in message.content or "@here" in message.content:
            return True
        return False

    async def _punish(self, message, reason):
        guild = message.guild
        conf = self.config.guild(guild)
        punishment = await conf.punishment()
        timeout_time = await conf.timeout_time()
        user = message.author

        # Prevent repeated actions in a short time
        now = time.time()
        last = self.user_last_action.get(user.id, 0)
        if now - last < 10:
            return
        self.user_last_action[user.id] = now

        try:
            await message.delete()
        except Exception:
            pass

        try:
            if punishment == "timeout":
                # Use Discord's timeout (communication disabled) if available
                if hasattr(user, "timeout"):
                    until = discord.utils.utcnow() + discord.timedelta(seconds=timeout_time)
                    await user.timeout(until, reason="AntiSpam: " + reason)
                else:
                    # Fallback: try to find a Muted role (legacy, not recommended)
                    muted = discord.utils.get(guild.roles, name="Muted")
                    if not muted:
                        muted = await guild.create_role(name="Muted", reason="AntiSpam timeout fallback role")
                        for channel in guild.channels:
                            try:
                                await channel.set_permissions(muted, send_messages=False, add_reactions=False)
                            except Exception:
                                continue
                    await user.add_roles(muted, reason="AntiSpam: " + reason)
                    await asyncio.sleep(timeout_time)
                    await user.remove_roles(muted, reason="AntiSpam: timeout expired")
            elif punishment == "kick":
                await user.kick(reason="AntiSpam: " + reason)
            elif punishment == "ban":
                await user.ban(reason="AntiSpam: " + reason, delete_message_days=1)
            # else: none
        except Exception:
            pass

        try:
            await user.send(
                f"You were punished in **{guild.name}** for spam: {reason}"
            )
        except Exception:
            pass

        # Log to the configured log channel, if set
        log_channel_id = await conf.log_channel()
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel:
                try:
                    embed = discord.Embed(
                        title="AntiSpam Action",
                        description=f"User: {user.mention} (`{user.id}`)\nReason: {reason}",
                        color=discord.Color.red(),
                        timestamp=discord.utils.utcnow(),
                    )
                    embed.add_field(name="Punishment", value=punishment)
                    embed.add_field(name="Channel", value=message.channel.mention)
                    await log_channel.send(embed=embed)
                except Exception:
                    pass

    @commands.command()
    @commands.guild_only()
    async def antispaminfo(self, ctx):
        """Show current AntiSpam settings."""
        conf = self.config.guild(ctx.guild)
        enabled = await conf.enabled()
        message_limit = await conf.message_limit()
        interval = await conf.interval()
        punishment = await conf.punishment()
        timeout_time = await conf.timeout_time()
        ignored_channels = await conf.ignored_channels()
        ignored_roles = await conf.ignored_roles()
        ignored_users = await conf.ignored_users()
        log_channel_id = await conf.log_channel()
        log_channel = ctx.guild.get_channel(log_channel_id) if log_channel_id else None
        embed = discord.Embed(title="AntiSpam Settings", color=discord.Color.blurple())
        embed.add_field(name="Enabled", value=str(enabled))
        embed.add_field(name="Message Limit", value=f"{message_limit} per {interval}s")
        embed.add_field(name="Punishment", value=punishment)
        if punishment == "timeout":
            embed.add_field(name="Timeout Time", value=f"{timeout_time}s")
        if log_channel:
            embed.add_field(name="Log Channel", value=log_channel.mention, inline=False)
        if ignored_channels:
            chans = [f"<#{cid}>" for cid in ignored_channels]
            embed.add_field(name="Whitelisted Channels", value=", ".join(chans), inline=False)
        if ignored_roles:
            roles = [f"<@&{rid}>" for rid in ignored_roles]
            embed.add_field(name="Whitelisted Roles", value=", ".join(roles), inline=False)
        if ignored_users:
            users = [f"<@{uid}>" for uid in ignored_users]
            embed.add_field(name="Whitelisted Users", value=", ".join(users), inline=False)
        await ctx.send(embed=embed)
