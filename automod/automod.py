import discord # type: ignore
from redbot.core import commands, Config # type: ignore
import aiohttp # type: ignore
from collections import Counter
import unicodedata
import re
import asyncio
import tempfile
import matplotlib.pyplot as plt # type: ignore
import matplotlib.dates as mdates # type: ignore
from matplotlib.lines import Line2D
import math
import calendar
from datetime import datetime, timezone, timedelta
import asyncio
import io
import os

from . import views

class AutoMod(commands.Cog):
    """AI-powered automatic text moderation provided by frontier moderation models"""

    def __init__(self, bot):
        self.bot = bot
        self.session = None

        # Configuration setup
        self.config = Config.get_conf(self, identifier=11111111111)
        self._register_config()

        # In-memory reminder tracking to prevent duplicate reminders
        self._reminder_sent_at = {}  # {guild_id: {channel_id: datetime}}

        # Track timeouts issued by message id for "Untimeout" button
        self._timeout_issued_for_message = {}  # {message_id: True/False}

        # Store deleted messages for possible restoration
        self._deleted_messages = {}  # {message_id: {"content": ..., "author_id": ..., "author_name": ..., "author_avatar": ..., "channel_id": ..., "attachments": [...] }}

        # For logging: track which image was flagged if an image is moderated
        self._flagged_image_for_message = {}  # {message_id: image_url}

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
            replacements = {'nègre': 'negro', 'reggin': 'nigger', 'gooning': 'masturbating'}
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

        # Increment the message count for the channel in config
        guild_conf = self.config.guild(guild)
        user_message_counts = await guild_conf.user_message_counts()
        channel_id = channel.id
        user_message_counts[channel_id] = user_message_counts.get(channel_id, 0) + 1
        await guild_conf.user_message_counts.set(user_message_counts)

        # Check if the message count has reached 75
        if user_message_counts[channel_id] >= 75:
            # Prevent duplicate reminders by checking last sent time
            now = datetime.utcnow()
            if guild.id not in self._reminder_sent_at:
                self._reminder_sent_at[guild.id] = {}
            last_sent = self._reminder_sent_at[guild.id].get(channel.id)
            # Only send if not sent in the last 5 minutes (300 seconds)
            if not last_sent or (now - last_sent).total_seconds() > 300:
                await self.send_monitoring_reminder(channel)
                self._reminder_sent_at[guild.id][channel.id] = now
            # Reset the message count for the channel regardless
            user_message_counts[channel_id] = 0
            await guild_conf.user_message_counts.set(user_message_counts)

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
            embed.set_footer(text=f'Use "{command_prefix}automod vote" to give feedback on this server\'s moderation')
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
            guild_conf = self.config.guild(guild)
            if not await guild_conf.moderation_enabled():
                return

            if getattr(message.channel, "id", None) in await guild_conf.whitelisted_channels():
                return

            whitelisted_categories = await guild_conf.whitelisted_categories()
            if getattr(message.channel, "category_id", None) in whitelisted_categories:
                return

            whitelisted_roles = await guild_conf.whitelisted_roles()
            if hasattr(message.author, "roles") and any(getattr(role, "id", None) in whitelisted_roles for role in getattr(message.author, "roles", [])):
                return

            if getattr(message.author, "id", None) in await guild_conf.whitelisted_users():
                return

            if hasattr(message.channel, "is_nsfw") and callable(getattr(message.channel, "is_nsfw", None)):
                try:
                    is_nsfw = message.channel.is_nsfw()
                except Exception:
                    is_nsfw = False
                if is_nsfw and await guild_conf.bypass_nsfw():
                    return

            # Increment statistics directly in config
            await self.increment_statistic(guild.id, 'message_count')
            await self.increment_statistic('global', 'global_message_count')
            await self.increment_user_message_count(guild.id, message.author.id)

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
                        await self.increment_statistic(guild.id, 'image_count')
                        await self.increment_statistic('global', 'global_image_count')

            # Only send text for moderation in the main request
            text_category_scores = await self.analyze_content(input_data, api_key, message)
            moderation_threshold = await guild_conf.moderation_threshold()
            text_flagged = any(score > moderation_threshold for score in text_category_scores.values())

            # Analyze each image individually (API only supports one image at a time)
            for attachment in image_attachments:
                image_data = [{"type": "image_url", "image_url": {"url": attachment.url}}]
                image_category_scores = await self.analyze_content(image_data, api_key, message)
                image_flagged = any(score > moderation_threshold for score in image_category_scores.values())

                if image_flagged:
                    await self.update_moderation_stats(guild.id, message, image_category_scores)
                    # Track which image was flagged for this message
                    self._flagged_image_for_message[message.id] = attachment.url
                    # Always pass the flagged image url to handle_moderation for logging
                    await self.handle_moderation(message, image_category_scores, flagged_image_url=attachment.url)
                else:
                    # If not flagged, ensure we don't leave a stale value
                    if self._flagged_image_for_message.get(message.id) == attachment.url:
                        del self._flagged_image_for_message[message.id]

                # Space out requests
                await asyncio.sleep(1)

            if text_flagged:
                await self.update_moderation_stats(guild.id, message, text_category_scores)
                # For text moderation, clear any flagged image for this message
                if message.id in self._flagged_image_for_message:
                    del self._flagged_image_for_message[message.id]
                await self.handle_moderation(message, text_category_scores, flagged_image_url=None)

            if await guild_conf.debug_mode():
                # For debug logging, also use the flagged image if present
                flagged_image_url = self._flagged_image_for_message.get(message.id)
                await self.log_message(message, text_category_scores, flagged_image_url=flagged_image_url)
        except Exception as e:
            raise RuntimeError(f"Error processing message: {e}")

    async def increment_statistic(self, guild_id, stat_name, increment_value=1):
        if guild_id == 'global':
            current_value = await self.config.get_attr(stat_name)()
            await self.config.get_attr(stat_name).set(current_value + increment_value)
        else:
            guild_conf = self.config.guild_from_id(guild_id)
            current_value = await guild_conf.get_attr(stat_name)()
            await guild_conf.get_attr(stat_name).set(current_value + increment_value)

    async def increment_user_message_count(self, guild_id, user_id):
        if guild_id == 'global':
            # Not used for global
            return
        guild_conf = self.config.guild_from_id(guild_id)
        user_message_counts = await guild_conf.user_message_counts()
        user_message_counts[user_id] = user_message_counts.get(user_id, 0) + 1
        await guild_conf.user_message_counts.set(user_message_counts)

    async def update_moderation_stats(self, guild_id, message, text_category_scores):
        # Increment counts
        await self.increment_statistic(guild_id, 'moderated_count')
        await self.increment_statistic('global', 'global_moderated_count')

        # Update per-user moderation counts
        if guild_id == 'global':
            conf = self.config
            key = 'global_moderated_users'
        else:
            conf = self.config.guild_from_id(guild_id)
            key = 'moderated_users'
        users = await conf.get_attr(key)()
        users[str(message.author.id)] = users.get(str(message.author.id), 0) + 1
        await conf.get_attr(key).set(users)

        # Update category counters
        await self.update_category_counter(guild_id, text_category_scores)
        await self.update_category_counter('global', text_category_scores)

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
            guild_conf = self.config.guild_from_id(guild_id)
            user_violations = await guild_conf.user_violations()
            user_id_str = str(message.author.id)
            if user_id_str not in user_violations:
                user_violations[user_id_str] = []
            user_violations[user_id_str] = (user_violations[user_id_str] + [violation_entry])[-50:]
            await guild_conf.user_violations.set(user_violations)

        if any(getattr(attachment, "content_type", None) and attachment.content_type.startswith("image/") and not attachment.content_type.endswith("gif") for attachment in getattr(message, "attachments", [])):
            await self.increment_statistic(guild_id, 'moderated_image_count')
            await self.increment_statistic('global', 'global_moderated_image_count')

    async def update_category_counter(self, guild_id, text_category_scores):
        if guild_id == 'global':
            conf = self.config
            key = 'global_category_counter'
        else:
            conf = self.config.guild_from_id(guild_id)
            key = 'category_counter'
        counter = Counter(await conf.get_attr(key)())
        for category, score in text_category_scores.items():
            if score > 0.2:
                counter[category] += 1
        await conf.get_attr(key).set(dict(counter))

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

    async def translate_to_language(self, text, language):
        """
        Translate the given text to the specified language using OpenAI's GPT-3.5/4 API.
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
                f"Translate the following message to {language}. "
                f"If the message is already in {language}, return it unchanged. "
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

    async def explain_moderation(self, message_content, category_scores=None):
        """
        Send the message content to the OpenAI Moderations endpoint to get scores,
        then use GPT-4o to explain why the message matches those moderation scores.
        """
        try:
            api_key = (await self.bot.get_shared_api_tokens("openai")).get("api_key")
            if not api_key:
                return None
            if self.session is None or getattr(self.session, "closed", True):
                self.session = aiohttp.ClientSession()

            # Step 1: Get moderation scores from OpenAI Moderations endpoint
            moderation_scores = None
            async with self.session.post(
                "https://api.openai.com/v1/moderations",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                },
                json={"input": message_content},
                timeout=15
            ) as mod_resp:
                if mod_resp.status == 200:
                    mod_data = await mod_resp.json()
                    results = mod_data.get("results", [])
                    if results:
                        moderation_scores = results[0].get("category_scores", {})
                if not moderation_scores:
                    return None

            # Step 2: Prepare the prompt for GPT-4o using the moderation scores
            sorted_scores = sorted(moderation_scores.items(), key=lambda item: item[1], reverse=True)
            top_scores = sorted_scores[:6]
            score_lines = "\n".join(f"- {cat}: {score*100:.1f}%" for cat, score in top_scores)
            prompt = (
                "You are an expert in content moderation and AI safety. "
                "Given the following message and the AI moderation scores for various abuse categories, "
                "explain in clear, concise terms why the message may have matched these categories. "
                "If a score is high, explain what in the message could have triggered it. "
                "If all scores are low, explain that the message is likely safe. "
                "Do not add extra commentary or disclaimers. "
                "Format your answer as a short paragraph for staff review.\n\n"
                f"Message:\n{message_content}\n\n"
                f"AI moderation scores:\n{score_lines}"
            )
            payload = {
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "You are an expert content moderation analyst"},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 500,
                "temperature": 0.2,
            }
            async with self.session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                },
                json=payload,
                timeout=30
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    choices = data.get("choices", [])
                    if choices and "message" in choices[0]:
                        return choices[0]["message"]["content"].strip()
                return None
        except Exception:
            return None

    async def _upload_to_tmpfiles(self, file_path):
        """
        Upload a file to tmpfiles.org and return the URL.
        """
        try:
            if self.session is None or getattr(self.session, "closed", True):
                self.session = aiohttp.ClientSession()
            with open(file_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("file", f, filename=os.path.basename(file_path))
                async with self.session.post("https://tmpfiles.org/api/v1/upload", data=data) as resp:
                    if resp.status == 200:
                        resp_json = await resp.json()
                        tmpfiles_url = resp_json.get("data", {}).get("url")
                        return tmpfiles_url
        except Exception:
            return None
        return None

    async def handle_moderation(self, message, category_scores, flagged_image_url=None):
        try:
            guild = message.guild
            guild_conf = self.config.guild(guild)
            timeout_duration = await guild_conf.timeout_duration()
            log_channel_id = await guild_conf.log_channel()
            delete_violatory_messages = await guild_conf.delete_violatory_messages()

            message_deleted = False
            flagged_image_tempfile = None
            flagged_image_filename = None
            flagged_image_tmpfiles_url = None

            # If a flagged image is present, download it before deletion using tempfile
            if flagged_image_url:
                try:
                    if self.session is None or getattr(self.session, "closed", True):
                        self.session = aiohttp.ClientSession()
                    async with self.session.get(flagged_image_url) as resp:
                        if resp.status == 200:
                            # Try to get the filename from the URL
                            flagged_image_filename = flagged_image_url.split("/")[-1]
                            if not flagged_image_filename or "." not in flagged_image_filename:
                                flagged_image_filename = "flagged_image.png"
                            # Write to a NamedTemporaryFile
                            flagged_image_tempfile = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(flagged_image_filename)[-1])
                            flagged_image_tempfile.write(await resp.read())
                            flagged_image_tempfile.flush()
                            flagged_image_tempfile.close()
                except Exception:
                    flagged_image_tempfile = None
                    flagged_image_filename = None

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
                    # Increment per-user moderation count
                    users = await guild_conf.moderated_users()
                    users[str(message.author.id)] = users.get(str(message.author.id), 0) + 1
                    await guild_conf.moderated_users.set(users)
                    message_deleted = True
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    pass

            # If the message was deleted and we have a flagged image tempfile, upload it to tmpfiles
            if message_deleted and flagged_image_tempfile and flagged_image_filename:
                flagged_image_tmpfiles_url = await self._upload_to_tmpfiles(flagged_image_tempfile.name)
            else:
                flagged_image_tmpfiles_url = None

            timeout_issued = False
            if timeout_duration > 0:
                try:
                    reason = (
                        f"AI moderator issued a timeout. Violation: " +
                        ", ".join(f"{category}: {score * 100:.0f}%" for category, score in category_scores.items() if score > 0.2) +
                        f". Message: {message.content}"
                    )
                    await message.author.timeout(timedelta(minutes=timeout_duration), reason=reason)
                    await self.increment_statistic(guild.id, 'timeout_count')
                    await self.increment_statistic('global', 'global_timeout_count')
                    await self.increment_statistic(guild.id, 'total_timeout_duration', timeout_duration)
                    await self.increment_statistic('global', 'global_total_timeout_duration', timeout_duration)
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
                    # Use the tmpfiles url if the message was deleted and we have a flagged image tempfile
                    embed_image_url = None
                    if message_deleted and flagged_image_tmpfiles_url:
                        embed_image_url = flagged_image_tmpfiles_url
                    elif flagged_image_url:
                        embed_image_url = flagged_image_url
                    else:
                        embed_image_url = None

                    embed = await self._create_moderation_embed(
                        message, category_scores, "AI moderator detected potential misbehavior", action_taken, flagged_image_url=embed_image_url
                    )
                    # Use the ModerationActionView from views.py instead of the local class
                    timeout_issued_val = timeout_issued
                    timeout_duration_val = await guild_conf.timeout_duration()
                    view = views.ModerationActionView(self, message, timeout_issued_val, timeout_duration=timeout_duration_val)
                    # If a flagged image was present and we have the tempfile, send as a file
                    if flagged_image_tempfile and flagged_image_filename:
                        # Set the embed image to the attachment if not using tmpfiles url
                        if not (message_deleted and flagged_image_tmpfiles_url):
                            embed.set_image(url=f"attachment://{flagged_image_filename}")
                        try:
                            with open(flagged_image_tempfile.name, "rb") as f:
                                file = discord.File(f, filename=flagged_image_filename)
                                await log_channel.send(embed=embed, view=view, file=file)
                        finally:
                            try:
                                os.unlink(flagged_image_tempfile.name)
                            except Exception:
                                pass
                    else:
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

    async def _create_action_view(self, message, category_scores, timeout_issued=None):
        # Determine if a timeout was issued for this message
        if timeout_issued is None:
            timeout_issued = self._timeout_issued_for_message.get(message.id, False)
        timeout_duration = await self.config.guild(message.guild).timeout_duration()
        # Use the ModerationActionView from views.py
        return views.ModerationActionView(self, message, timeout_issued, timeout_duration=timeout_duration)

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

    async def log_message(self, message, category_scores, error_code=None, flagged_image_url=None):
        try:
            guild = message.guild
            log_channel_id = await self.config.guild(guild).log_channel()

            if log_channel_id:
                log_channel = guild.get_channel(log_channel_id)
                if log_channel:
                    # If an image was flagged for this message, use it in the embed
                    # Give priority to the flagged_image_url argument, then fallback to self._flagged_image_for_message
                    image_url = flagged_image_url if flagged_image_url else self._flagged_image_for_message.get(message.id)
                    # If the message was deleted and the image_url is a Discord CDN url, try to upload it to tmpfiles
                    if image_url and isinstance(image_url, str) and image_url.startswith("https://cdn.discordapp.com/"):
                        # Download and upload to tmpfiles
                        tmpfiles_url = None
                        try:
                            if self.session is None or getattr(self.session, "closed", True):
                                self.session = aiohttp.ClientSession()
                            async with self.session.get(image_url) as resp:
                                if resp.status == 200:
                                    # Try to get the filename from the URL
                                    filename = image_url.split("/")[-1]
                                    if not filename or "." not in filename:
                                        filename = "flagged_image.png"
                                    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[-1]) as tmpf:
                                        tmpf.write(await resp.read())
                                        tmpf.flush()
                                        tmpf.close()
                                        tmpfiles_url = await self._upload_to_tmpfiles(tmpf.name)
                                    try:
                                        os.unlink(tmpf.name)
                                    except Exception:
                                        pass
                        except Exception:
                            tmpfiles_url = None
                        if tmpfiles_url:
                            image_url = tmpfiles_url
                    embed = await self._create_moderation_embed(
                        message, category_scores, "Message processed by Omni", "No action taken", flagged_image_url=image_url
                    )
                    if error_code:
                        embed.add_field(name="Error", value=f":x: `{error_code}` Failed to send to moderation endpoint.", inline=False)
                    # Use the ModerationActionView from views.py
                    view = await self._create_action_view(message, category_scores)
                    await log_channel.send(embed=embed, view=view)
        except Exception as e:
            raise RuntimeError(f"Failed to log message: {e}")

    @commands.guild_only()
    @commands.group()
    async def automod(self, ctx):
        """
        An automatic, AI-powered moderator that handles text and image content
        
        **[Visit the docs to learn more](<https://sentri.beehive.systems/features/agentic-moderator>)**
        """
        pass

    @automod.command()
    async def stats(self, ctx):
        """
        Show statistics of the moderation activity.

        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-stats>)
        """
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

            embed = discord.Embed(title="Agentic moderator statistics", color=0xfffffe)
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

    @automod.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def history(self, ctx, user: discord.Member = None):
        """
        Show a user's violation history

        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-history>)
        """

        temp_file = None
        file = None
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

            # Sort violations most recent first by timestamp (descending)
            violations = sorted(
                violations,
                key=lambda v: v.get("timestamp", 0),
                reverse=True
            )

            # Get warning count for this user
            user_warnings = await guild_conf.user_warnings()
            warning_count = user_warnings.get(str(user.id), 0)

            if not violations and warning_count == 0:
                await ctx.send(f"No violations or warnings found for {user.mention}.")
                return

            # --- Generate abuse trend "GitHub-style" heatmap ---
            # We'll show a grid of days (last 8 weeks, 7 days per week), color intensity = #violations
            timestamps = [v.get("timestamp") for v in violations if v.get("timestamp")]
            image_url = None
            if timestamps:
                # Convert to datetime objects
                datetimes = [datetime.fromtimestamp(ts, tz=timezone.utc) for ts in timestamps]
                datetimes.sort()
                if len(datetimes) > 0:
                    # Build a grid for the last 8 weeks (56 days)
                    today = datetime.now(timezone.utc).date()
                    start_date = today - timedelta(days=55)
                    date_grid = [start_date + timedelta(days=i) for i in range(56)]
                    # Count violations per day
                    day_counts = Counter(dt.date() for dt in datetimes)
                    # Find max for color scaling
                    max_count = max(day_counts.values()) if day_counts else 1

                    # Prepare grid: 7 rows (days of week), 8 columns (weeks)
                    grid = [[0 for _ in range(8)] for _ in range(7)]
                    for idx, day in enumerate(date_grid):
                        week = idx // 7
                        weekday = day.weekday()  # Monday=0
                        grid[weekday][week] = day_counts.get(day, 0)

                    # --- Enhanced Color Palette and Design ---
                    # Use a more visually pleasing palette (cool-to-warm, with soft background)
                    import matplotlib.colors as mcolors
                    from matplotlib.patches import FancyBboxPatch

                    github_colors = [
                        "#f4f6fb",  # 0 - very light blue-gray
                        "#b3c6e0",  # 1-9 - soft blue
                        "#7fa8d1",  # 10-19 - medium blue
                        "#f7b267",  # 20-29 - soft orange
                        "#f4845f",  # 30+ - coral
                        "#c44536",  # 40+ - deep red
                    ]
                    def get_color(val):
                        if val == 0:
                            return github_colors[0]
                        elif 1 <= val < 10:
                            return github_colors[1]
                        elif 10 <= val < 20:
                            return github_colors[2]
                        elif 20 <= val < 30:
                            return github_colors[3]
                        elif 30 <= val < 40:
                            return github_colors[4]
                        else:  # 40 or more
                            return github_colors[5]

                    # Plot the grid with rounded corners and subtle grid lines
                    fig, ax = plt.subplots(figsize=(8.5, 2.7), dpi=120)
                    ax.set_facecolor("#f9fafb")
                    # Draw a soft drop shadow background
                    shadow = FancyBboxPatch(
                        (0.15, -0.2), 8-0.3, 7+0.4,
                        boxstyle="round,pad=0.18,rounding_size=0.25",
                        linewidth=0, facecolor="#e0e3ea", alpha=0.35, zorder=0
                    )
                    ax.add_patch(shadow)

                    # Draw the heatmap cells
                    for week in range(8):
                        for weekday in range(7):
                            count = grid[weekday][week]
                            color = get_color(count)
                            # Rounded rectangle for each cell
                            cell = FancyBboxPatch(
                                (week+0.08, 6 - weekday + 0.08), 0.84, 0.84,
                                boxstyle="round,pad=0.08,rounding_size=0.18",
                                linewidth=0.7, edgecolor="#d0d3db", facecolor=color, zorder=2
                            )
                            ax.add_patch(cell)
                            # Optionally, show count if high
                            if count > 0 and max_count > 2 and count == max_count:
                                ax.text(week + 0.5, 6 - weekday + 0.5, str(count), color="#222", ha="center", va="center", fontsize=8, fontweight="bold", zorder=3)

                    # Set axis
                    ax.set_xlim(-0.1, 8.1)
                    ax.set_ylim(-0.1, 7.1)
                    ax.set_xticks(range(8))
                    ax.set_yticks(range(7))

                    # Week labels (show start of each week)
                    week_labels = []
                    for week in range(8):
                        week_start = start_date + timedelta(days=week * 7)
                        week_labels.append(week_start.strftime("%b %d"))
                    ax.set_xticklabels(week_labels, rotation=35, ha="right", fontsize=9, color="#4a4a4a", fontweight="medium")
                    # Day labels (Mon, Tue, Wed, Thu, Fri, Sat, Sun)
                    day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                    ax.set_yticklabels(day_labels, fontsize=9, color="#4a4a4a", fontweight="medium")
                    ax.tick_params(left=False, bottom=False, labelleft=True, labelbottom=True)

                    # Title with user avatar
                    from matplotlib.offsetbox import OffsetImage, AnnotationBbox
                    import requests
                    from io import BytesIO

                    ax.set_title(
                        f"Abuse Trend for {user.display_name} (last 8 weeks)",
                        fontsize=15, pad=18, color="#2d2d2d", fontweight="bold", loc="left"
                    )
                    # Add user avatar (left of title)
                    try:
                        avatar_url = user.display_avatar.url
                        response = requests.get(avatar_url, timeout=3)
                        if response.status_code == 200:
                            avatar_img = plt.imread(BytesIO(response.content))
                            imagebox = OffsetImage(avatar_img, zoom=0.18, clip_path=None)
                            ab = AnnotationBbox(
                                imagebox, (-0.7, 7.2), frameon=False, box_alignment=(0.5, 0.5), zorder=10
                            )
                            ax.add_artist(ab)
                    except Exception:
                        pass

                    # Remove spines
                    for spine in ax.spines.values():
                        spine.set_visible(False)
                    plt.tight_layout(pad=1.5)

                    # Custom Legend (horizontal, below the plot)
                    from matplotlib.lines import Line2D
                    legend_elements = [
                        Line2D([0], [0], marker='s', color='w', label='0', markerfacecolor=github_colors[0], markersize=13, markeredgecolor="#d0d3db"),
                        Line2D([0], [0], marker='s', color='w', label='1-9', markerfacecolor=github_colors[1], markersize=13, markeredgecolor="#d0d3db"),
                        Line2D([0], [0], marker='s', color='w', label='10-19', markerfacecolor=github_colors[2], markersize=13, markeredgecolor="#d0d3db"),
                        Line2D([0], [0], marker='s', color='w', label='20-29', markerfacecolor=github_colors[3], markersize=13, markeredgecolor="#d0d3db"),
                        Line2D([0], [0], marker='s', color='w', label='30-39', markerfacecolor=github_colors[4], markersize=13, markeredgecolor="#d0d3db"),
                        Line2D([0], [0], marker='s', color='w', label='40+', markerfacecolor=github_colors[5], markersize=13, markeredgecolor="#d0d3db"),
                    ]
                    ax.legend(
                        handles=legend_elements,
                        title="Violations",
                        bbox_to_anchor=(0.5, -0.18),
                        loc="upper center",
                        fontsize=9,
                        title_fontsize=10,
                        frameon=False,
                        ncol=6,
                        handletextpad=0.7,
                        columnspacing=1.2,
                        borderaxespad=0.0,
                    )

                    # Subtle grid lines for week separation
                    for week in range(1, 8):
                        ax.axvline(week, color="#e6e8ef", linewidth=0.7, zorder=1, alpha=0.7)
                    for day in range(1, 7):
                        ax.axhline(day, color="#e6e8ef", linewidth=0.7, zorder=1, alpha=0.7)

                    # Save to tempfile
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        plt.savefig(tmp, format="png", bbox_inches="tight", dpi=140, transparent=True)
                        temp_file = tmp.name
                    plt.close(fig)
                    image_url = "attachment://abuse_trend.png"
                else:
                    temp_file = None
                    image_url = None
            else:
                temp_file = None
                image_url = None

            # Pagination setup
            VIOLATIONS_PER_PAGE = 5
            total_violations = len(violations)
            total_pages = max(1, math.ceil(total_violations / VIOLATIONS_PER_PAGE))

            def make_embed(page: int):
                start = page * VIOLATIONS_PER_PAGE
                end = start + VIOLATIONS_PER_PAGE
                violations_to_show = violations[start:end]
                embed = discord.Embed(
                    title=f"📜 Violation History for {user.display_name}",
                    color=0x7fa8d1,
                    description=f"**Showing {start+1}-{min(end, total_violations)} of {total_violations} violations for {user.mention}.**"
                )
                for v in violations_to_show:
                    ts = v.get("timestamp")
                    time_str = f"<t:{int(ts)}:R>" if ts else "Unknown time"
                    content = v.get("content", "*No content*")
                    categories = v.get("categories", {})
                    cat_str = ", ".join(f"`{cat}`: **{score*100:.0f}%**" for cat, score in categories.items())
                    channel_id = v.get("channel_id")
                    channel_mention = f"<#{channel_id}>" if channel_id else "Unknown"
                    embed.add_field(
                        name=f"🕒 {time_str} in {channel_mention}",
                        value=f"**Categories:** {cat_str}\n**Message:** {content[:300]}{'...' if len(content) > 300 else ''}",
                        inline=False
                    )
                embed.add_field(
                    name="⚠️ Warnings issued",
                    value=f"**{warning_count}** warning{'s' if warning_count != 1 else ''} for this user.",
                    inline=False
                )
                embed.set_footer(
                    text=f"Page {page+1}/{total_pages} • Only the last 50 violations are kept per user. Warnings are cumulative.",
                    icon_url=getattr(user.display_avatar, "url", discord.Embed.Empty)
                )
                if image_url:
                    embed.set_image(url=image_url)
                return embed

            # If only one page, just send the embed
            if total_pages == 1:
                embed = make_embed(0)
                if temp_file:
                    with open(temp_file, "rb") as f:
                        file = discord.File(f, filename="abuse_trend.png")
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

            # Emoji-based pagination (left, close/delete, right)
            LEFT_EMOJI = "⬅️"
            CLOSE_EMOJI = "🗑️"
            RIGHT_EMOJI = "➡️"
            PAGINATION_EMOJIS = [LEFT_EMOJI, CLOSE_EMOJI, RIGHT_EMOJI]

            page = 0
            embed = make_embed(page)
            if temp_file:
                with open(temp_file, "rb") as f:
                    file = discord.File(f, filename="abuse_trend.png")
                    message = await ctx.send(embed=embed, file=file)
            else:
                message = await ctx.send(embed=embed)

            for emoji in PAGINATION_EMOJIS:
                try:
                    await message.add_reaction(emoji)
                except Exception:
                    pass

            def check(reaction, user_):
                return (
                    reaction.message.id == message.id
                    and user_.id == ctx.author.id
                    and str(reaction.emoji) in PAGINATION_EMOJIS
                )

            try:
                while True:
                    try:
                        reaction, user_ = await ctx.bot.wait_for("reaction_add", timeout=120.0, check=check)
                    except asyncio.TimeoutError:
                        break

                    emoji = str(reaction.emoji)
                    old_page = page
                    if emoji == LEFT_EMOJI:
                        if page > 0:
                            page -= 1
                    elif emoji == RIGHT_EMOJI:
                        if page < total_pages - 1:
                            page += 1
                    elif emoji == CLOSE_EMOJI:
                        try:
                            await message.delete()
                        except Exception:
                            pass
                        if temp_file:
                            import os
                            try:
                                os.remove(temp_file)
                            except Exception:
                                pass
                        return

                    # Remove user's reaction to keep UI clean
                    try:
                        await message.remove_reaction(reaction.emoji, user_)
                    except Exception:
                        pass

                    if page != old_page:
                        embed = make_embed(page)
                        if temp_file:
                            with open(temp_file, "rb") as f:
                                file = discord.File(f, filename="abuse_trend.png")
                                await message.edit(embed=embed, attachments=[file])
                        else:
                            await message.edit(embed=embed)
            finally:
                # Clean up reactions
                try:
                    await message.clear_reactions()
                except Exception:
                    pass
                if temp_file:
                    import os
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass
        except Exception as e:
            raise RuntimeError(f"Failed to display violation history: {e}")

    @automod.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def settings(self, ctx):
        """
        Show AI AutoMod settings
        
        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-settings>)
        """
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
            monitoring_warning_status = "Active" if monitoring_warning_enabled else "Disabled with additional liability"

            embed = discord.Embed(title="Omni settings", description="Here is your server's current AI content moderation settings.\n\nCurious what Omni has done in your server?\n`omni stats`", color=0xfffffe)
            embed.add_field(name="Whitelisted channels", value=whitelisted_channels_names, inline=True)
            embed.add_field(name="Moderative threshold", value=f"{moderation_threshold * 100:.2f}%", inline=True)
            embed.add_field(name="Content scanning", value=":white_check_mark: **Enabled**" if moderation_enabled else ":x: Disabled", inline=True)
            embed.add_field(name="Whitelisted categories", value=whitelisted_categories_names, inline=True)
            embed.add_field(name="Timeout duration", value=f"{timeout_duration} minutes", inline=True)
            embed.add_field(name="Automatic deletion", value=":white_check_mark: **Enabled**" if delete_violatory_messages else ":warning: Disabled", inline=True)
            embed.add_field(name="Whitelisted roles", value=whitelisted_roles_names, inline=True)
            embed.add_field(name="Sending alerts to", value=log_channel_name, inline=True)
            embed.add_field(name="Debug mode", value=":cog: Enabled" if debug_mode else ":white_check_mark: **Disabled**", inline=True)
            embed.add_field(name="Whitelisted users", value=whitelisted_users_names, inline=True)
            embed.add_field(name="Privacy reminder", value=monitoring_warning_status, inline=True)
            embed.add_field(name="Auto whitelist NSFW", value=":warning: Enabled" if bypass_nsfw else ":white_check_mark: **Disabled**", inline=True)

            await ctx.send(embed=embed)
        except Exception as e:
            raise RuntimeError(f"Failed to display settings: {e}")

    @automod.command()
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

    @automod.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def threshold(self, ctx, threshold: float):
        """
        Set the moderation threshold for message sensitivity.

        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-threshold>)
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
    @automod.command()
    async def vote(self, ctx):
        """
        Give feedback on the server's agentic moderation
        
        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-vote>)
        """
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

    @automod.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def toggle(self, ctx):
        """
        Toggle automatic moderation on or off.
        
        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-toggle>)
        """
        try:
            guild = ctx.guild
            current_status = await self.config.guild(guild).moderation_enabled()
            new_status = not current_status
            await self.config.guild(guild).moderation_enabled.set(new_status)
            status = "enabled" if new_status else "disabled"
            await ctx.send(f"Automatic moderation {status}.")
        except Exception as e:
            raise RuntimeError(f"Failed to toggle automatic moderation: {e}")

    @automod.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def disclaimer(self, ctx):
        """
        Toggle privacy warning

        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-disclaimer>)
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

    @automod.command()
    async def reasons(self, ctx):
        """
        Explain content categories
        
        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-reasons>)
        """
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

    @automod.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def delete(self, ctx):
        """
        Toggle automatic deletion
        
        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-delete>)
        """
        try:
            guild = ctx.guild
            current_status = await self.config.guild(guild).delete_violatory_messages()
            new_status = not current_status
            await self.config.guild(guild).delete_violatory_messages.set(new_status)
            status = "enabled" if new_status else "disabled"
            await ctx.send(f"Deletion of violatory messages {status}.")
        except Exception as e:
            raise RuntimeError(f"Failed to toggle message deletion: {e}")

    @automod.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def timeout(self, ctx, duration: int):
        """
        Set timeout length
        
        Disable using `0`
        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-timeout>)
        """
        try:
            if duration >= 0:
                await self.config.guild(ctx.guild).timeout_duration.set(duration)
                await ctx.send(f"Timeout duration set to {duration} minutes.")
            else:
                await ctx.send("Timeout duration must be 0 or greater.")
        except Exception as e:
            raise RuntimeError(f"Failed to set timeout duration: {e}")

    @automod.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def logs(self, ctx, channel: discord.TextChannel):
        """
        Set automod logging channel.
        
        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-logs>)
        """
        try:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            await ctx.send(f"Log channel set to {channel.mention}.")
        except Exception as e:
            raise RuntimeError(f"Failed to set log channel: {e}")

    @automod.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def whitelist(self, ctx):
        """
        Control AI AutoMod whitelisting

        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-whitelist>)
        """
        pass

    @whitelist.command(name="channel")
    async def whitelist_channel(self, ctx, channel: discord.TextChannel):
        """
        Add/remove a channel
        
        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-whitelist-channel>)
        """
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
                embed = discord.Embed(title="Whitelist was modified", description=changelog_message, color=0xfffffe)
                await ctx.send(embed=embed)
        except Exception as e:
            raise RuntimeError(f"Failed to update channel whitelist: {e}")

    @whitelist.command(name="role")
    async def whitelist_role(self, ctx, role: discord.Role):
        """
        Add/remove a role
        
        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-whitelist-role>)
        """
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
                embed = discord.Embed(title="Whitelist was updated", description=changelog_message, color=0xfffffe)
                await ctx.send(embed=embed)
        except Exception as e:
            raise RuntimeError(f"Failed to update role whitelist: {e}")

    @whitelist.command(name="user")
    async def whitelist_user(self, ctx, user: discord.User):
        """
        Add/remove a user
        
        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-whitelist-user>)
        """
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
                embed = discord.Embed(title="Whitelist was updated", description=changelog_message, color=0xfffffe)
                await ctx.send(embed=embed)
        except Exception as e:
            raise RuntimeError(f"Failed to update user whitelist: {e}")

    @whitelist.command(name="category")
    async def whitelist_category(self, ctx, category: discord.CategoryChannel):
        """
        Add/remove a category
        
        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-whitelist-category>)
        """
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
                embed = discord.Embed(title="Whitelist was updated", description=changelog_message, color=0xfffffe)
                await ctx.send(embed=embed)
        except Exception as e:
            raise RuntimeError(f"Failed to update category whitelist: {e}")

    @whitelist.command(name="nsfw")
    async def whitelist_nsfw(self, ctx):
        """
        Enable/Disable NSFW bypass
        
        [View command documentation](<https://sentri.beehive.systems/features/agentic-moderator#automod-whitelist-nsfw>)
        """
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

    @automod.command(hidden=True)
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