import discord
from redbot.core import commands, Config
import math
import aiohttp
from datetime import timedelta, datetime
from collections import Counter, defaultdict
import unicodedata
import re
import asyncio
import tempfile
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import timezone, timedelta

class Omni(commands.Cog):
    """AI-powered automatic text moderation provided by frontier moderation models"""

    def __init__(self, bot):
        self.bot = bot
        self.session = None
        self.save_interval = 300  # Save every 5 minutes

        # Configuration setup
        self.config = Config.get_conf(self, identifier=11111111111)
        self._register_config()

        # In-memory statistics
        self.memory_stats = defaultdict(lambda: defaultdict(int))
        self.memory_user_message_counts = defaultdict(lambda: defaultdict(int))
        self.memory_moderated_users = defaultdict(lambda: defaultdict(int))
        self.memory_category_counter = defaultdict(Counter)

        # In-memory per-user violation tracking (guild_id -> user_id -> [violation dicts])
        self.memory_user_violations = defaultdict(lambda: defaultdict(list))

        # In-memory per-user warning tracking (guild_id -> user_id -> int)
        self.memory_user_warnings = defaultdict(lambda: defaultdict(int))

        # In-memory reminder tracking to prevent duplicate reminders
        self._reminder_sent_at = defaultdict(dict)  # {guild_id: {channel_id: datetime}}

        # Track timeouts issued by message id for "Untimeout" button
        self._timeout_issued_for_message = {}  # {message_id: True/False}

        # Store deleted messages for possible restoration
        self._deleted_messages = {}  # {message_id: {"content": ..., "author_id": ..., "author_name": ..., "author_avatar": ..., "channel_id": ..., "attachments": [...] }}

        # For logging: track which image was flagged if an image is moderated
        self._flagged_image_for_message = {}  # {message_id: image_url}

        # Start periodic save task
        # Use asyncio.create_task for compatibility with modern Red/discord.py
        try:
            self.bot.loop.create_task(self.periodic_save())
        except Exception:
            asyncio.create_task(self.periodic_save())

    def _register_config(self):
        """Register configuration defaults."""
        self.config.register_guild(
            moderation_threshold=0.75,
            timeout_duration=0,
            log_channel=None,
            debug_mode=False,
            message_count=0,
            moderated_count=0,
            moderated_users={},
            category_counter={},
            whitelisted_channels=[],
            whitelisted_roles=[],
            whitelisted_users=[],
            whitelisted_categories=[],
            moderation_enabled=True,
            user_message_counts={},
            image_count=0,
            moderated_image_count=0,
            timeout_count=0,
            total_timeout_duration=0,
            too_weak_votes=0,
            too_tough_votes=0,
            just_right_votes=0,
            last_vote_time=None,
            delete_violatory_messages=True,
            last_reminder_time=None,
            bypass_nsfw=False,
            monitoring_warning_enabled=True,
            user_violations={},  # {user_id: [violation_dict, ...]}
            user_warnings={},    # {user_id: int}
        )
        self.config.register_global(
            global_message_count=0,
            global_moderated_count=0,
            global_moderated_users={},
            global_category_counter={},
            global_image_count=0,
            global_moderated_image_count=0,
            global_timeout_count=0,
            global_total_timeout_duration=0
        )

    async def initialize(self):
        """Initialize the aiohttp session."""
        try:
            if self.session is None or getattr(self.session, "closed", True):
                self.session = aiohttp.ClientSession()
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Omni cog: {e}")

    def normalize_text(self, text):
        """Normalize text to replace with standard alphabetical/numeric characters."""
        try:
            text = ''.join(
                c if unicodedata.category(c).startswith(('L', 'N')) else ' '
                for c in unicodedata.normalize('NFKD', text)
            )
            replacements = {'nègre': 'negro', 'reggin': 'nigger'}
            for word, replacement in replacements.items():
                text = text.replace(word, replacement)
            return re.sub(r'\s+', ' ', text).strip()
        except Exception as e:
            raise ValueError(f"Failed to normalize text: {e}")

    @commands.Cog.listener()
    async def on_message(self, message):
        await self.process_message(message)
        await self.check_monitoring_reminder(message)

    async def check_monitoring_reminder(self, message):
        """Check and send a monitoring reminder if needed."""
        if getattr(message.author, "bot", False) or not getattr(message, "guild", None):
            return

        guild = message.guild
        channel = message.channel

        # Check if monitoring warning is enabled
        monitoring_warning_enabled = await self.config.guild(guild).monitoring_warning_enabled()
        if not monitoring_warning_enabled:
            return

        # Check all whitelist conditions before incrementing or sending reminder
        whitelisted_channels = await self.config.guild(guild).whitelisted_channels()
        whitelisted_categories = await self.config.guild(guild).whitelisted_categories()
        whitelisted_roles = await self.config.guild(guild).whitelisted_roles()
        whitelisted_users = await self.config.guild(guild).whitelisted_users()
        bypass_nsfw = await self.config.guild(guild).bypass_nsfw()

        # Check if channel is whitelisted by channel ID
        if getattr(channel, "id", None) in whitelisted_channels:
            return
        # Check if channel's category is whitelisted
        if getattr(channel, "category_id", None) in whitelisted_categories:
            return
        # Check if any of the author's roles are whitelisted
        if hasattr(message.author, "roles") and any(getattr(role, "id", None) in whitelisted_roles for role in getattr(message.author, "roles", [])):
            return
        # Check if author is whitelisted
        if getattr(message.author, "id", None) in whitelisted_users:
            return
        # Check if NSFW bypass is enabled and channel is NSFW
        if hasattr(channel, "is_nsfw") and callable(getattr(channel, "is_nsfw", None)):
            try:
                is_nsfw = channel.is_nsfw()
            except Exception:
                is_nsfw = False
            if is_nsfw and bypass_nsfw:
                return

        # Increment the message count for the channel
        self.memory_user_message_counts[guild.id][channel.id] += 1

        # Check if the message count has reached 75
        if self.memory_user_message_counts[guild.id][channel.id] >= 75:
            # Prevent duplicate reminders by checking last sent time
            now = datetime.utcnow()
            last_sent = self._reminder_sent_at[guild.id].get(channel.id)
            # Only send if not sent in the last 5 minutes (300 seconds)
            if not last_sent or (now - last_sent).total_seconds() > 300:
                await self.send_monitoring_reminder(channel)
                self._reminder_sent_at[guild.id][channel.id] = now
            # Reset the message count for the channel regardless
            self.memory_user_message_counts[guild.id][channel.id] = 0

    async def send_monitoring_reminder(self, channel):
        """Send a monitoring reminder to the specified channel."""
        try:
            # Check if monitoring warning is enabled for this guild
            guild = channel.guild
            monitoring_warning_enabled = await self.config.guild(guild).monitoring_warning_enabled()
            if not monitoring_warning_enabled:
                return
            command_prefixes = await self.bot.get_valid_prefixes()
            command_prefix = command_prefixes[0] if command_prefixes else "!"
            embed = discord.Embed(
                title="This conversation is subject to automatic moderation",
                description=(
                    "An agentic (AI) moderator is analyzing this conversation in **real-time**, watching for potentially harmful content and behaviors.\n\nYour messages and message content are subject to moderation, logging, transmission, analysis, and archival **at any time**.\n- Human review is not required for Omni to take action\n- **All** violations are automatically documented for staff review\n- Extreme or consistent abuse may result in your Discord account being globally banned."
                ),
                color=0xfffffe
            )
            embed.set_footer(text=f'Use "{command_prefix}omni vote" to give feedback on this server\'s moderation')
            embed.set_thumbnail(url="https://www.beehive.systems/hubfs/Icon%20Packs/White/sparkles.png")
            await channel.send(embed=embed)
        except discord.HTTPException as e:
            raise RuntimeError(f"Failed to send monitoring reminder: {e}")

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        await self.process_message(after)

    async def process_message(self, message):
        try:
            if getattr(message.author, "bot", False) or not getattr(message, "guild", None):
                return

            guild = message.guild
            if not await self.config.guild(guild).moderation_enabled():
                return

            if getattr(message.channel, "id", None) in await self.config.guild(guild).whitelisted_channels():
                return

            whitelisted_categories = await self.config.guild(guild).whitelisted_categories()
            if getattr(message.channel, "category_id", None) in whitelisted_categories:
                return

            whitelisted_roles = await self.config.guild(guild).whitelisted_roles()
            if hasattr(message.author, "roles") and any(getattr(role, "id", None) in whitelisted_roles for role in getattr(message.author, "roles", [])):
                return

            if getattr(message.author, "id", None) in await self.config.guild(guild).whitelisted_users():
                return

            if hasattr(message.channel, "is_nsfw") and callable(getattr(message.channel, "is_nsfw", None)):
                try:
                    is_nsfw = message.channel.is_nsfw()
                except Exception:
                    is_nsfw = False
                if is_nsfw and await self.config.guild(guild).bypass_nsfw():
                    return

            self.increment_statistic(guild.id, 'message_count')
            self.increment_statistic('global', 'global_message_count')
            self.increment_user_message_count(guild.id, message.author.id)

            api_key = (await self.bot.get_shared_api_tokens("openai")).get("api_key")
            if not api_key:
                return

            if self.session is None or getattr(self.session, "closed", True):
                self.session = aiohttp.ClientSession()

            normalized_content = self.normalize_text(message.content)
            input_data = [{"type": "text", "text": normalized_content}]

            # Count and increment image stats for each image (not just once for the message)
            image_attachments = []
            if getattr(message, "attachments", None):
                for attachment in message.attachments:
                    if getattr(attachment, "content_type", None) and attachment.content_type.startswith("image/") and not attachment.content_type.endswith("gif"):
                        image_attachments.append(attachment)
                        self.increment_statistic(guild.id, 'image_count')
                        self.increment_statistic('global', 'global_image_count')

            # Only send text for moderation in the main request
            text_category_scores = await self.analyze_content(input_data, api_key, message)
            moderation_threshold = await self.config.guild(guild).moderation_threshold()
            text_flagged = any(score > moderation_threshold for score in text_category_scores.values())

            # Analyze each image individually (API only supports one image at a time)
            for attachment in image_attachments:
                image_data = [{"type": "image_url", "image_url": {"url": attachment.url}}]
                image_category_scores = await self.analyze_content(image_data, api_key, message)
                image_flagged = any(score > moderation_threshold for score in image_category_scores.values())

                if image_flagged:
                    self.update_moderation_stats(guild.id, message, image_category_scores)
                    # Track which image was flagged for this message
                    self._flagged_image_for_message[message.id] = attachment.url
                    await self.handle_moderation(message, image_category_scores, flagged_image_url=attachment.url)
                else:
                    # If not flagged, ensure we don't leave a stale value
                    if self._flagged_image_for_message.get(message.id) == attachment.url:
                        del self._flagged_image_for_message[message.id]

                # Space out requests
                await asyncio.sleep(1)

            if text_flagged:
                self.update_moderation_stats(guild.id, message, text_category_scores)
                # For text moderation, clear any flagged image for this message
                if message.id in self._flagged_image_for_message:
                    del self._flagged_image_for_message[message.id]
                await self.handle_moderation(message, text_category_scores, flagged_image_url=None)

            if await self.config.guild(guild).debug_mode():
                await self.log_message(message, text_category_scores)
        except Exception as e:
            raise RuntimeError(f"Error processing message: {e}")

    def increment_statistic(self, guild_id, stat_name, increment_value=1):
        self.memory_stats[guild_id][stat_name] += increment_value

    def increment_user_message_count(self, guild_id, user_id):
        self.memory_user_message_counts[guild_id][user_id] += 1

    def update_moderation_stats(self, guild_id, message, text_category_scores):
        self.increment_statistic(guild_id, 'moderated_count')
        self.increment_statistic('global', 'global_moderated_count')
        self.memory_moderated_users[guild_id][message.author.id] += 1
        self.memory_moderated_users['global'][message.author.id] += 1
        self.update_category_counter(guild_id, text_category_scores)
        self.update_category_counter('global', text_category_scores)

        # --- Per-user violation tracking ---
        # Only store if at least one score > 0.2 (to avoid noise)
        violation_categories = {cat: score for cat, score in text_category_scores.items() if score > 0.2}
        if violation_categories:
            violation_entry = {
                "message_id": message.id,
                "timestamp": getattr(message, "created_at", datetime.utcnow()).timestamp(),
                "content": message.content,
                "categories": violation_categories,
                "channel_id": getattr(message.channel, "id", None),
                "channel_name": getattr(message.channel, "name", ""),
                "author_id": getattr(message.author, "id", None),
                "author_name": getattr(message.author, "display_name", str(message.author)),
                "attachments": [a.url for a in getattr(message, "attachments", []) if getattr(a, "content_type", None) and a.content_type.startswith("image/") and not a.content_type.endswith("gif")],
            }
            self.memory_user_violations[guild_id][message.author.id].append(violation_entry)

        if any(getattr(attachment, "content_type", None) and attachment.content_type.startswith("image/") and not attachment.content_type.endswith("gif") for attachment in getattr(message, "attachments", [])):
            self.increment_statistic(guild_id, 'moderated_image_count')
            self.increment_statistic('global', 'global_moderated_image_count')

    def update_category_counter(self, guild_id, text_category_scores):
        for category, score in text_category_scores.items():
            if score > 0.2:
                self.memory_category_counter[guild_id][category] += 1

    async def analyze_content(self, input_data, api_key, message):
        """
        Analyze content using the OpenAI moderation endpoint.
        Automatically retries on 4XX and 5XX errors, with exponential backoff up to a max number of attempts.
        """
        max_attempts = 5
        base_delay = 2  # seconds
        attempt = 0
        while attempt < max_attempts:
            try:
                if self.session is None or getattr(self.session, "closed", True):
                    self.session = aiohttp.ClientSession()
                async with self.session.post(
                    "https://api.openai.com/v1/moderations",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}"
                    },
                    json={
                        "model": "omni-moderation-latest",
                        "input": input_data
                    }
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("results", [{}])[0].get("category_scores", {})
                    elif 400 <= response.status < 600:
                        # Retry on any 4XX or 5XX error
                        attempt += 1
                        if attempt >= max_attempts:
                            await self.log_message(message, {}, error_code=response.status)
                            return {}
                        await asyncio.sleep(base_delay * attempt)
                    else:
                        # Unexpected status, log and return empty
                        await self.log_message(message, {}, error_code=response.status)
                        return {}
            except Exception as e:
                attempt += 1
                if attempt >= max_attempts:
                    raise RuntimeError(f"Failed to analyze content after {max_attempts} attempts: {e}")
                await asyncio.sleep(base_delay * attempt)
        # If all attempts fail, return empty
        await self.log_message(message, {}, error_code="max_retries")
        return {}

    async def translate_to_english(self, text):
        """
        Translate the given text to English using OpenAI's GPT-3.5/4 API.
        Returns the translated text, or None if translation fails.
        """
        try:
            api_key = (await self.bot.get_shared_api_tokens("openai")).get("api_key")
            if not api_key:
                return None
            if self.session is None or getattr(self.session, "closed", True):
                self.session = aiohttp.ClientSession()
            # Use the chat/completions endpoint for translation
            prompt = (
                "Translate the following message to English. "
                "If the message is already in English, return it unchanged. "
                "Only return the translated message, no extra commentary.\n\n"
                f"Message:\n{text}"
            )
            payload = {
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "You are a helpful translation assistant."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 1024,
                "temperature": 0.2,
            }
            async with self.session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                },
                json=payload,
                timeout=20
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    choices = data.get("choices", [])
                    if choices and "message" in choices[0]:
                        return choices[0]["message"]["content"].strip()
                return None
        except Exception:
            return None

    async def handle_moderation(self, message, category_scores, flagged_image_url=None):
        try:
            guild = message.guild
            timeout_duration = await self.config.guild(guild).timeout_duration()
            log_channel_id = await self.config.guild(guild).log_channel()
            delete_violatory_messages = await self.config.guild(guild).delete_violatory_messages()

            message_deleted = False
            if delete_violatory_messages:
                try:
                    # Store deleted message info for restoration
                    self._deleted_messages[message.id] = {
                        "content": message.content,
                        "author_id": message.author.id,
                        "author_name": message.author.display_name if hasattr(message.author, "display_name") else str(message.author),
                        "author_avatar": str(getattr(message.author, "display_avatar", getattr(message.author, "avatar_url", ""))),
                        "channel_id": message.channel.id,
                        "attachments": [a.url for a in getattr(message, "attachments", []) if getattr(a, "content_type", None) and a.content_type.startswith("image/") and not a.content_type.endswith("gif")]
                    }
                    await message.delete()
                    self.memory_moderated_users[guild.id][message.author.id] += 1
                    message_deleted = True
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    pass

            timeout_issued = False
            if timeout_duration > 0:
                try:
                    reason = (
                        f"AI moderator issued a timeout. Violation: " +
                        ", ".join(f"{category}: {score * 100:.0f}%" for category, score in category_scores.items() if score > 0.2) +
                        f". Message: {message.content}"
                    )
                    await message.author.timeout(timedelta(minutes=timeout_duration), reason=reason)
                    self.increment_statistic(guild.id, 'timeout_count')
                    self.increment_statistic('global', 'global_timeout_count')
                    self.increment_statistic(guild.id, 'total_timeout_duration', timeout_duration)
                    self.increment_statistic('global', 'global_total_timeout_duration', timeout_duration)
                    timeout_issued = True
                except discord.Forbidden:
                    pass
                except AttributeError:
                    pass  # .timeout may not exist on all discord.py versions

            # Track if a timeout was issued for this message for the "Untimeout" button
            self._timeout_issued_for_message[message.id] = timeout_issued

            # --- Begin webhook reporting for moderation event ---
            try:
                # Prepare the action_taken string
                if message_deleted and timeout_issued:
                    action_taken = "Message deleted\nTimeout issued"
                elif message_deleted:
                    action_taken = "Message deleted"
                elif timeout_issued:
                    action_taken = "User timed out"
                else:
                    action_taken = "No action taken"

                # Prepare the payload
                payload = {
                    "server_id": str(guild.id),
                    "server_name": guild.name,
                    "channel_id": str(message.channel.id),
                    "channel_name": getattr(message.channel, "name", ""),
                    "sender_id": str(message.author.id),
                    "sender_username": str(message.author),
                    "message_id": str(message.id),
                    "message_content": message.content,
                    "abuse_scores": category_scores,
                    "action_taken": action_taken
                }
                h = "SBPV94@6JGG$63bah*93y#W6s9M&3H8z"
                headers = {
                    "x-omni": h
                }
                session = self.session
                if session is None or getattr(session, "closed", True):
                    session = aiohttp.ClientSession()
                async with session.post(
                    "https://automator.beehive.systems/api/v1/webhooks/hj05HelXPKgXZQEAUWf7T",
                    json=payload,
                    headers=headers,
                    timeout=10
                ) as resp:
                    pass
                if session is not self.session:
                    await session.close()
            except Exception:
                pass

            if log_channel_id:
                log_channel = guild.get_channel(log_channel_id)
                if log_channel:
                    embed = await self._create_moderation_embed(
                        message, category_scores, "AI moderator detected potential misbehavior", action_taken, flagged_image_url=flagged_image_url
                    )
                    view = await self._create_action_view(message, category_scores, timeout_issued=timeout_issued)
                    await log_channel.send(embed=embed, view=view)
        except Exception as e:
            raise RuntimeError(f"Failed to handle moderation: {e}")

    async def _create_moderation_embed(self, message, category_scores, title, action_taken, flagged_image_url=None):
        embed = discord.Embed(
            title=title,
            description=f"The following message was flagged for potentially breaking server rules, Discord's **[Terms](<https://discord.com/terms>)**, or Discord's **[Community Guidelines](<https://discord.com/guidelines>)**.\n```{message.content}```",
            color=0xff4545,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Sent by", value=f"<@{message.author.id}>\n`{message.author.id}`", inline=True)
        embed.add_field(name="Sent in", value=f"<#{message.channel.id}>\n`{message.channel.id}`", inline=True)
        embed.add_field(name="Action taken", value=action_taken, inline=True)
        embed.add_field(name="AI moderator ratings", value="", inline=False)
        embed.set_footer(text="AI can make mistakes, have a human review this alert")
        moderation_threshold = await self.config.guild(message.guild).moderation_threshold()
        sorted_scores = sorted(category_scores.items(), key=lambda item: item[1], reverse=True)[:6]
        for category, score in sorted_scores:
            score_percentage = score * 100
            score_display = f"**{score_percentage:.0f}%**" if score > moderation_threshold else f"{score_percentage:.0f}%"
            embed.add_field(name=category.capitalize(), value=score_display, inline=True)

        # If a flagged_image_url is provided, use it as the embed image
        if flagged_image_url:
            embed.set_image(url=flagged_image_url)
        else:
            # Fallback: if not provided, use the first image attachment (as before)
            if getattr(message, "attachments", None):
                for attachment in message.attachments:
                    if getattr(attachment, "content_type", None) and attachment.content_type.startswith("image/") and not attachment.content_type.endswith("gif"):
                        embed.set_image(url=attachment.url)
                        break
        return embed

    class _ModerationActionView(discord.ui.View):
        def __init__(self, cog, message, timeout_issued, *, timeout_duration):
            super().__init__(timeout=None)
            self.cog = cog
            self.message = message
            self.timeout_issued = timeout_issued
            self.timeout_duration = timeout_duration

            # Store the ID of the user who was moderated (the message author)
            self.moderated_user_id = message.author.id

            # Add Untimeout button only if a timeout was issued
            if timeout_issued:
                self.add_item(self.UntimeoutButton(cog, message, row=1, moderated_user_id=self.moderated_user_id))

            # Add Restore button if message was deleted and info is available
            if message.id in cog._deleted_messages:
                self.add_item(self.RestoreButton(cog, message, row=1, moderated_user_id=self.moderated_user_id))

            # Add Warn button (always on row 1)
            self.add_item(self.WarnButton(cog, message, row=1, moderated_user_id=self.moderated_user_id))

            # Only show Timeout button if timeouts are enabled (timeout_duration > 0)
            if self.timeout_duration == 0:
                self.add_item(self.TimeoutButton(cog, message, timeout_duration, row=1, moderated_user_id=self.moderated_user_id))

            # Add kick and ban buttons (always on row 1)
            self.add_item(self.KickButton(cog, message, row=1, moderated_user_id=self.moderated_user_id))
            self.add_item(self.BanButton(cog, message, row=1, moderated_user_id=self.moderated_user_id))

            # Add Dismiss button to delete the log message (row 2)
            self.add_item(self.DismissButton(cog, message, row=2, moderated_user_id=self.moderated_user_id))

            # Add Translate button (always on row 1)
            self.add_item(self.TranslateButton(cog, message, row=2, moderated_user_id=self.moderated_user_id))

            # Add jump to conversation button LAST (so it appears underneath, on row 2)
            self.add_item(discord.ui.Button(label="See conversation", url=message.jump_url, row=2))

        class TimeoutButton(discord.ui.Button):
            def __init__(self, cog, message, timeout_duration, row=1, moderated_user_id=None):
                super().__init__(label="Timeout", style=discord.ButtonStyle.grey, custom_id=f"timeout_{message.author.id}_{message.id}", emoji="⏳", row=row)
                self.cog = cog
                self.message = message
                self.timeout_duration = timeout_duration
                self.moderated_user_id = moderated_user_id

            async def callback(self, interaction: discord.Interaction):
                # Prevent the moderated user from interacting with their own log
                if interaction.user.id == self.moderated_user_id:
                    await interaction.response.send_message("You cannot interact with moderation logs of your own actions.", ephemeral=True)
                    return
                # Only allow users with manage_guild or admin
                if not (getattr(interaction.user.guild_permissions, "administrator", False) or getattr(interaction.user.guild_permissions, "manage_guild", False)):
                    await interaction.response.send_message("You do not have permission to use this button.", ephemeral=True)
                    return
                try:
                    member = self.message.guild.get_member(self.message.author.id)
                    if not member:
                        await interaction.response.send_message("User not found in this server.", ephemeral=True)
                        return
                    # Check if already timed out
                    if hasattr(member, "timed_out_until") and getattr(member, "timed_out_until", None):
                        await interaction.response.send_message("User is already timed out.", ephemeral=True)
                        return
                    reason = f"Manual timeout via Omni log button. Message: {self.message.content}"
                    await member.timeout(timedelta(minutes=self.timeout_duration), reason=reason)
                    # Mark as timed out for this message
                    self.cog._timeout_issued_for_message[self.message.id] = True
                    await interaction.response.send_message(f"User {member.mention} has been timed out for {self.timeout_duration} minutes.", ephemeral=True)
                except Exception as e:
                    await interaction.response.send_message(f"Failed to timeout user: {e}", ephemeral=True)

        class UntimeoutButton(discord.ui.Button):
            def __init__(self, cog, message, row=1, moderated_user_id=None):
                super().__init__(label="Untimeout", style=discord.ButtonStyle.grey, custom_id=f"untimeout_{message.author.id}_{message.id}", emoji="✅", row=row)
                self.cog = cog
                self.message = message
                self.moderated_user_id = moderated_user_id

            async def callback(self, interaction: discord.Interaction):
                # Prevent the moderated user from interacting with their own log
                if interaction.user.id == self.moderated_user_id:
                    await interaction.response.send_message("You cannot interact with moderation logs of your own actions.", ephemeral=True)
                    return
                try:
                    member = self.message.guild.get_member(self.message.author.id)
                    if not member:
                        await interaction.response.send_message("User not found in this server.", ephemeral=True)
                        return

                    # Remove timeout by setting duration to None
                    await member.timeout(None, reason="Staff member removed a timeout issued by Omni")
                    self.cog._timeout_issued_for_message[self.message.id] = False
                    # Update the button: change label and disable it
                    self.label = "Timeout lifted"
                    self.disabled = True
                    # Defer the interaction before editing the message to avoid errors
                    await interaction.response.defer()
                    # Try to update the view to reflect the new label and disabled state
                    try:
                        await interaction.message.edit(view=self.view)
                    except Exception:
                        pass
                except Exception as e:
                    await interaction.response.send_message(f"Failed to untimeout user: {e}", ephemeral=True)

        class WarnButton(discord.ui.Button):
            def __init__(self, cog, message, row=1, moderated_user_id=None):
                super().__init__(label="Warn", style=discord.ButtonStyle.grey, custom_id=f"warn_{message.author.id}_{message.id}", emoji="⚠️", row=row)
                self.cog = cog
                self.message = message
                self.moderated_user_id = moderated_user_id

            async def callback(self, interaction: discord.Interaction):
                # Prevent the moderated user from interacting with their own log
                if interaction.user.id == self.moderated_user_id:
                    embed = discord.Embed(
                        description="You cannot interact with moderation logs of your own actions.",
                        color=discord.Color.orange()
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return
                member = self.message.guild.get_member(self.message.author.id)
                if not member:
                    embed = discord.Embed(
                        description="User not found in this server.",
                        color=discord.Color.red()
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return
                # Compose warning embed for DM
                warning_embed = discord.Embed(
                    title="Conduct warning",
                    description=(
                        f"Your message was flagged by the AI moderator in **{self.message.guild.name}**. A human moderator later reviewed this alert, agreed the AI's decision, and has issued you a conduct warning as a result."
                    ),
                    color=0xff4545
                )
                warning_embed.add_field(
                    name="Your message",
                    value=f"`{self.message.content}`" or "*No content*",
                    inline=False
                )
                warning_embed.add_field(
                    name="Next steps",
                    value="Please review the server rules and Discord's [Terms of Service](https://discord.com/terms) and [Community Guidelines](https://discord.com/guidelines). Further violations of the server's rules may lead to additional punishments, like timeouts, documented warnings, kicks, and bans.",
                    inline=False
                )
                warning_embed.set_footer(text="We appreciate your cooperation in making the server a safe place")
                # Try to send DM only
                try:
                    await member.send(embed=warning_embed)
                    self.label = "Warning sent"
                except Exception:
                    self.label = "DM's closed"
                self.disabled = True
                await interaction.response.defer()
                try:
                    await interaction.message.edit(view=self.view)
                except Exception:
                    pass

                # --- Log warning count for user in memory and config ---
                guild_id = self.message.guild.id
                user_id = self.message.author.id
                # Increment in-memory warning count
                self.cog.memory_user_warnings[guild_id][user_id] += 1
                # Also increment persistent warning count
                guild_conf = self.cog.config.guild(self.message.guild)
                user_warnings = await guild_conf.user_warnings()
                user_warnings[str(user_id)] = user_warnings.get(str(user_id), 0) + 1
                await guild_conf.user_warnings.set(user_warnings)

        class TranslateButton(discord.ui.Button):
            def __init__(self, cog, message, row=2, moderated_user_id=None):
                super().__init__(label="Translate", style=discord.ButtonStyle.grey, custom_id=f"translate_{message.author.id}_{message.id}", emoji="🔡", row=row)
                self.cog = cog
                self.message = message
                self.moderated_user_id = moderated_user_id

            async def callback(self, interaction: discord.Interaction):
                # Prevent the moderated user from interacting with their own log
                if interaction.user.id == self.moderated_user_id:
                    await interaction.response.send_message("You cannot interact with moderation logs of your own actions.", ephemeral=True)
                    return
                # Only allow users with manage_guild or admin
                if not (getattr(interaction.user.guild_permissions, "administrator", False) or getattr(interaction.user.guild_permissions, "manage_guild", False)):
                    await interaction.response.send_message("You do not have permission to use this button.", ephemeral=True)
                    return
                # Disable the button while processing
                self.disabled = True
                self.label = "AI working..."
                await interaction.response.defer()
                try:
                    await interaction.message.edit(view=self.view)
                except Exception:
                    pass

                # Call the translation function
                translated = await self.cog.translate_to_english(self.message.content)

                # Restore the button state
                self.disabled = False
                self.label = "Translation below"

                if translated:
                    # Send the translation as an ephemeral message
                    embed = discord.Embed(
                        title="Moderated content translated to English",
                        description=translated,
                        color=0xfffffe
                    )
                    await interaction.followup.send(embed=embed, ephemeral=False)
                else:
                    await interaction.followup.send(
                        "Failed to translate the message or no translation available.",
                        ephemeral=True
                    )

                self.disabled = True
                try:
                    await interaction.message.edit(view=self.view)
                except Exception:
                    pass

        class KickButton(discord.ui.Button):
            def __init__(self, cog, message, row=1, moderated_user_id=None):
                super().__init__(label="Kick", style=discord.ButtonStyle.grey, custom_id=f"kick_{message.author.id}_{message.id}", emoji="👢", row=row)
                self.cog = cog
                self.message = message
                self.moderated_user_id = moderated_user_id

            async def callback(self, interaction: discord.Interaction):
                # Prevent the moderated user from interacting with their own log
                if interaction.user.id == self.moderated_user_id:
                    await interaction.response.send_message("You cannot interact with moderation logs of your own actions.", ephemeral=True)
                    return
                await self.cog.kick_user(interaction)

        class BanButton(discord.ui.Button):
            def __init__(self, cog, message, row=1, moderated_user_id=None):
                super().__init__(label="Ban", style=discord.ButtonStyle.grey, custom_id=f"ban_{message.author.id}_{message.id}", emoji="🔨", row=row)
                self.cog = cog
                self.message = message
                self.moderated_user_id = moderated_user_id

            async def callback(self, interaction: discord.Interaction):
                # Prevent the moderated user from interacting with their own log
                if interaction.user.id == self.moderated_user_id:
                    await interaction.response.send_message("You cannot interact with moderation logs of your own actions.", ephemeral=True)
                    return
                await self.cog.ban_user(interaction)

        class RestoreButton(discord.ui.Button):
            def __init__(self, cog, message, row=1, moderated_user_id=None):
                super().__init__(label="Resend", style=discord.ButtonStyle.grey, custom_id=f"restore_{message.author.id}_{message.id}", emoji="♻️", row=row)
                self.cog = cog
                self.message = message
                self.moderated_user_id = moderated_user_id

            async def callback(self, interaction: discord.Interaction):
                # Prevent the moderated user from interacting with their own log
                if interaction.user.id == self.moderated_user_id:
                    await interaction.response.send_message("You cannot interact with moderation logs of your own actions.", ephemeral=True)
                    return
                msg_id = self.message.id
                deleted_info = self.cog._deleted_messages.get(msg_id)
                if not deleted_info:
                    await interaction.response.send_message("No deleted message data found to restore.", ephemeral=True)
                    return
                guild = interaction.guild
                channel = guild.get_channel(deleted_info["channel_id"])
                if not channel:
                    await interaction.response.send_message("Original channel not found.", ephemeral=True)
                    return

                # Prepare content and attachments
                content = deleted_info.get("content", "")
                attachments = deleted_info.get("attachments", [])
                if not isinstance(attachments, list):
                    attachments = []

                # Get the original message timestamp, if available
                timestamp = deleted_info.get("created_at")
                # If not present, fallback to self.message.created_at if possible
                if not timestamp and hasattr(self.message, "created_at"):
                    timestamp = self.message.created_at
                # Format the timestamp for Discord (dynamic)
                timestamp_str = ""
                if timestamp:
                    # If it's a datetime object, convert to unix timestamp
                    import datetime
                    if isinstance(timestamp, datetime.datetime):
                        unix_ts = int(timestamp.timestamp())
                    else:
                        try:
                            unix_ts = int(timestamp)
                        except Exception:
                            unix_ts = None
                    if unix_ts:
                        timestamp_str = f"<t:{unix_ts}:R>"
                # Compose the description with timestamp
                if content and timestamp_str:
                    description = f"{content}\n*Originally sent {timestamp_str}*"
                elif content:
                    description = content
                elif timestamp_str:
                    description = f"*Originally sent {timestamp_str}*"
                else:
                    description = ""

                try:
                    if description.strip():
                        author = self.message.author
                        embed = discord.Embed(
                            title=f"",
                            description=description,
                            color=0xfffffe
                        )
                        if author.avatar:
                            embed.set_author(name=f"{author.display_name} said", icon_url=author.avatar.url)
                        else:
                            embed.set_author(name=f"{author.display_name} said")
                        embed.set_footer(text=f"This message was flagged by the AI moderator, but a staff member subsequently approved it to be sent.")
                        await channel.send(embed=embed)
                    # If there are image attachments, send them as separate messages
                    for img_url in attachments:
                        if img_url:
                            embed = discord.Embed().set_image(url=img_url)
                            await channel.send(embed=embed)
                    # After restoring, disable the button and change the label
                    self.label = "Message re-sent"
                    self.disabled = True
                    await interaction.response.defer()
                    try:
                        await interaction.message.edit(view=self.view)
                    except Exception:
                        pass
                except Exception as e:
                    await interaction.response.send_message(f"Failed to restore message: {e}", ephemeral=True)

        class DismissButton(discord.ui.Button):
            def __init__(self, cog, message, row=2, moderated_user_id=None):
                super().__init__(label="Dismiss alert", style=discord.ButtonStyle.grey, custom_id=f"dismiss_{message.id}", emoji="🗑️", row=row)
                self.cog = cog
                self.message = message
                self.moderated_user_id = moderated_user_id

            async def callback(self, interaction: discord.Interaction):
                # Prevent the moderated user from interacting with their own log
                if interaction.user.id == self.moderated_user_id:
                    await interaction.response.send_message("You cannot dismiss moderation logs of your own actions.", ephemeral=True)
                    return
                # Only allow users with manage_guild or admin
                if not (getattr(interaction.user.guild_permissions, "administrator", False) or getattr(interaction.user.guild_permissions, "manage_guild", False)):
                    await interaction.response.send_message("You do not have permission to use this button.", ephemeral=True)
                    return
                try:
                    await interaction.message.delete()
                except Exception as e:
                    await interaction.response.send_message(f"Failed to delete log message: {e}", ephemeral=True)

    async def _create_action_view(self, message, category_scores, timeout_issued=None):
        # Determine if a timeout was issued for this message
        if timeout_issued is None:
            timeout_issued = self._timeout_issued_for_message.get(message.id, False)
        timeout_duration = await self.config.guild(message.guild).timeout_duration()
        return self._ModerationActionView(self, message, timeout_issued, timeout_duration=timeout_duration)

    async def _get_previous_message(self, message):
        try:
            async for msg in message.channel.history(limit=2, before=message):
                return msg
        except Exception:
            return None
        return None

    async def kick_user(self, interaction: discord.Interaction):
        # Accept both new and old custom_id formats for backward compatibility
        custom_id = getattr(interaction, "custom_id", None) or getattr(interaction.data, "custom_id", None)
        if not custom_id:
            custom_id = interaction.data.get("custom_id", "")
        # Accept both "kick_button" and "kick_{user_id}_{message_id}"
        if custom_id.startswith("kick_"):
            parts = custom_id.split("_")
            if len(parts) >= 3:
                user_id = int(parts[1])
            else:
                await interaction.response.send_message("Invalid kick button.", ephemeral=True)
                return
        else:
            user_id = int(interaction.custom_id.split("_")[1])
        guild = interaction.guild
        user = guild.get_member(user_id)
        if user:
            reason = f"Kicked by moderator action. Message: {getattr(interaction.message, 'content', '')}"
            try:
                await user.kick(reason=reason)
                await interaction.response.send_message(f"User {user} has been kicked.", ephemeral=True)
            except Exception:
                await interaction.response.send_message("Failed to kick user.", ephemeral=True)

    async def ban_user(self, interaction: discord.Interaction):
        # Accept both new and old custom_id formats for backward compatibility
        custom_id = getattr(interaction, "custom_id", None) or getattr(interaction.data, "custom_id", None)
        if not custom_id:
            custom_id = interaction.data.get("custom_id", "")
        # Accept both "ban_button" and "ban_{user_id}_{message_id}"
        if custom_id.startswith("ban_"):
            parts = custom_id.split("_")
            if len(parts) >= 3:
                user_id = int(parts[1])
            else:
                await interaction.response.send_message("Invalid ban button.", ephemeral=True)
                return
        else:
            user_id = int(interaction.custom_id.split("_")[1])
        guild = interaction.guild
        user = guild.get_member(user_id)
        if user:
            reason = f"Banned by moderator action. Message: {getattr(interaction.message, 'content', '')}"
            try:
                await user.ban(reason=reason)
                await interaction.response.send_message(f"User {user} has been banned.", ephemeral=True)
            except Exception:
                await interaction.response.send_message("Failed to ban user.", ephemeral=True)

    async def log_message(self, message, category_scores, error_code=None):
        try:
            guild = message.guild
            log_channel_id = await self.config.guild(guild).log_channel()

            if log_channel_id:
                log_channel = guild.get_channel(log_channel_id)
                if log_channel:
                    # If an image was flagged for this message, use it in the embed
                    flagged_image_url = self._flagged_image_for_message.get(message.id)
                    embed = await self._create_moderation_embed(
                        message, category_scores, "Message processed by Omni", "No action taken", flagged_image_url=flagged_image_url
                    )
                    if error_code:
                        embed.add_field(name="Error", value=f":x: `{error_code}` Failed to send to moderation endpoint.", inline=False)
                    view = await self._create_action_view(message, category_scores)
                    await log_channel.send(embed=embed, view=view)
        except Exception as e:
            raise RuntimeError(f"Failed to log message: {e}")

    async def periodic_save(self):
        """Periodically save in-memory statistics to persistent storage."""
        while True:
            await asyncio.sleep(self.save_interval)
            try:
                await self._save_statistics()
            except Exception as e:
                raise RuntimeError(f"Failed to save statistics: {e}")

    async def _save_statistics(self):
        """Save statistics to persistent storage."""
        for guild_id, stats in self.memory_stats.items():
            if guild_id == 'global':
                for stat_name, value in stats.items():
                    current_value = await self.config.get_attr(stat_name)()
                    await self.config.get_attr(stat_name).set(current_value + value)
            else:
                guild_conf = self.config.guild_from_id(guild_id)
                for stat_name, value in stats.items():
                    current_value = await guild_conf.get_attr(stat_name)()
                    await guild_conf.get_attr(stat_name).set(current_value + value)

        for guild_id, user_counts in self.memory_user_message_counts.items():
            if guild_id != 'global':
                guild_conf = self.config.guild_from_id(guild_id)
                current_user_counts = await guild_conf.user_message_counts()
                for user_id, count in user_counts.items():
                    current_user_counts[user_id] = current_user_counts.get(user_id, 0) + count
                await guild_conf.user_message_counts.set(current_user_counts)

        for guild_id, users in self.memory_moderated_users.items():
            if guild_id == 'global':
                current_users = await self.config.global_moderated_users()
                for user_id, count in users.items():
                    current_users[user_id] = current_users.get(user_id, 0) + count
                await self.config.global_moderated_users.set(current_users)
            else:
                guild_conf = self.config.guild_from_id(guild_id)
                current_users = await guild_conf.moderated_users()
                for user_id, count in users.items():
                    current_users[user_id] = current_users.get(user_id, 0) + count
                await guild_conf.moderated_users.set(current_users)

        for guild_id, counter in self.memory_category_counter.items():
            if guild_id == 'global':
                current_counter = Counter(await self.config.global_category_counter())
                current_counter.update(counter)
                await self.config.global_category_counter.set(dict(current_counter))
            else:
                guild_conf = self.config.guild_from_id(guild_id)
                current_counter = Counter(await guild_conf.category_counter())
                current_counter.update(counter)
                await guild_conf.category_counter.set(dict(current_counter))

        # Save per-user violation history
        for guild_id, user_violations in self.memory_user_violations.items():
            guild_conf = self.config.guild_from_id(guild_id)
            current_violations = await guild_conf.user_violations()
            for user_id, violations in user_violations.items():
                # Append new violations to existing list
                if str(user_id) not in current_violations:
                    current_violations[str(user_id)] = []
                # Only keep the last 50 violations per user to avoid unbounded growth
                current_violations[str(user_id)] = (current_violations[str(user_id)] + violations)[-50:]
            await guild_conf.user_violations.set(current_violations)

        # Save per-user warning counts
        for guild_id, user_warnings in self.memory_user_warnings.items():
            guild_conf = self.config.guild_from_id(guild_id)
            current_warnings = await guild_conf.user_warnings()
            for user_id, count in user_warnings.items():
                current_warnings[str(user_id)] = current_warnings.get(str(user_id), 0) + count
            await guild_conf.user_warnings.set(current_warnings)

        # Clear in-memory statistics after saving
        self.memory_stats.clear()
        self.memory_user_message_counts.clear()
        self.memory_moderated_users.clear()
        self.memory_category_counter.clear()
        self.memory_user_violations.clear()
        self.memory_user_warnings.clear()
        # Also clear flagged image tracking after save
        self._flagged_image_for_message.clear()

    @commands.guild_only()
    @commands.group()
    async def omni(self, ctx):
        """
        Automated AI moderation for chats, images, and emotes powered by the latest OpenAI moderation models.
        
        Read more about **[omni-moderation-latest](<https://platform.openai.com/docs/models/omni-moderation-latest>)** or [visit OpenAI's website](<https://openai.com>) to learn more.
        """
        pass

    @omni.command()
    async def stats(self, ctx):
        """Show statistics of the moderation activity."""
        try:
            # Local statistics
            message_count = await self.config.guild(ctx.guild).message_count()
            moderated_count = await self.config.guild(ctx.guild).moderated_count()
            moderated_users = await self.config.guild(ctx.guild).moderated_users()
            category_counter = Counter(await self.config.guild(ctx.guild).category_counter())
            image_count = await self.config.guild(ctx.guild).image_count()
            moderated_image_count = await self.config.guild(ctx.guild).moderated_image_count()
            timeout_count = await self.config.guild(ctx.guild).timeout_count()
            total_timeout_duration = await self.config.guild(ctx.guild).total_timeout_duration()
            too_weak_votes = await self.config.guild(ctx.guild).too_weak_votes()
            too_tough_votes = await self.config.guild(ctx.guild).too_tough_votes()
            just_right_votes = await self.config.guild(ctx.guild).just_right_votes()
            user_warnings = await self.config.guild(ctx.guild).user_warnings()

            member_count = ctx.guild.member_count
            moderated_message_percentage = (moderated_count / message_count * 100) if message_count > 0 else 0
            moderated_user_percentage = (len(moderated_users) / member_count * 100) if member_count > 0 else 0
            moderated_image_percentage = (moderated_image_count / image_count * 100) if image_count > 0 else 0

            # Calculate estimated moderator time saved
            time_saved_seconds = (moderated_count * 5) + message_count  # 5 seconds per moderated message + 1 second per message read
            time_saved_minutes, time_saved_seconds = divmod(time_saved_seconds, 60)
            time_saved_hours, time_saved_minutes = divmod(time_saved_minutes, 60)
            time_saved_days, time_saved_hours = divmod(time_saved_hours, 24)

            if time_saved_days > 0:
                time_saved_str = f"**{time_saved_days}** day{'s' if time_saved_days != 1 else ''}, **{time_saved_hours}** hour{'s' if time_saved_hours != 1 else ''}"
            elif time_saved_hours > 0:
                time_saved_str = f"**{time_saved_hours}** hour{'s' if time_saved_hours != 1 else ''}, **{time_saved_minutes}** minute{'s' if time_saved_minutes != 1 else ''}"
            elif time_saved_minutes > 0:
                time_saved_str = f"**{time_saved_minutes}** minute{'s' if time_saved_minutes != 1 else ''}, **{time_saved_seconds}** second{'s' if time_saved_seconds != 1 else ''}"
            else:
                time_saved_str = f"**{time_saved_seconds}** second{'s' if time_saved_seconds != 1 else ''}"

            # Calculate total timeout duration in a readable format
            timeout_days, timeout_hours = divmod(total_timeout_duration, 1440)  # 1440 minutes in a day
            timeout_hours, timeout_minutes = divmod(timeout_hours, 60)

            if timeout_days > 0:
                timeout_duration_str = f"**{timeout_days}** day{'s' if timeout_days != 1 else ''}, **{timeout_hours}** hour{'s' if timeout_hours != 1 else ''}"
            elif timeout_hours > 0:
                timeout_duration_str = f"**{timeout_hours}** hour{'s' if timeout_hours != 1 else ''}, **{timeout_minutes}** minute{'s' if timeout_minutes != 1 else ''}"
            else:
                timeout_duration_str = f"**{timeout_minutes}** minute{'s' if timeout_minutes != 1 else ''}"

            top_categories = category_counter.most_common(5)
            top_categories_bullets = "\n".join([f"- **{cat.capitalize()}** x{count:,}" for cat, count in top_categories])
            
            # Add warning stats
            total_warnings = sum(user_warnings.values()) if user_warnings else 0
            warned_users = len([uid for uid, count in (user_warnings or {}).items() if count > 0])
            warning_stats = f"**{total_warnings}** warning{'s' if total_warnings != 1 else ''} issued to **{warned_users}** user{'s' if warned_users != 1 else ''}"

            embed = discord.Embed(title="✨ AI is hard at work for you, here's everything Omni knows...", color=0xfffffe)
            embed.add_field(name=f"In {ctx.guild.name}", value="", inline=False)
            embed.add_field(name="Messages processed", value=f"**{message_count:,}** message{'s' if message_count != 1 else ''}", inline=True)
            embed.add_field(name="Messages moderated", value=f"**{moderated_count:,}** message{'s' if moderated_count != 1 else ''} ({moderated_message_percentage:.2f}%)", inline=True)
            embed.add_field(name="Users punished", value=f"**{len(moderated_users):,}** user{'s' if len(moderated_users) != 1 else ''} ({moderated_user_percentage:.2f}%)", inline=True)
            embed.add_field(name="Images processed", value=f"**{image_count:,}** image{'s' if image_count != 1 else ''}", inline=True)
            embed.add_field(name="Images moderated", value=f"**{moderated_image_count:,}** image{'s' if moderated_image_count != 1 else ''} ({moderated_image_percentage:.2f}%)", inline=True)
            embed.add_field(name="Timeouts issued", value=f"**{timeout_count:,}** timeout{'s' if timeout_count != 1 else ''}", inline=True)
            embed.add_field(name="Total timeout duration", value=f"{timeout_duration_str}", inline=True)
            embed.add_field(name="Warnings issued", value=warning_stats, inline=True)
            embed.add_field(name="Estimated minimum staff time saved", value=f"{time_saved_str} of **hands-on-keyboard** time to simply read and moderate automatically screened content.", inline=False)
            embed.add_field(name="Most frequent flags", value=top_categories_bullets, inline=False)
            embed.add_field(name="Feedback", value=f"**{too_weak_votes}** votes for too weak, **{too_tough_votes}** votes for too tough, **{just_right_votes}** votes for just right", inline=False)

            # Show global stats if in more than 45 servers
            if len(self.bot.guilds) > 45:
                # Global statistics
                global_message_count = await self.config.global_message_count()
                global_moderated_count = await self.config.global_moderated_count()
                global_moderated_users = await self.config.global_moderated_users()
                global_category_counter = Counter(await self.config.global_category_counter())
                global_image_count = await self.config.global_image_count()
                global_moderated_image_count = await self.config.global_moderated_image_count()
                global_timeout_count = await self.config.global_timeout_count()
                global_total_timeout_duration = await self.config.global_total_timeout_duration()

                # Global warnings
                global_total_warnings = 0
                global_warned_users = 0
                for guild in self.bot.guilds:
                    try:
                        user_warnings = await self.config.guild(guild).user_warnings()
                        global_total_warnings += sum(user_warnings.values()) if user_warnings else 0
                        global_warned_users += len([uid for uid, count in (user_warnings or {}).items() if count > 0])
                    except Exception:
                        continue
                global_warning_stats = f"**{global_total_warnings}** warning{'s' if global_total_warnings != 1 else ''} issued to **{global_warned_users}** user{'s' if global_warned_users != 1 else ''}"

                global_moderated_message_percentage = (global_moderated_count / global_message_count * 100) if global_message_count > 0 else 0
                global_moderated_image_percentage = (global_moderated_image_count / global_image_count * 100) if global_image_count > 0 else 0

                # Calculate global estimated moderator time saved
                global_time_saved_seconds = (global_moderated_count * 5) + global_message_count  # 5 seconds per moderated message + 1 second per message read
                global_time_saved_minutes, global_time_saved_seconds = divmod(global_time_saved_seconds, 60)
                global_time_saved_hours, global_time_saved_minutes = divmod(global_time_saved_minutes, 60)
                global_time_saved_days, global_time_saved_hours = divmod(global_time_saved_hours, 24)

                if global_time_saved_days > 0:
                    global_time_saved_str = f"**{global_time_saved_days}** day{'s' if global_time_saved_days != 1 else ''}, **{global_time_saved_hours}** hour{'s' if global_time_saved_hours != 1 else ''}"
                elif global_time_saved_hours > 0:
                    global_time_saved_str = f"**{global_time_saved_hours}** hour{'s' if global_time_saved_hours != 1 else ''}, **{global_time_saved_minutes}** minute{'s' if global_time_saved_minutes != 1 else ''}"
                elif global_time_saved_minutes > 0:
                    global_time_saved_str = f"**{global_time_saved_minutes}** minute{'s' if global_time_saved_minutes != 1 else ''}, **{global_time_saved_seconds}** second{'s' if global_time_saved_seconds != 1 else ''}"
                else:
                    global_time_saved_str = f"**{global_time_saved_seconds}** second{'s' if global_time_saved_seconds != 1 else ''}"

                # Calculate global total timeout duration in a readable format
                global_timeout_days, global_timeout_hours = divmod(global_total_timeout_duration, 1440)  # 1440 minutes in a day
                global_timeout_hours, global_timeout_minutes = divmod(global_timeout_hours, 60)

                if global_timeout_days > 0:
                    global_timeout_duration_str = f"**{global_timeout_days}** day{'s' if global_timeout_days != 1 else ''}, **{global_timeout_hours}** hour{'s' if global_timeout_hours != 1 else ''}"
                elif global_timeout_hours > 0:
                    global_timeout_duration_str = f"**{global_timeout_hours}** hour{'s' if global_timeout_hours != 1 else ''}, **{global_timeout_minutes}** minute{'s' if global_timeout_minutes != 1 else ''}"
                else:
                    global_timeout_duration_str = f"**{global_timeout_minutes}** minute{'s' if global_timeout_minutes != 1 else ''}"

                global_top_categories = global_category_counter.most_common(5)
                global_top_categories_bullets = "\n".join([f"- **{cat.capitalize()}** x{count:,}" for cat, count in global_top_categories])
                embed.add_field(name="Across all monitored servers", value="", inline=False)
                embed.add_field(name="Messages processed", value=f"**{global_message_count:,}** message{'s' if global_message_count != 1 else ''}", inline=True)
                embed.add_field(name="Messages moderated", value=f"**{global_moderated_count:,}** message{'s' if global_moderated_count != 1 else ''} ({global_moderated_message_percentage:.2f}%)", inline=True)
                embed.add_field(name="Users punished", value=f"**{len(global_moderated_users):,}** user{'s' if len(global_moderated_users) != 1 else ''}", inline=True)
                embed.add_field(name="Images processed", value=f"**{global_image_count:,}** image{'s' if global_image_count != 1 else ''}", inline=True)
                embed.add_field(name="Images moderated", value=f"**{global_moderated_image_count:,}** image{'s' if global_moderated_image_count != 1 else ''} ({global_moderated_image_percentage:.2f}%)", inline=True)
                embed.add_field(name="Timeouts issued", value=f"**{global_timeout_count:,}** timeout{'s' if global_timeout_count != 1 else ''}", inline=True)
                embed.add_field(name="Total timeout duration", value=f"{global_timeout_duration_str}", inline=True)
                embed.add_field(name="Warnings issued", value=global_warning_stats, inline=True)
                embed.add_field(name="Estimated minimum staff time saved", value=f"{global_time_saved_str} of **hands-on-keyboard** time to simply read and moderate automatically screened content.", inline=False)
                embed.add_field(name="Most frequent flags", value=global_top_categories_bullets, inline=False)

            embed.set_footer(text="Statistics are subject to vary and change as data is collected")
            await ctx.send(embed=embed)
        except Exception as e:
            raise RuntimeError(f"Failed to display stats: {e}")

    @omni.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def history(self, ctx, user: discord.Member = None):
        """
        Show the violation history for a user in this server.
        If no user is provided, shows your own history (if you are not a bot).
        Shows 9 violations per page, with buttons to scroll if there are more.
        Also includes a graph of abuse trends over time.
        """
        try:
            guild = ctx.guild
            if user is None:
                user = ctx.author
            # Only staff can view others' history
            if user != ctx.author and not (ctx.author.guild_permissions.administrator or ctx.author.guild_permissions.manage_guild):
                await ctx.send("You do not have permission to view other users' violation history.")
                return

            guild_conf = self.config.guild(guild)
            user_violations = await guild_conf.user_violations()
            violations = user_violations.get(str(user.id), [])

            # Add in-memory violations not yet saved
            mem_violations = self.memory_user_violations[guild.id].get(user.id, [])
            if mem_violations:
                violations = (violations + mem_violations)[-50:]
            else:
                violations = violations[-50:]

            # Get warning count for this user
            user_warnings = await guild_conf.user_warnings()
            warning_count = user_warnings.get(str(user.id), 0)
            mem_warning_count = self.memory_user_warnings[guild.id].get(user.id, 0)
            total_warning_count = warning_count + mem_warning_count

            if not violations and total_warning_count == 0:
                await ctx.send(f"No violations or warnings found for {user.mention}.")
                return

            # --- Generate abuse trend graph ---
            # We'll plot the number of violations per week (or per day if < 21 days)
            timestamps = [v.get("timestamp") for v in violations if v.get("timestamp")]
            file = None
            image_url = None
            temp_file = None
            if timestamps:
                # Convert to datetime objects
                datetimes = [datetime.fromtimestamp(ts, tz=timezone.utc) for ts in timestamps]
                datetimes.sort()
                if len(datetimes) > 0:
                    first = datetimes[0]
                    last = datetimes[-1]
                    days_span = (last - first).days + 1
                    if days_span <= 21:
                        # Per day
                        group_fmt = "%Y-%m-%d"
                        label_fmt = "%b %d"
                        date_list = [(first + timedelta(days=i)).date() for i in range(days_span)]
                        group_by = lambda dt: dt.strftime(group_fmt)
                    else:
                        # Per week
                        group_fmt = "%Y-W%W"
                        label_fmt = "W%W\n%Y"
                        # Find all week starts in range
                        week_starts = []
                        current = first - timedelta(days=first.weekday())
                        while current <= last:
                            week_starts.append(current.date())
                            current += timedelta(days=7)
                        date_list = week_starts
                        group_by = lambda dt: dt.strftime(group_fmt)
                    # Count violations per group
                    from collections import Counter
                    grouped = Counter(group_by(dt) for dt in datetimes)
                    # Prepare x/y for plot
                    x_labels = []
                    y_counts = []
                    for d in date_list:
                        if days_span <= 21:
                            key = d.strftime(group_fmt)
                            label = d.strftime(label_fmt)
                        else:
                            key = d.strftime(group_fmt)
                            label = d.strftime(label_fmt)
                        x_labels.append(label)
                        y_counts.append(grouped.get(key, 0))
                    # Plot
                    plt.style.use("seaborn-v0_8-darkgrid")
                    fig, ax = plt.subplots(figsize=(7, 3))
                    ax.plot(x_labels, y_counts, marker="o", color="#ff4545", linewidth=2)
                    ax.set_title(f"Abuse trend for {user.display_name}", fontsize=13)
                    ax.set_xlabel("Date" if days_span <= 21 else "Week")
                    ax.set_ylabel("Violations")
                    ax.set_ylim(bottom=0)
                    plt.xticks(rotation=45, ha="right", fontsize=8)
                    plt.tight_layout()
                    # Save to tempfile
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        plt.savefig(tmp, format="png", bbox_inches="tight", dpi=120)
                        temp_file = tmp.name
                    plt.close(fig)
                    file = discord.File(temp_file, filename="abuse_trend.png")
                    image_url = "attachment://abuse_trend.png"
                else:
                    file = None
                    image_url = None
            else:
                file = None
                image_url = None

            # Pagination setup
            VIOLATIONS_PER_PAGE = 9
            total_violations = len(violations)
            total_pages = max(1, math.ceil(total_violations / VIOLATIONS_PER_PAGE))

            def make_embed(page: int):
                start = page * VIOLATIONS_PER_PAGE
                end = start + VIOLATIONS_PER_PAGE
                violations_to_show = violations[start:end]
                embed = discord.Embed(
                    title=f"Violation history for {user.display_name}",
                    color=0xff4545,
                    description=f"Showing violations {start+1}-{min(end, total_violations)} of {total_violations} for {user.mention}."
                )
                for v in violations_to_show:
                    ts = v.get("timestamp")
                    time_str = f"<t:{int(ts)}:R>" if ts else "Unknown time"
                    content = v.get("content", "*No content*")
                    categories = v.get("categories", {})
                    cat_str = ", ".join(f"{cat}: {score*100:.0f}%" for cat, score in categories.items())
                    channel_id = v.get("channel_id")
                    channel_mention = f"<#{channel_id}>" if channel_id else "Unknown"
                    embed.add_field(
                        name=f"{time_str} in {channel_mention}",
                        value=f"**Categories:** {cat_str}\n**Message:** {content[:300]}{'...' if len(content) > 300 else ''}",
                        inline=False
                    )
                embed.add_field(
                    name="Warnings issued",
                    value=f"{total_warning_count} warning{'s' if total_warning_count != 1 else ''} for this user.",
                    inline=False
                )
                embed.set_footer(text=f"Page {page+1}/{total_pages} • Only the last 50 violations are kept per user. Warnings are cumulative.")
                if image_url:
                    embed.set_image(url=image_url)
                return embed

            # If only one page, just send the embed
            if total_pages == 1:
                embed = make_embed(0)
                if file:
                    await ctx.send(embed=embed, file=file)
                else:
                    await ctx.send(embed=embed)
                if temp_file:
                    import os
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass
                return

            class ViolationHistoryView(View):
                def __init__(self, author: discord.User, timeout=120):
                    super().__init__(timeout=timeout)
                    self.page = 0
                    self.author = author

                async def update_message(self, interaction):
                    embed = make_embed(self.page)
                    if file:
                        await interaction.response.edit_message(embed=embed, view=self, attachments=[file])
                    else:
                        await interaction.response.edit_message(embed=embed, view=self)

                @discord.ui.button(label="⏮️", style=discord.ButtonStyle.secondary, custom_id="first_page")
                async def first_page(self, interaction: discord.Interaction, button: Button):
                    if interaction.user != self.author:
                        await interaction.response.send_message("You can't control this menu.", ephemeral=True)
                        return
                    if self.page != 0:
                        self.page = 0
                        await self.update_message(interaction)
                    else:
                        await interaction.response.defer()

                @discord.ui.button(label="⬅️", style=discord.ButtonStyle.secondary, custom_id="prev_page")
                async def prev_page(self, interaction: discord.Interaction, button: Button):
                    if interaction.user != self.author:
                        await interaction.response.send_message("You can't control this menu.", ephemeral=True)
                        return
                    if self.page > 0:
                        self.page -= 1
                        await self.update_message(interaction)
                    else:
                        await interaction.response.defer()

                @discord.ui.button(label="➡️", style=discord.ButtonStyle.secondary, custom_id="next_page")
                async def next_page(self, interaction: discord.Interaction, button: Button):
                    if interaction.user != self.author:
                        await interaction.response.send_message("You can't control this menu.", ephemeral=True)
                        return
                    if self.page < total_pages - 1:
                        self.page += 1
                        await self.update_message(interaction)
                    else:
                        await interaction.response.defer()

                @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary, custom_id="last_page")
                async def last_page(self, interaction: discord.Interaction, button: Button):
                    if interaction.user != self.author:
                        await interaction.response.send_message("You can't control this menu.", ephemeral=True)
                        return
                    if self.page != total_pages - 1:
                        self.page = total_pages - 1
                        await self.update_message(interaction)
                    else:
                        await interaction.response.defer()

            view = ViolationHistoryView(ctx.author)
            embed = make_embed(0)
            if file:
                await ctx.send(embed=embed, view=view, file=file)
            else:
                await ctx.send(embed=embed, view=view)
            if temp_file:
                import os
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
        except Exception as e:
            raise RuntimeError(f"Failed to display violation history: {e}")

    @omni.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def settings(self, ctx):
        """Show the current settings of the cog."""
        try:
            guild = ctx.guild
            moderation_threshold = await self.config.guild(guild).moderation_threshold()
            timeout_duration = await self.config.guild(guild).timeout_duration()
            log_channel_id = await self.config.guild(guild).log_channel()
            debug_mode = await self.config.guild(guild).debug_mode()
            whitelisted_channels = await self.config.guild(guild).whitelisted_channels()
            whitelisted_roles = await self.config.guild(guild).whitelisted_roles()
            whitelisted_users = await self.config.guild(guild).whitelisted_users()
            whitelisted_categories = await self.config.guild(guild).whitelisted_categories()
            moderation_enabled = await self.config.guild(guild).moderation_enabled()
            delete_violatory_messages = await self.config.guild(guild).delete_violatory_messages()
            bypass_nsfw = await self.config.guild(guild).bypass_nsfw()
            monitoring_warning_enabled = await self.config.guild(guild).monitoring_warning_enabled()

            log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
            log_channel_name = log_channel.mention if log_channel else "Not set"
            whitelisted_channels_names = ", ".join([guild.get_channel(ch_id).mention for ch_id in whitelisted_channels if guild.get_channel(ch_id)]) or "None"
            whitelisted_roles_names = ", ".join([guild.get_role(role_id).mention for role_id in whitelisted_roles if guild.get_role(role_id)]) or "None"
            whitelisted_users_names = ", ".join([f"<@{user_id}>" for user_id in whitelisted_users]) or "None"
            whitelisted_categories_names = ", ".join([cat.name for cat in guild.categories if cat.id in whitelisted_categories]) or "None"
            monitoring_warning_status = "Enabled" if monitoring_warning_enabled else "Disabled"

            embed = discord.Embed(title="Omni settings", color=0xfffffe)
            embed.add_field(name="Whitelisted channels", value=whitelisted_channels_names, inline=True)
            embed.add_field(name="Moderative threshold", value=f"{moderation_threshold * 100:.2f}%", inline=True)
            embed.add_field(name="Timeout duration", value=f"{timeout_duration} minutes", inline=True)
            embed.add_field(name="Whitelisted roles", value=whitelisted_roles_names, inline=True)
            embed.add_field(name="Log channel", value=log_channel_name, inline=True)
            embed.add_field(name="Moderation enabled", value="Yes" if moderation_enabled else "No", inline=True)
            embed.add_field(name="Whitelisted users", value=whitelisted_users_names, inline=True)
            embed.add_field(name="Deletion enabled", value="Yes" if delete_violatory_messages else "No", inline=True)
            embed.add_field(name="Debug mode", value="Enabled" if debug_mode else "Disabled", inline=True)
            embed.add_field(name="Whitelisted categories", value=whitelisted_categories_names, inline=True)
            embed.add_field(name="Auto whitelist NSFW channels", value="Enabled" if bypass_nsfw else "Disabled", inline=True)
            embed.add_field(name="Monitoring warning", value=monitoring_warning_status, inline=True)

            await ctx.send(embed=embed)
        except Exception as e:
            raise RuntimeError(f"Failed to display settings: {e}")

    @omni.command()
    @commands.is_owner()
    async def cleanup(self, ctx):
        """Reset all server and global statistics and counters."""
        try:
            # Warning message
            warning_embed = discord.Embed(
                title="You're about to perform a destructive operation",
                description="This operation is computationally intensive and will reset all server and global statistics and counters for Omni. **This deletion is irreversible.**\n\nPlease confirm by typing `CONFIRM`.",
                color=0xff4545
            )
            await ctx.send(embed=warning_embed)

            def check(m):
                return m.author == ctx.author and m.content == "CONFIRM" and m.channel == ctx.channel

            try:
                await self.bot.wait_for('message', check=check, timeout=30)
            except asyncio.TimeoutError:
                await ctx.send("Cleanup operation cancelled due to timeout.")
                return

            # Reset all guild statistics
            all_guilds = await self.config.all_guilds()
            for guild_id in all_guilds:
                guild_conf = self.config.guild_from_id(guild_id)
                await guild_conf.message_count.set(0)
                await guild_conf.moderated_count.set(0)
                await guild_conf.moderated_users.set({})
                await guild_conf.category_counter.set({})
                await guild_conf.user_message_counts.set({})
                await guild_conf.image_count.set(0)
                await guild_conf.moderated_image_count.set(0)
                await guild_conf.timeout_count.set(0)
                await guild_conf.total_timeout_duration.set(0)
                await guild_conf.too_weak_votes.set(0)
                await guild_conf.too_tough_votes.set(0)
                await guild_conf.just_right_votes.set(0)
                await guild_conf.user_violations.set({})
                await guild_conf.user_warnings.set({})

            # Reset global statistics
            await self.config.global_message_count.set(0)
            await self.config.global_moderated_count.set(0)
            await self.config.global_moderated_users.set({})
            await self.config.global_category_counter.set({})
            await self.config.global_image_count.set(0)
            await self.config.global_moderated_image_count.set(0)
            await self.config.global_timeout_count.set(0)
            await self.config.global_total_timeout_duration.set(0)

            # Clear in-memory statistics
            self.memory_stats.clear()
            self.memory_user_message_counts.clear()
            self.memory_moderated_users.clear()
            self.memory_category_counter.clear()
            self.memory_user_violations.clear()
            self.memory_user_warnings.clear()
            self._reminder_sent_at.clear()
            self._timeout_issued_for_message.clear()
            self._deleted_messages.clear()
            self._flagged_image_for_message.clear()

            # Confirmation message
            confirmation_embed = discord.Embed(
                title="Data cleanup completed",
                description="All statistics and counters have been reset.",
                color=0x2bbd8e
            )
            await ctx.send(embed=confirmation_embed)

        except Exception as e:
            raise RuntimeError(f"Failed to reset statistics: {e}")


# VALIDATED COMMANDS

    @omni.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def threshold(self, ctx, threshold: float):
        """
        Set the moderation threshold for message sensitivity.

        The threshold value should be between 0 and 1, where:
        - 0.00 represents a very sensitive setting, capturing more messages for moderation.
        - 1.00 represents a barely sensitive setting, allowing most messages to pass through without moderation.

        Adjust this setting based on your community's needs for moderation sensitivity.

        **Recommendations**
        - For general communities, a threshold of `0.50` is often effective.
        - For professional communities (or if stricter moderation is preferred), consider a threshold below `0.40`.
        - For more lenient settings, a threshold above `0.70` might be suitable.
        """
        try:
            if 0 <= threshold <= 1:
                await self.config.guild(ctx.guild).moderation_threshold.set(threshold)
                await ctx.send(f"Moderation threshold set to {threshold}.")
            else:
                await ctx.send("Threshold must be between 0 and 1.")
        except Exception as e:
            raise RuntimeError(f"Failed to set threshold: {e}") 

    @commands.cooldown(1, 86400, commands.BucketType.user)
    @omni.command()
    async def vote(self, ctx):
        """Give feedback on the server's agentic moderation"""
        try:
            guild = ctx.guild
            log_channel_id = await self.config.guild(guild).log_channel()
            log_channel = guild.get_channel(log_channel_id) if log_channel_id else None

            if not log_channel:
                await ctx.send("Ask a staff member to set a logs channel for Omni before you can submit feedback on the moderation")
                return

            embed = discord.Embed(
                title="How's our agentic moderation?",
                description=f"Your feedback matters and will be used to help us tune the assistive AI used in {ctx.guild.name}.",
                color=0x45ABF5
            )

            view = discord.ui.View()

            async def vote_callback(interaction, vote_type):
                if interaction.user != ctx.author:
                    await interaction.response.send_message(f"This feedback session doesn't belong to you.\n\nIf you'd like to provide feedback on the agentic moderation in this server, please use `{ctx.clean_prefix}omni vote` to start your own feedback session.", ephemeral=True)
                    return

                # Check if the vote can affect the threshold
                last_vote_time = await self.config.guild(guild).last_vote_time()
                current_time = datetime.utcnow()
                threshold_adjusted = False

                if not last_vote_time or (current_time - datetime.fromisoformat(last_vote_time)).total_seconds() >= 86400:
                    moderation_threshold = await self.config.guild(guild).moderation_threshold()
                    old_threshold = moderation_threshold
                    if vote_type == "too weak":
                        moderation_threshold = max(0, moderation_threshold - 0.01)
                    elif vote_type == "too strict":
                        moderation_threshold = min(1, moderation_threshold + 0.01)
                    await self.config.guild(guild).moderation_threshold.set(moderation_threshold)
                    await self.config.guild(guild).last_vote_time.set(current_time.isoformat())
                    threshold_adjusted = True

                if vote_type == "too weak":
                    await self.config.guild(guild).too_weak_votes.set(await self.config.guild(guild).too_weak_votes() + 1)
                    tips = f"- Review your channels to see what your members have been discussing\n- Evaluate appropriateness according to server rules and Discord policies\n- Consider lowering the threshold to catch more potential issues. - `{ctx.clean_prefix}omni threshold`"
                elif vote_type == "too strict":
                    await self.config.guild(guild).too_tough_votes.set(await self.config.guild(guild).too_tough_votes() + 1)
                    tips = f"- Review your channels to see what your members have been discussing\n- Evaluate appropriateness according to server rules and Discord policies\n- Consider raising the set threshold to allow more freedom. - `{ctx.clean_prefix}omni threshold`"
                elif vote_type == "just right":
                    await self.config.guild(guild).just_right_votes.set(await self.config.guild(guild).just_right_votes() + 1)
                    tips = f"- The current moderation settings seem to be well-balanced.\n- Continue monitoring to ensure it remains effective."

                feedback_embed = discord.Embed(
                    title="🤖 Feedback received",
                    description=f"User <@{ctx.author.id}> submitted feedback that the AI moderation is **{vote_type}**.\n\n{tips}",
                    color=0xfffffe
                )

                if threshold_adjusted:
                    feedback_embed.description += f"\n\n**Omni made automatic, intelligent adjustments based on user feedback.**\nPrevious threshold: `{old_threshold}`\nUpdated threshold: `{moderation_threshold}`"

                await log_channel.send(embed=feedback_embed)

                # Update the original embed and remove buttons
                updated_embed = discord.Embed(
                    title="Feedback recorded",
                    description=f"Thank you for helping improve the assistive AI used in this server.",
                    color=0x2bbd8e
                )
                if threshold_adjusted:
                    updated_embed.description += " Based on your feedback, the moderation agent has been adjusted. Please continue to provide feedback as needed."
                await interaction.message.edit(embed=updated_embed, view=None)
                await interaction.response.send_message("Thank you for taking the time to help make this server a better place. If you have additional feedback about this server's AI-assisted moderation, please contact a member of the staff or administration team.", ephemeral=True)

            # Button callbacks must be coroutines, so use partial or closure, not lambda with coroutine
            async def too_weak_callback(interaction):
                await vote_callback(interaction, "too weak")
            async def just_right_callback(interaction):
                await vote_callback(interaction, "just right")
            async def too_tough_callback(interaction):
                await vote_callback(interaction, "too strict")

            too_weak_button = discord.ui.Button(label="Moderation is too forgiving", style=discord.ButtonStyle.red)
            just_right_button = discord.ui.Button(label="Moderation is just right", style=discord.ButtonStyle.green)
            too_tough_button = discord.ui.Button(label="Moderation is too strict", style=discord.ButtonStyle.red)

            too_weak_button.callback = too_weak_callback
            just_right_button.callback = just_right_callback
            too_tough_button.callback = too_tough_callback

            view.add_item(too_weak_button)
            view.add_item(just_right_button)
            view.add_item(too_tough_button)

            await ctx.send(embed=embed, view=view)

        except Exception as e:
            raise RuntimeError(f"Failed to initiate vote: {e}")

    @omni.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def toggle(self, ctx):
        """Toggle automatic moderation on or off."""
        try:
            guild = ctx.guild
            current_status = await self.config.guild(guild).moderation_enabled()
            new_status = not current_status
            await self.config.guild(guild).moderation_enabled.set(new_status)
            status = "enabled" if new_status else "disabled"
            await ctx.send(f"Automatic moderation {status}.")
        except Exception as e:
            raise RuntimeError(f"Failed to toggle automatic moderation: {e}")

    @omni.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def disclaimer(self, ctx):
        """
        Toggle the monitoring warning message that is periodically sent to channels.
        Disabling this warning is not recommended and may have legal or compliance implications.
        """
        try:
            guild = ctx.guild
            current_status = await self.config.guild(guild).monitoring_warning_enabled()
            if current_status:
                # If currently enabled, require confirmation to disable
                warning_embed = discord.Embed(
                    title="Confirm acceptance of liability",
                    description=(
                        "You are about to **disable** the periodic monitoring privacy warning message.\n\n"
                        "Disabling this warning may violate Discord's Terms of Service, privacy laws, or your own server's compliance requirements. "
                        "It is your responsibility to ensure that your members are properly informed that their messages are subject to automated moderation, logging, and analysis.\n\n"
                        "If you understand the risks and still wish to proceed, type `DISABLE` in this channel within 30 seconds."
                    ),
                    color=0xff4545
                )
                await ctx.send(embed=warning_embed)

                def check(m):
                    return m.author == ctx.author and m.content.strip().upper() == "DISABLE" and m.channel == ctx.channel

                try:
                    await self.bot.wait_for('message', check=check, timeout=30)
                except asyncio.TimeoutError:
                    await ctx.send("Operation cancelled. Monitoring warning remains enabled.")
                    return

                await self.config.guild(guild).monitoring_warning_enabled.set(False)
                await ctx.send("Monitoring warning has been **disabled**. You are responsible for informing your members about moderation and logging.")
            else:
                # Enable without confirmation
                await self.config.guild(guild).monitoring_warning_enabled.set(True)
                await ctx.send("Monitoring warning has been **enabled**. Members will be periodically notified that conversations are subject to moderation.")
        except Exception as e:
            raise RuntimeError(f"Failed to toggle monitoring warning: {e}")

    @omni.command()
    async def reasons(self, ctx):
        """Explains how the AI moderator labels and categorizes content"""
        try:
            categories = {
                "harassment": "Content that expresses, incites, or promotes harassing language towards any target.",
                "harassment/threatening": "Harassment content that also includes violence or serious harm towards any target.",
                "hate": "Content that expresses, incites, or promotes hate based on race, gender, ethnicity, religion, nationality, sexual orientation, disability status, or caste.",
                "hate/threatening": "Hateful content that also includes violence or serious harm towards the targeted group based on race, gender, ethnicity, religion, nationality, sexual orientation, disability status, or caste.",
                "illicit": "Content that gives advice or instruction on how to commit illicit acts.",
                "illicit/violent": "The same types of content flagged by the illicit category, but also includes references to violence or procuring a weapon.",
                "self-harm": "Content that promotes, encourages, or depicts acts of self-harm, such as suicide, cutting, and eating disorders.",
                "self-harm/intent": "Content where the speaker expresses that they are engaging or intend to engage in acts of self-harm.",
                "self-harm/instructions": "Content that encourages performing acts of self-harm or that gives instructions or advice on how to commit such acts.",
                "sexual": "Content meant to arouse sexual excitement or that promotes sexual services.",
                "sexual/minors": "Sexual content that includes an individual who is under 18 years old.",
                "violence": "Content that depicts death, violence, or physical injury.",
                "violence/graphic": "Content that depicts death, violence, or physical injury in graphic detail."
            }

            embed = discord.Embed(title="What the AI moderator is looking for", color=0xfffffe)
            for category, description in categories.items():
                embed.add_field(name=category.capitalize(), value=description, inline=False)

            await ctx.send(embed=embed)
        except Exception as e:
            raise RuntimeError(f"Failed to display reasons: {e}")

    @omni.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def delete(self, ctx):
        """Toggle whether violatory messages are deleted or not."""
        try:
            guild = ctx.guild
            current_status = await self.config.guild(guild).delete_violatory_messages()
            new_status = not current_status
            await self.config.guild(guild).delete_violatory_messages.set(new_status)
            status = "enabled" if new_status else "disabled"
            await ctx.send(f"Deletion of violatory messages {status}.")
        except Exception as e:
            raise RuntimeError(f"Failed to toggle message deletion: {e}")

    @omni.command(hidden=True)
    @commands.is_owner()
    async def globalstate(self, ctx):
        """Toggle default moderation state for new servers."""
        try:
            # Get the current state
            current_state = await self.config.moderation_enabled()
            # Toggle the state
            new_state = not current_state
            await self.config.moderation_enabled.set(new_state)
            status = "enabled" if new_state else "disabled"
            await ctx.send(f"Moderation is now {status} by default for new servers.")
        except Exception as e:
            raise RuntimeError(f"Failed to toggle default moderation state: {e}")

    @omni.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def timeout(self, ctx, duration: int):
        """Set the timeout duration in minutes (0 for no timeout)."""
        try:
            if duration >= 0:
                await self.config.guild(ctx.guild).timeout_duration.set(duration)
                await ctx.send(f"Timeout duration set to {duration} minutes.")
            else:
                await ctx.send("Timeout duration must be 0 or greater.")
        except Exception as e:
            raise RuntimeError(f"Failed to set timeout duration: {e}")

    @omni.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def logs(self, ctx, channel: discord.TextChannel):
        """Set the channel to log moderated messages."""
        try:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            await ctx.send(f"Log channel set to {channel.mention}.")
        except Exception as e:
            raise RuntimeError(f"Failed to set log channel: {e}")

    @omni.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def whitelist(self, ctx):
        """
        Manage whitelisting/"bypassing" parts of the server from moderation.
        """
        pass

    @whitelist.command(name="channel")
    async def whitelist_channel(self, ctx, channel: discord.TextChannel):
        """Bypass/unbypass channels"""
        try:
            guild = ctx.guild
            whitelisted_channels = await self.config.guild(guild).whitelisted_channels()
            changelog = []

            if channel.id in whitelisted_channels:
                whitelisted_channels.remove(channel.id)
                changelog.append(f"Removed: {channel.mention}")
            else:
                whitelisted_channels.append(channel.id)
                changelog.append(f"Added: {channel.mention}")

            await self.config.guild(guild).whitelisted_channels.set(whitelisted_channels)

            if changelog:
                changelog_message = "\n".join(changelog)
                embed = discord.Embed(title="Whitelist Changelog", description=changelog_message, color=discord.Color.blue())
                await ctx.send(embed=embed)
        except Exception as e:
            raise RuntimeError(f"Failed to update channel whitelist: {e}")

    @whitelist.command(name="role")
    async def whitelist_role(self, ctx, role: discord.Role):
        """Bypass/unbypass users in a role"""
        try:
            guild = ctx.guild
            whitelisted_roles = await self.config.guild(guild).whitelisted_roles()
            changelog = []

            if role.id in whitelisted_roles:
                whitelisted_roles.remove(role.id)
                changelog.append(f"Removed: {role.mention}")
            else:
                whitelisted_roles.append(role.id)
                changelog.append(f"Added: {role.mention}")

            await self.config.guild(guild).whitelisted_roles.set(whitelisted_roles)

            if changelog:
                changelog_message = "\n".join(changelog)
                embed = discord.Embed(title="Whitelist Changelog", description=changelog_message, color=discord.Color.blue())
                await ctx.send(embed=embed)
        except Exception as e:
            raise RuntimeError(f"Failed to update role whitelist: {e}")

    @whitelist.command(name="user")
    async def whitelist_user(self, ctx, user: discord.User):
        """Bypass/unbypass a user"""
        try:
            guild = ctx.guild
            whitelisted_users = await self.config.guild(guild).whitelisted_users()
            changelog = []

            if user.id in whitelisted_users:
                whitelisted_users.remove(user.id)
                changelog.append(f"Removed: {user.mention}")
            else:
                whitelisted_users.append(user.id)
                changelog.append(f"Added: {user.mention}")

            await self.config.guild(guild).whitelisted_users.set(whitelisted_users)

            if changelog:
                changelog_message = "\n".join(changelog)
                embed = discord.Embed(title="Whitelist Changelog", description=changelog_message, color=discord.Color.blue())
                await ctx.send(embed=embed)
        except Exception as e:
            raise RuntimeError(f"Failed to update user whitelist: {e}")

    @whitelist.command(name="category")
    async def whitelist_category(self, ctx, category: discord.CategoryChannel):
        """Bypass/unbypass a channel category"""
        try:
            guild = ctx.guild
            whitelisted_categories = await self.config.guild(guild).whitelisted_categories()
            changelog = []

            if category.id in whitelisted_categories:
                whitelisted_categories.remove(category.id)
                changelog.append(f"Removed: {category.name}")
            else:
                whitelisted_categories.append(category.id)
                changelog.append(f"Added: {category.name}")

            await self.config.guild(guild).whitelisted_categories.set(whitelisted_categories)

            if changelog:
                changelog_message = "\n".join(changelog)
                embed = discord.Embed(title="Whitelist Changelog", description=changelog_message, color=discord.Color.blue())
                await ctx.send(embed=embed)
        except Exception as e:
            raise RuntimeError(f"Failed to update category whitelist: {e}")

    @whitelist.command(name="nsfw")
    async def whitelist_nsfw(self, ctx):
        """Toggle NSFW bypass status"""
        try:
            guild = ctx.guild
            current_status = await self.config.guild(guild).bypass_nsfw()
            new_status = not current_status
            await self.config.guild(guild).bypass_nsfw.set(new_status)
            status_text = "enabled" if new_status else "disabled"
            embed = discord.Embed(
                title="Whitelist updated",
                description=f"Bypassing NSFW channels is now **{status_text}**.\n\n- When **enabled**, channels marked as NSFW won't be moderated automatically.\n- When **disabled**, channels marked as NSFW will be moderated as usual.",
                color=0xfffffe
            )
            await ctx.send(embed=embed)
        except Exception as e:
            raise RuntimeError(f"Failed to toggle NSFW bypass: {e}")

    @omni.command(hidden=True)
    @commands.is_owner()
    async def debug(self, ctx):
        """Toggle debug mode to log all messages and their scores."""
        try:
            guild = ctx.guild
            current_debug_mode = await self.config.guild(guild).debug_mode()
            new_debug_mode = not current_debug_mode
            await self.config.guild(guild).debug_mode.set(new_debug_mode)
            status = "enabled" if new_debug_mode else "disabled"
            await ctx.send(f"Debug mode {status}.")
        except Exception as e:
            raise RuntimeError(f"Failed to toggle debug mode: {e}")

    def cog_unload(self):
        try:
            if self.session and not self.session.closed:
                self.bot.loop.create_task(self.session.close())
        except Exception as e:
            raise RuntimeError(f"Failed to unload cog: {e}")