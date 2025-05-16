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
    Detects and mitigates message spam, flooding, copypasta, ascii art, emoji spam, and more.
    """

    __author__ = "adminelevation"
    __version__ = "1.0.0"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=73947298374)
        default_guild = {
            "enabled": True,
            "message_limit": 6,
            "interval": 7,
            "similarity_threshold": 0.85,
            "ascii_art_threshold": 12,
            "ascii_art_min_lines": 6,
            "emoji_spam_threshold": 15,  # New: max emojis per message before considered spam
            "emoji_spam_unique_threshold": 10,  # New: max unique emojis per message before considered spam
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
        if messages < 1 or seconds < 1:
            await ctx.send("Both messages and seconds must be greater than 0.")
            return
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
        if seconds < 1:
            await ctx.send("Timeout time must be greater than 0 seconds.")
            return
        await self.config.guild(ctx.guild).timeout_time.set(seconds)
        await ctx.send(f"Timeout time set to {seconds} seconds.")

    @antispam.command()
    async def setemojispam(self, ctx, max_emojis: int = 15, max_unique: int = 10):
        """Set emoji spam thresholds: max_emojis per message, max_unique emojis per message."""
        if max_emojis < 1 or max_unique < 1:
            await ctx.send("Both emoji thresholds must be greater than 0.")
            return
        await self.config.guild(ctx.guild).emoji_spam_threshold.set(max_emojis)
        await self.config.guild(ctx.guild).emoji_spam_unique_threshold.set(max_unique)
        await ctx.send(f"Emoji spam thresholds set: {max_emojis} total, {max_unique} unique per message.")

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
    async def on_message_without_command(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        # Prevent responding to self-bot or webhook messages
        if getattr(message, "webhook_id", None) is not None:
            return

        guild = message.guild
        conf = self.config.guild(guild)
        try:
            enabled = await conf.enabled()
        except Exception:
            return

        if not enabled:
            return

        # Ignore if in ignored channel/role/user
        try:
            ignored_channels = await conf.ignored_channels()
            ignored_roles = await conf.ignored_roles()
            ignored_users = await conf.ignored_users()
        except Exception:
            return

        if message.channel.id in ignored_channels:
            return
        if hasattr(message.author, "roles"):
            if any(role.id in ignored_roles for role in getattr(message.author, "roles", [])):
                return
        if message.author.id in ignored_users:
            return

        # Cache message
        now = time.time()
        cache = self.user_message_cache[message.author.id]
        cache.append((now, message.content))

        # Heuristic 1: Message Frequency (Flooding)
        try:
            message_limit = await conf.message_limit()
            interval = await conf.interval()
        except Exception:
            return
        recent_msgs = [t for t, _ in cache if now - t < interval]
        if len(recent_msgs) >= message_limit:
            reason = (
                f"Failed antispam check 1 for message flooding: "
                f"Sent {len(recent_msgs)} messages in {interval} seconds."
            )
            # Show each message with its timestamp (formatted), no [1], [2], etc.
            evidence = "\n".join(
                f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}: {content[:200]}"
                for ts, content in list(cache)[-len(recent_msgs):]
            )
            await self._punish(message, reason, evidence=evidence)
            return

        # Heuristic 2: Message Similarity (Copypasta/Repeat)
        try:
            similarity_threshold = await conf.similarity_threshold()
        except Exception:
            similarity_threshold = 0.85
        if len(cache) >= 3:
            last = cache[-1][1]
            similar_count = 0
            similar_msgs = []
            for idx, (_, prev) in enumerate(list(cache)[-4:-1]):
                if self._similar(last, prev, similarity_threshold):
                    similar_count += 1
                    similar_msgs.append(prev)
            if similar_count >= 2:
                reason = (
                    f"Failed antispam check 2 for copypasta/repeat: "
                    f"Sent {similar_count+1} highly similar messages."
                )
                evidence = (
                    f"Latest message:\n{last[:400]}\n\n"
                    f"Previous similar messages:\n" +
                    "\n".join(f"[{i+1}] {msg[:400]}" for i, msg in enumerate(similar_msgs))
                )
                await self._punish(message, reason, evidence=evidence)
                return

        # Heuristic 2b: Similar message content in last 5 minutes
        # This is the new check for similar messages over the last 5 minutes
        five_minutes = 5 * 60
        similar_count_5min = 0
        similar_msgs_5min = []
        if len(cache) >= 2:
            last_content = cache[-1][1]
            # Only consider messages in the last 5 minutes, excluding the current one
            for ts, prev_content in list(cache)[:-1]:
                if now - ts > five_minutes:
                    continue
                if self._similar(last_content, prev_content, similarity_threshold):
                    similar_count_5min += 1
                    similar_msgs_5min.append((ts, prev_content))
            # If 3 or more similar messages in last 5 minutes (including the current one)
            if similar_count_5min >= 2:
                reason = (
                    f"Failed antispam check 2b for repeated similar messages in last 5 minutes: "
                    f"Sent {similar_count_5min+1} highly similar messages in 5 minutes."
                )
                evidence = (
                    f"Latest message:\n{last_content[:400]}\n\n"
                    f"Previous similar messages in last 5 minutes:\n" +
                    "\n".join(
                        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}: {msg[:400]}"
                        for ts, msg in similar_msgs_5min
                    )
                )
                await self._punish(message, reason, evidence=evidence)
                return

        # Heuristic 3: ASCII Art / Large Block Messages
        try:
            ascii_art_threshold = await conf.ascii_art_threshold()
            ascii_art_min_lines = await conf.ascii_art_min_lines()
        except Exception:
            ascii_art_threshold = 12
            ascii_art_min_lines = 6
        if self._is_ascii_art(message.content, ascii_art_threshold, ascii_art_min_lines):
            reason = (
                "Failed antispam check 3 for ASCII art/large block message: "
                "Sent ASCII art or large block message."
            )
            evidence = f"Message content (first 600 chars):\n`{message.content[:600]}`"
            await self._punish(message, reason, evidence=evidence)
            return

        # Heuristic 4: Emoji Spam/Excessive Emoji Usage
        try:
            emoji_spam_threshold = await conf.emoji_spam_threshold()
            emoji_spam_unique_threshold = await conf.emoji_spam_unique_threshold()
        except Exception:
            emoji_spam_threshold = 15
            emoji_spam_unique_threshold = 10
        emoji_count, unique_emoji_count, emoji_list = self._count_emojis(message.content)
        if emoji_count >= emoji_spam_threshold or unique_emoji_count >= emoji_spam_unique_threshold:
            reason = (
                f"Failed antispam check 4 for emoji spam: "
                f"Emoji spam/excessive emoji usage. "
                f"Total emojis: {emoji_count}, Unique emojis: {unique_emoji_count}."
            )
            evidence = (
                f"Total emojis: {emoji_count}\n"
                f"Unique emojis: {unique_emoji_count}\n"
                f"Emojis: {' '.join(emoji_list)[:400]}\n"
                f"Message content (first 400 chars):\n{message.content[:400]}"
            )
            await self._punish(message, reason, evidence=evidence)
            return

        # Heuristic 5: Zalgo/Unicode Spam
        if self._is_zalgo(message.content):
            zalgo_chars = re.findall(r'[\u0300-\u036F\u0489]', message.content)
            reason = (
                f"Failed antispam check 5 for Zalgo/unicode spam: "
                f"Sent Zalgo/unicode spam. Number of zalgo/unicode marks: {len(zalgo_chars)}."
            )
            evidence = (
                f"Message content (first 400 chars):\n{message.content[:400]}\n\n"
                f"Number of zalgo/unicode marks: {len(zalgo_chars)}"
            )
            await self._punish(message, reason, evidence=evidence)
            return

        # Heuristic 6: Mass Mentions
        if self._is_mass_mention(message):
            mention_list = [f"<@{m.id}>" for m in message.mentions]
            reason = (
                f"Failed antispam check 6 for mass mention spam: "
                f"Mass mention spam. Mentions: {len(message.mentions)}, "
                f"@everyone: {'@everyone' in message.content}, @here: {'@here' in message.content}."
            )
            evidence = (
                f"Mentions: {', '.join(mention_list) if mention_list else 'None'}\n"
                f"@everyone: {'@everyone' in message.content}\n"
                f"@here: {'@here' in message.content}\n"
                f"Message content (first 400 chars):\n{message.content[:400]}"
            )
            await self._punish(message, reason, evidence=evidence)
            return

    def _similar(self, a, b, threshold):
        if not a or not b:
            return False
        try:
            ratio = SequenceMatcher(None, a, b).ratio()
        except Exception:
            return False
        return ratio > threshold

    def _is_ascii_art(self, content, threshold, min_lines):
        # Count lines with high ascii density or long lines
        lines = content.splitlines()
        if len(lines) < min_lines:
            return False
        ascii_lines = 0
        for line in lines:
            # Only count lines with at least one non-whitespace character
            if len(line) > threshold and all(ord(c) < 128 for c in line if c.strip()):
                ascii_lines += 1
        return ascii_lines >= min_lines

    def _is_zalgo(self, content):
        # Zalgo = excessive combining unicode marks
        zalgo_re = re.compile(r'[\u0300-\u036F\u0489]')
        try:
            return len(zalgo_re.findall(content)) > 15
        except Exception:
            return False

    def _is_mass_mention(self, message):
        # 5+ mentions in a message
        if hasattr(message, "mentions") and len(message.mentions) >= 5:
            return True
        # @everyone or @here
        if "@everyone" in message.content or "@here" in message.content:
            return True
        return False

    def _count_emojis(self, content):
        # Returns (total_emoji_count, unique_emoji_count, emoji_list)
        # Discord custom emoji: <a?:name:id>
        custom_emoji_re = re.compile(r'<a?:\w+:\d+>')
        # Unicode emoji: use a broad regex for emoji blocks
        # This regex is not perfect but covers most emoji
        unicode_emoji_re = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F680-\U0001F6FF"  # transport & map symbols
            "\U0001F1E0-\U0001F1FF"  # flags (iOS)
            "\U00002700-\U000027BF"  # Dingbats
            "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
            "\U00002600-\U000026FF"  # Misc symbols
            "\U00002B50"             # ⭐
            "\U00002B06"             # ⬆
            "\U00002B07"             # ⬇
            "\U00002B1B-\U00002B1C"  # ⬛⬜
            "\U0000231A-\U0000231B"  # ⌚⌛
            "\U000025AA-\U000025AB"  # ▪▫
            "\U000025FB-\U000025FE"  # ◻◾
            "\U0001F004"             # 🀄
            "\U0001F0CF"             # 🃏
            "]+"
        )
        custom_emojis = custom_emoji_re.findall(content)
        unicode_emojis = unicode_emoji_re.findall(content)
        emoji_list = custom_emojis + unicode_emojis
        unique_emoji_set = set(emoji_list)
        return len(emoji_list), len(unique_emoji_set), emoji_list

    async def _punish(self, message, reason, evidence=None):
        guild = message.guild
        conf = self.config.guild(guild)
        try:
            punishment = await conf.punishment()
        except Exception:
            punishment = "timeout"
        try:
            timeout_time = await conf.timeout_time()
        except Exception:
            timeout_time = 60
        user = message.author

        # Prevent repeated actions in a short time
        now = time.time()
        last = self.user_last_action.get(user.id, 0)
        if now - last < 10:
            return
        self.user_last_action[user.id] = now

        # Try to delete the message, but ignore if already deleted or missing permissions
        try:
            await message.delete()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            pass
        except Exception:
            pass

        try:
            # The reason string is already descriptive from the calling context
            if punishment == "timeout":
                # Use Discord's timeout (communication disabled) if available
                if hasattr(user, "timeout"):
                    import datetime
                    until = discord.utils.utcnow() + datetime.timedelta(seconds=timeout_time)
                    await user.timeout(until, reason=reason)
                else:
                    # Fallback: try to find a Muted role (legacy, not recommended)
                    muted = discord.utils.get(guild.roles, name="Muted")
                    if not muted:
                        try:
                            muted = await guild.create_role(name="Muted", reason="AntiSpam timeout fallback role")
                        except Exception:
                            muted = None
                    if muted:
                        for channel in guild.channels:
                            try:
                                await channel.set_permissions(muted, send_messages=False, add_reactions=False)
                            except Exception:
                                continue
                        try:
                            await user.add_roles(muted, reason=reason)
                            await asyncio.sleep(timeout_time)
                            await user.remove_roles(muted, reason="AntiSpam: timeout expired")
                        except Exception:
                            pass
            elif punishment == "kick":
                try:
                    await user.kick(reason=reason)
                except Exception:
                    pass
            elif punishment == "ban":
                try:
                    await user.ban(reason=reason, delete_message_days=1)
                except Exception:
                    pass
            # else: none
        except Exception:
            pass

        # Log to the configured log channel, if set
        try:
            log_channel_id = await conf.log_channel()
        except Exception:
            log_channel_id = None

        log_channel = None
        # Fix: Use bot.get_channel if get_channel is not available on guild, and check permissions
        if log_channel_id:
            # Try to get channel from guild first
            log_channel = guild.get_channel(log_channel_id)
            # If not found, fallback to bot.get_channel
            if log_channel is None and hasattr(self.bot, "get_channel"):
                log_channel = self.bot.get_channel(log_channel_id)
        # Also check if log_channel is a TextChannel and bot can send messages
        if log_channel and isinstance(log_channel, discord.TextChannel):
            try:
                # Check if bot has permission to send messages and embeds
                perms = log_channel.permissions_for(guild.me)
                if not (perms.send_messages and perms.embed_links):
                    return
                embed = discord.Embed(
                    title="Potential spam detected",
                    description=f"User: {user.mention} (`{user.id}`)\nReason: {reason}",
                    color=0xff4545,
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Punishment", value=punishment)
                embed.add_field(name="Channel", value=message.channel.mention)
                if evidence:
                    # Truncate evidence if too long for Discord
                    if len(evidence) > 1000:
                        evidence = evidence[:1000] + "\n...(truncated)"
                    embed.add_field(name="Evidence", value=evidence, inline=False)
                await log_channel.send(embed=embed)
            except Exception as e:
                # For debugging, you may want to log this somewhere else
                pass

    @antispam.command()
    @commands.guild_only()
    async def settings(self, ctx):
        """Show current AntiSpam settings."""
        conf = self.config.guild(ctx.guild)
        try:
            enabled = await conf.enabled()
            message_limit = await conf.message_limit()
            interval = await conf.interval()
            punishment = await conf.punishment()
            timeout_time = await conf.timeout_time()
            ignored_channels = await conf.ignored_channels()
            ignored_roles = await conf.ignored_roles()
            ignored_users = await conf.ignored_users()
            log_channel_id = await conf.log_channel()
            emoji_spam_threshold = await conf.emoji_spam_threshold()
            emoji_spam_unique_threshold = await conf.emoji_spam_unique_threshold()
        except Exception:
            await ctx.send("Failed to fetch AntiSpam settings.")
            return
        # Fix: Use bot.get_channel if get_channel is not available on guild
        log_channel = None
        if log_channel_id:
            log_channel = ctx.guild.get_channel(log_channel_id)
            if log_channel is None and hasattr(self.bot, "get_channel"):
                log_channel = self.bot.get_channel(log_channel_id)
        embed = discord.Embed(title="AntiSpam settings", color=0xfffffe)
        embed.add_field(name="Enabled", value=str(enabled))
        embed.add_field(name="Message Limit", value=f"{message_limit} per {interval}s")
        embed.add_field(name="Punishment", value=punishment)
        if punishment == "timeout":
            embed.add_field(name="Timeout Time", value=f"{timeout_time}s")
        embed.add_field(name="Emoji Spam Threshold", value=f"{emoji_spam_threshold} total, {emoji_spam_unique_threshold} unique", inline=False)
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
