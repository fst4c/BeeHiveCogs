import discord  # type: ignore
from redbot.core import commands, Config, checks  # type: ignore
import asyncio
import re
import time
import unicodedata
import string
from collections import defaultdict, deque, Counter
from difflib import SequenceMatcher

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

class AntiSpam(commands.Cog):
    """
    Heuristic-based anti-spam cog for Red-DiscordBot.
    Detects and mitigates message spam, flooding, copypasta, ascii art, emoji spam, unicode/invisible abuse, and more.
    """

    __author__ = "adminelevation"
    __version__ = "1.0.0"

    # Virus signature-like detection codes and their descriptions
    DETECTION_SIGNATURES = {
        "MsgFlood.A!msg": "Message flooding: Too many messages sent in a short time.",
        "Repeat.Copypasta.B!msg": "Copypasta/repeat: Multiple highly similar messages sent in a short time.",
        "Repeat.Timespan.C!msg": "Repeated similar messages: Multiple highly similar messages sent over a longer period.",
        "Block.AsciiArt.D!msg": "ASCII art/large block: Message contains large ASCII art or block text.",
        "Emoji.Spam.E!msg": "Emoji spam: Excessive emoji usage in a single message.",
        "Unicode.Zalgo.F!msg": "Zalgo/unicode spam: Excessive use of combining unicode marks (Zalgo text).",
        "Mention.Mass.G!msg": "Mass mention: Excessive user mentions or use of @everyone/@here.",
        "Invisible.Obfuscation.H!msg": "Obfuscated/invisible characters: Message contains invisible or control unicode characters.",
        "Unicode.Homoglyph.I!msg": "Unicode homoglyph abuse: Message uses visually confusable unicode characters.",
        "Coordinated.Raid.J!msg": "Coordinated spam/raid: Multiple new users spamming in a channel.",
    }

    # Unicode invisible/obfuscation characters
    INVISIBLE_CHARS = [
        "\u200b",  # zero-width space
        "\u200c",  # zero-width non-joiner
        "\u200d",  # zero-width joiner
        "\u200e",  # left-to-right mark
        "\u200f",  # right-to-left mark
        "\u202a",  # left-to-right embedding
        "\u202b",  # right-to-left embedding
        "\u202c",  # pop directional formatting
        "\u202d",  # left-to-right override
        "\u202e",  # right-to-left override
        "\u2060",  # word joiner
        "\u2061",  # function application
        "\u2062",  # invisible times
        "\u2063",  # invisible separator
        "\u2064",  # invisible plus
        "\ufeff",  # zero-width no-break space
    ]

    # Unicode confusables/homoglyphs (partial, for demonstration)
    HOMOGLYPH_MAP = {
        "а": "a",  # Cyrillic a
        "е": "e",  # Cyrillic e
        "о": "o",  # Cyrillic o
        "р": "p",  # Cyrillic p
        "с": "c",  # Cyrillic c
        "у": "y",  # Cyrillic y
        "х": "x",  # Cyrillic x
        "і": "i",  # Cyrillic i
        "Ι": "I",  # Greek capital iota
        "Ο": "O",  # Greek capital omicron
        "Α": "A",  # Greek capital alpha
        "Β": "B",  # Greek capital beta
        "ϲ": "c",  # Greek small letter lunate sigma
        # ... (expand as needed)
    }

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
            "emoji_spam_threshold": 15,
            "emoji_spam_unique_threshold": 10,
            "punishment": "timeout",
            "timeout_time": 15,  # Default timeout time in minutes
            "ignored_channels": [],
            "ignored_roles": [],
            "ignored_users": [],
            "log_channel": None,
            # Raid detection thresholds (new)
            "raid_window": 30,  # seconds
            "raid_join_age": 600,  # seconds (10 minutes)
            "raid_min_msgs": 6,
            "raid_min_unique_users": 4,
            "raid_min_new_users": 3,
        }
        self.config.register_guild(**default_guild)
        self.user_message_cache = defaultdict(lambda: deque(maxlen=15))
        self.user_last_action = {}

        # For coordinated/raid detection
        self.channel_user_message_times = defaultdict(lambda: deque(maxlen=100))
        self.channel_new_user_joins = defaultdict(lambda: deque(maxlen=100))
        self.user_first_seen = {}

    async def red_delete_data_for_user(self, *, requester, user_id: int):
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
    async def limit(self, ctx, messages: int, seconds: int):
        """Set message limit and interval (messages, seconds)."""
        if messages < 1 or seconds < 1:
            await ctx.send("Both messages and seconds must be greater than 0.")
            return
        await self.config.guild(ctx.guild).message_limit.set(messages)
        await self.config.guild(ctx.guild).interval.set(seconds)
        await ctx.send(f"Set to {messages} messages per {seconds} seconds.")

    @antispam.command()
    async def punishment(self, ctx, punishment: str):
        """Set punishment: timeout, kick, ban, none."""
        punishment = punishment.lower()
        if punishment not in ("timeout", "kick", "ban", "none"):
            await ctx.send("Invalid punishment. Choose from: timeout, kick, ban, none.")
            return
        await self.config.guild(ctx.guild).punishment.set(punishment)
        await ctx.send(f"Punishment set to: {punishment}")

    @antispam.command()
    async def timeout(self, ctx, minutes: int):
        """Set timeout duration in minutes."""
        if minutes < 1:
            await ctx.send("Timeout time must be greater than 0 minutes.")
            return
        await self.config.guild(ctx.guild).timeout_time.set(minutes)
        await ctx.send(f"Timeout time set to {minutes} minutes.")

    @antispam.command()
    async def emojispam(self, ctx, max_emojis: int = 15, max_unique: int = 10):
        """Set emoji spam thresholds: max_emojis per message, max_unique emojis per message."""
        if max_emojis < 1 or max_unique < 1:
            await ctx.send("Both emoji thresholds must be greater than 0.")
            return
        await self.config.guild(ctx.guild).emoji_spam_threshold.set(max_emojis)
        await self.config.guild(ctx.guild).emoji_spam_unique_threshold.set(max_unique)
        await ctx.send(f"Emoji spam thresholds set: {max_emojis} total, {max_unique} unique per message.")

    @antispam.command(name="similarity")
    async def similarity(self, ctx, threshold: float):
        """
        Set the similarity threshold for copypasta/repeat detection (0.0 - 1.0).
        Higher values are stricter (default: 0.85).
        """
        if not (0.0 < threshold < 1.0):
            await ctx.send("Threshold must be between 0.0 and 1.0 (exclusive).")
            return
        await self.config.guild(ctx.guild).similarity_threshold.set(threshold)
        await ctx.send(f"Similarity threshold set to {threshold:.2f}.")

    @antispam.command(name="logs")
    async def logs(self, ctx, channel: discord.TextChannel = None):
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

    @whitelist.command(name="addchannel")
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

    @whitelist.command(name="addrole")
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

    @whitelist.command(name="adduser")
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

    @antispam.command(name="signatures")
    async def signatures(self, ctx):
        """Show detection signature codes and their descriptions."""
        embed = discord.Embed(
            title="AntiSpam signatures",
            color=0x00bfff,
            description="These are the detection codes used in AntiSpam logs and punishments."
        )
        for code, desc in self.DETECTION_SIGNATURES.items():
            embed.add_field(name=code, value=desc, inline=False)
        await ctx.send(embed=embed)

    @antispam.group(name="raid", invoke_without_command=True)
    async def raid(self, ctx):
        """Configure coordinated raid/spam detection thresholds."""
        await ctx.send_help()

    @raid.command(name="window")
    async def raid_window(self, ctx, seconds: int):
        """Set the time window (in seconds) for raid detection (default: 30)."""
        if seconds < 5 or seconds > 600:
            await ctx.send("Window must be between 5 and 600 seconds.")
            return
        await self.config.guild(ctx.guild).raid_window.set(seconds)
        await ctx.send(f"Raid detection window set to {seconds} seconds.")

    @raid.command(name="joinage")
    async def raid_joinage(self, ctx, seconds: int):
        """Set the max account age (in seconds) to consider a user 'new' for raid detection (default: 600)."""
        if seconds < 60 or seconds > 86400:
            await ctx.send("Join age must be between 60 and 86400 seconds (1 minute to 1 day).")
            return
        await self.config.guild(ctx.guild).raid_join_age.set(seconds)
        await ctx.send(f"Raid detection 'new user' join age set to {seconds} seconds.")

    @raid.command(name="minmsgs")
    async def raid_minmsgs(self, ctx, count: int):
        """Set the minimum number of messages in the window to trigger raid detection (default: 6)."""
        if count < 2 or count > 100:
            await ctx.send("Minimum messages must be between 2 and 100.")
            return
        await self.config.guild(ctx.guild).raid_min_msgs.set(count)
        await ctx.send(f"Raid detection minimum messages set to {count}.")

    @raid.command(name="minunique")
    async def raid_minunique(self, ctx, count: int):
        """Set the minimum number of unique users in the window to trigger raid detection (default: 4)."""
        if count < 2 or count > 100:
            await ctx.send("Minimum unique users must be between 2 and 100.")
            return
        await self.config.guild(ctx.guild).raid_min_unique_users.set(count)
        await ctx.send(f"Raid detection minimum unique users set to {count}.")

    @raid.command(name="minnew")
    async def raid_minnew(self, ctx, count: int):
        """Set the minimum number of new users in the window to trigger raid detection (default: 3)."""
        if count < 1 or count > 100:
            await ctx.send("Minimum new users must be between 1 and 100.")
            return
        await self.config.guild(ctx.guild).raid_min_new_users.set(count)
        await ctx.send(f"Raid detection minimum new users set to {count}.")

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        if getattr(message, "webhook_id", None) is not None:
            return

        # Autobypass users with administrator permission
        if hasattr(message.author, "guild_permissions"):
            if getattr(message.author.guild_permissions, "administrator", False):
                return

        guild = message.guild
        conf = self.config.guild(guild)
        try:
            enabled = await conf.enabled()
        except Exception:
            return

        if not enabled:
            return

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

        now = time.time()
        cache = self.user_message_cache[message.author.id]
        cache.append((now, message.content))

        # Track first seen for coordinated/raid detection
        if message.author.id not in self.user_first_seen:
            self.user_first_seen[message.author.id] = now
            # Track join for this channel
            self.channel_new_user_joins[message.channel.id].append((now, message.author.id))

        # Track per-channel user message times for coordinated/raid detection
        self.channel_user_message_times[message.channel.id].append((now, message.author.id))

        # Heuristic 1: Message Frequency (Flooding)
        try:
            message_limit = await conf.message_limit()
            interval = await conf.interval()
        except Exception:
            return
        recent_msgs = [t for t, _ in cache if now - t < interval]
        if len(recent_msgs) >= message_limit:
            reason = "MsgFlood.A!msg"
            evidence = "\n".join(
                f"<t:{int(ts)}:f>: {content[:200]}"
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
            similar_msgs_timestamps = []
            for idx, (ts, prev) in enumerate(list(cache)[-4:-1]):
                if self._similar(last, prev, similarity_threshold):
                    similar_count += 1
                    similar_msgs.append(prev)
                    similar_msgs_timestamps.append(ts)
            if similar_count >= 2:
                reason = "Spam:Repeat.Copypasta.B!msg"
                evidence = (
                    f"{last[:400]}"
                    f"\n" +
                    "\n".join(
                        f"<t:{int(ts)}:f>: {msg[:400]}"
                        for ts, msg in zip(similar_msgs_timestamps, similar_msgs)
                    )
                )
                await self._punish(message, reason, evidence=evidence)
                return

        # Heuristic 2b: Similar message content in last 5 minutes
        five_minutes = 5 * 60
        similar_count_5min = 0
        similar_msgs_5min = []
        if len(cache) >= 2:
            last_content = cache[-1][1]
            for ts, prev_content in list(cache)[:-1]:
                if now - ts > five_minutes:
                    continue
                if self._similar(last_content, prev_content, similarity_threshold):
                    similar_count_5min += 1
                    similar_msgs_5min.append((ts, prev_content))
            if similar_count_5min >= 2:
                reason = "Repeat.Timespan.C!msg"
                evidence = (
                    f"Latest message:\n{last_content[:400]}\n\n"
                    f"Previous similar messages in last 5 minutes:\n" +
                    "\n".join(
                        f"<t:{int(ts)}:f>: {msg[:400]}"
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
            reason = "Block.AsciiArt.D!msg"
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
            reason = "Emoji.Spam.E!msg"
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
            reason = "Unicode.Zalgo.F!msg"
            evidence = (
                f"Message content (first 400 chars):\n{message.content[:400]}\n\n"
                f"Number of zalgo/unicode marks: {len(zalgo_chars)}"
            )
            await self._punish(message, reason, evidence=evidence)
            return

        # Heuristic 6: Mass Mentions
        if self._is_mass_mention(message):
            mention_list = [f"<@{m.id}>" for m in message.mentions]
            reason = "Mention.Mass.G!msg"
            evidence = (
                f"Mentions: {', '.join(mention_list) if mention_list else 'None'}\n"
                f"@everyone: {'@everyone' in message.content}\n"
                f"@here: {'@here' in message.content}\n"
                f"Message content (first 400 chars):\n{message.content[:400]}"
            )
            await self._punish(message, reason, evidence=evidence)
            return

        # Heuristic 7: Obfuscated/Invisible Characters
        invisible_found = self._find_invisible_chars(message.content)
        if invisible_found:
            reason = "Invisible.Obfuscation.H!msg"
            evidence = (
                f"Message contains invisible/obfuscated characters:\n"
                f"{', '.join(invisible_found)}\n"
                f"Message content (first 400 chars):\n{message.content[:400]}"
            )
            await self._punish(message, reason, evidence=evidence)
            return

        # Heuristic 8: Unicode Homoglyph/Language Abuse
        if self._has_homoglyph_abuse(message.content):
            reason = "Unicode.Homoglyph.I!msg"
            evidence = (
                f"Message contains suspicious unicode homoglyphs (confusable with ASCII):\n"
                f"Message content (first 400 chars):\n{message.content[:400]}"
            )
            await self._punish(message, reason, evidence=evidence)
            return

        # Heuristic 9: Coordinated Spam/Raid Detection
        raid_triggered, raid_evidence = await self._detect_coordinated_raid(message)
        if raid_triggered:
            reason = "Coordinated.Raid.J!msg"
            await self._punish(message, reason, evidence=raid_evidence)
            return

    def _normalize_text(self, text):
        # Remove invisible chars, normalize case, strip punctuation, NFKC normalize, replace homoglyphs
        text = unicodedata.normalize("NFKC", text)
        # Remove invisible chars
        for ch in self.INVISIBLE_CHARS:
            text = text.replace(ch, "")
        # Replace homoglyphs with ASCII equivalents
        text = "".join(self.HOMOGLYPH_MAP.get(c, c) for c in text)
        # Remove punctuation
        text = text.translate(str.maketrans("", "", string.punctuation))
        # Lowercase
        text = text.lower()
        # Remove extra whitespace
        text = " ".join(text.split())
        return text

    def _similar(self, a, b, threshold):
        if not a or not b:
            return False
        norm_a = self._normalize_text(a)
        norm_b = self._normalize_text(b)
        try:
            if RAPIDFUZZ_AVAILABLE:
                # Use token_sort_ratio for better fuzzy matching
                score = fuzz.token_sort_ratio(norm_a, norm_b) / 100.0
            else:
                # Fallback to SequenceMatcher
                score = SequenceMatcher(None, norm_a, norm_b).ratio()
        except Exception:
            return False
        return score > threshold

    def _is_ascii_art(self, content, threshold, min_lines):
        lines = content.splitlines()
        if len(lines) < min_lines:
            return False
        ascii_lines = 0
        for line in lines:
            if len(line) > threshold and all(ord(c) < 128 for c in line if c.strip()):
                ascii_lines += 1
        return ascii_lines >= min_lines

    def _is_zalgo(self, content):
        zalgo_re = re.compile(r'[\u0300-\u036F\u0489]')
        try:
            return len(zalgo_re.findall(content)) > 15
        except Exception:
            return False

    def _is_mass_mention(self, message):
        if hasattr(message, "mentions") and len(message.mentions) >= 5:
            return True
        if "@everyone" in message.content or "@here" in message.content:
            return True
        return False

    def _count_emojis(self, content):
        custom_emoji_re = re.compile(r'<a?:\w+:\d+>')
        unicode_emoji_re = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "\U00002700-\U000027BF"
            "\U0001F900-\U0001F9FF"
            "\U00002600-\U000026FF"
            "\U00002B50"
            "\U00002B06"
            "\U00002B07"
            "\U00002B1B-\U00002B1C"
            "\U0000231A-\U0000231B"
            "\U000025AA-\U000025AB"
            "\U000025FB-\U000025FE"
            "\U0001F004"
            "\U0001F0CF"
            "]+"
        )
        custom_emojis = custom_emoji_re.findall(content)
        unicode_emojis = unicode_emoji_re.findall(content)
        emoji_list = custom_emojis + unicode_emojis
        unique_emoji_set = set(emoji_list)
        return len(emoji_list), len(unique_emoji_set), emoji_list

    def _find_invisible_chars(self, content):
        found = []
        for ch in self.INVISIBLE_CHARS:
            if ch in content:
                name = unicodedata.name(ch, f"U+{ord(ch):04X}")
                found.append(f"{name} (U+{ord(ch):04X})")
        # Also check for other control chars (C0/C1)
        for c in content:
            if unicodedata.category(c) in ("Cf", "Cc") and c not in self.INVISIBLE_CHARS:
                name = unicodedata.name(c, f"U+{ord(c):04X}")
                found.append(f"{name} (U+{ord(c):04X})")
        return found

    def _has_homoglyph_abuse(self, content):
        # If message contains a suspicious number of non-ASCII chars that are confusable with ASCII
        count = 0
        for c in content:
            if c in self.HOMOGLYPH_MAP and self.HOMOGLYPH_MAP[c] != c:
                count += 1
        # Heuristic: 3+ confusable chars in a short message, or 5+ in any message
        if count >= 5:
            return True
        if count >= 3 and len(content) < 50:
            return True
        return False

    async def _detect_coordinated_raid(self, message):
        # Look for many new users (joined in last X minutes) sending messages in a channel in a short time
        now = time.time()
        conf = self.config.guild(message.guild)
        try:
            window = await conf.raid_window()
        except Exception:
            window = 30
        try:
            join_age = await conf.raid_join_age()
        except Exception:
            join_age = 10 * 60
        try:
            min_msgs = await conf.raid_min_msgs()
        except Exception:
            min_msgs = 6
        try:
            min_unique_users = await conf.raid_min_unique_users()
        except Exception:
            min_unique_users = 4
        try:
            min_new_users = await conf.raid_min_new_users()
        except Exception:
            min_new_users = 3

        channel_id = message.channel.id
        recent_msgs = [u for t, u in self.channel_user_message_times[channel_id] if now - t < window]
        if len(recent_msgs) < min_msgs:
            return False, None
        # Count how many unique users, and how many are "new"
        user_counts = Counter(recent_msgs)
        unique_users = set(recent_msgs)
        new_users = [u for u in unique_users if now - self.user_first_seen.get(u, now) < join_age]
        if len(new_users) >= min_new_users and len(unique_users) >= min_unique_users:
            evidence = (
                f"Possible coordinated spam/raid detected in {message.channel.mention}.\n"
                f"Recent unique users: {len(unique_users)} (new: {len(new_users)}) in {window}s\n"
                f"New users: {', '.join(str(u) for u in new_users)}"
            )
            return True, evidence
        return False, None

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
            timeout_time = 1  # fallback to 1 minute
        user = message.author

        now = time.time()
        last = self.user_last_action.get(user.id, 0)
        if now - last < 10:
            return
        self.user_last_action[user.id] = now

        try:
            await message.delete()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            pass
        except Exception:
            pass

        try:
            if punishment == "timeout":
                if hasattr(user, "timeout"):
                    import datetime
                    until = discord.utils.utcnow() + datetime.timedelta(minutes=timeout_time)
                    await user.timeout(until, reason=reason)
                # If the user object does not have a timeout method, do nothing (no fallback mute)
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
        except Exception:
            pass

        try:
            log_channel_id = await conf.log_channel()
        except Exception:
            log_channel_id = None

        log_channel = None
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel is None and hasattr(self.bot, "get_channel"):
                log_channel = self.bot.get_channel(log_channel_id)
        if log_channel and isinstance(log_channel, discord.TextChannel):
            try:
                perms = log_channel.permissions_for(guild.me)
                if not (perms.send_messages and perms.embed_links):
                    return
                embed = discord.Embed(
                    title="Potential spam detected",
                    color=0xff4545,
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=False)
                embed.add_field(
                    name="Signature",
                    value=f"**{reason}**\n[p]-# `antispam signatures` for details.",
                    inline=False
                )
                embed.add_field(name="Punishment", value=punishment)
                embed.add_field(name="Channel", value=message.channel.mention)
                if evidence:
                    if len(evidence) > 1000:
                        evidence = evidence[:1000] + "\n...(truncated)"
                    embed.add_field(name="Evidence", value=evidence, inline=False)
                await log_channel.send(embed=embed)
            except Exception as e:
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
            similarity_threshold = await conf.similarity_threshold()
            raid_window = await conf.raid_window()
            raid_join_age = await conf.raid_join_age()
            raid_min_msgs = await conf.raid_min_msgs()
            raid_min_unique_users = await conf.raid_min_unique_users()
            raid_min_new_users = await conf.raid_min_new_users()
        except Exception:
            await ctx.send("Failed to fetch AntiSpam settings.")
            return
        log_channel = None
        if log_channel_id:
            log_channel = ctx.guild.get_channel(log_channel_id)
            if log_channel is None and hasattr(self.bot, "get_channel"):
                log_channel = self.bot.get_channel(log_channel_id)
        embed = discord.Embed(title="AntiSpam settings", color=0xfffffe)
        embed.add_field(name="Enabled", value=str(enabled))
        embed.add_field(name="Message limit", value=f"{message_limit} per {interval}s")
        embed.add_field(name="Punishment", value=punishment)
        if punishment == "timeout":
            embed.add_field(name="Timeout Time", value=f"{timeout_time}m")
        embed.add_field(name="Emoji Spam threshold", value=f"{emoji_spam_threshold} total, {emoji_spam_unique_threshold} unique", inline=False)
        embed.add_field(name="Similarity threshold", value=f"{similarity_threshold:.2f}", inline=False)
        embed.add_field(
            name="Raid detection",
            value=(
                f"Window: {raid_window}s, "
                f"Join age: {raid_join_age}s, "
                f"Min msgs: {raid_min_msgs}, "
                f"Min unique users: {raid_min_unique_users}, "
                f"Min new users: {raid_min_new_users}"
            ),
            inline=False
        )
        if log_channel:
            embed.add_field(name="Log channel", value=log_channel.mention, inline=False)
        if ignored_channels:
            chans = [f"<#{cid}>" for cid in ignored_channels]
            embed.add_field(name="Whitelisted channels", value=", ".join(chans), inline=False)
        if ignored_roles:
            roles = [f"<@&{rid}>" for rid in ignored_roles]
            embed.add_field(name="Whitelisted roles", value=", ".join(roles), inline=False)
        if ignored_users:
            users = [f"<@{uid}>" for uid in ignored_users]
            embed.add_field(name="Whitelisted users", value=", ".join(users), inline=False)
        await ctx.send(embed=embed)
