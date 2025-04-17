import asyncio
import logging
import json
from typing import Union, Dict, Any
import discord
import aiohttp
import io
from aiohttp_retry import ExponentialRetry as Pulse
from redbot.core import commands
from shazamio.api import Shazam as AudioAlchemist
from shazamio.serializers import Serialize as Shazamalize
from colorthief import ColorThief
from datetime import datetime

class ShazamCog(commands.Cog):
    """Cog to interact with the Shazam API using shazamio."""

    def __init__(self, bot):
        self.bot = bot
        self.alchemist: AudioAlchemist = AudioAlchemist()

    async def __aio_get(self, url: str) -> bytes:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=120.0) as response:
                    response.raise_for_status()
                    return await response.read()
        except aiohttp.ClientError as error:
            logging.exception("Error fetching media from URL: %s", url, exc_info=True)
            raise commands.UserFeedbackCheckFailure("Failed to fetch media from the URL.") from error

    async def get_dominant_color(self, image_url: str) -> discord.Color:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as response:
                    response.raise_for_status()
                    image_data = await response.read()
                    color_thief = ColorThief(io.BytesIO(image_data))
                    dominant_color = color_thief.get_color(quality=1)
                    return discord.Color.from_rgb(*dominant_color)
        except Exception as e:
            logging.exception("Error fetching dominant color from image: %s", image_url, exc_info=True)
            raise RuntimeError("Failed to fetch dominant color from the image.") from e

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Automatically identify a song from an audio URL or uploaded file."""

        # Prevent bot from responding to itself or other bots
        if message.author.bot:
            return

        urls = []
        for attachment in message.attachments:
            # Defensive: check for None and for audio content type
            if getattr(attachment, "content_type", None) and str(attachment.content_type).startswith('audio/'):
                urls.append(attachment.url)

        if not urls:
            return

        async with message.channel.typing():
            for url in urls:
                try:
                    media = await self.__aio_get(url)
                    track_info = await self.alchemist.recognize(media)

                    if track_info and isinstance(track_info, dict) and 'track' in track_info:
                        track = track_info['track']
                        share_text = track.get('share', {}).get('text', 'Unknown Title')
                        coverart_url = track.get('images', {}).get('coverart', '')
                        try:
                            embed_color = await self.get_dominant_color(coverart_url) if coverart_url else discord.Color.blue()
                        except Exception:
                            embed_color = discord.Color.blue()

                        genre = track.get('genres', {}).get('primary', 'N/A')
                        release_date_str = track.get('releasedate', '')

                        # Check if release date is available, otherwise use metadata
                        if not release_date_str or release_date_str == 'Unknown Release Date':
                            sections = track_info.get('sections', [{}])
                            metadata = sections[0].get('metadata', []) if sections and isinstance(sections, list) and isinstance(sections[0], dict) else []
                            release_date_str = metadata[2].get('text', '') if len(metadata) > 2 and isinstance(metadata[2], dict) else ''

                        # Convert release date to discord dynamic timestamp
                        release_date_timestamp = ''
                        if release_date_str:
                            try:
                                if len(release_date_str) == 4 and release_date_str.isdigit():  # Year only
                                    release_date = datetime.strptime(release_date_str, '%Y')
                                else:
                                    # Defensive: try both d-m-Y and Y-m-d
                                    try:
                                        release_date = datetime.strptime(release_date_str, '%d-%m-%Y')
                                    except ValueError:
                                        release_date = datetime.strptime(release_date_str, '%Y-%m-%d')
                                release_date_timestamp = f"<t:{int(release_date.timestamp())}:D>"
                            except Exception as ve:
                                logging.exception("Error parsing release date: %s", release_date_str, exc_info=True)
                                release_date_timestamp = release_date_str  # fallback to raw string

                        description = f"{genre}"
                        if release_date_str:  # Ensure release date is shown when available
                            description += f", released {release_date_timestamp}"

                        embed = discord.Embed(
                            title=share_text,
                            description=description,
                            color=embed_color
                        )
                        if coverart_url:
                            embed.set_thumbnail(url=coverart_url)

                        # Check for explicit content
                        hub_info = track.get('hub', {})
                        if isinstance(hub_info, dict) and hub_info.get('explicit', False):
                            embed.set_footer(text="Song contains explicit content, audience discretion advised")

                        # Create URL buttons for Shazam and Apple Music
                        view = discord.ui.View()
                        shazam_url = track.get('url', '')
                        # Defensive: check for options and actions structure
                        apple_music_url = ''
                        hub = track.get('hub', {})
                        if isinstance(hub, dict):
                            options = hub.get('options', [])
                            if options and isinstance(options, list) and isinstance(options[0], dict):
                                actions = options[0].get('actions', [])
                                if actions and isinstance(actions, list) and isinstance(actions[0], dict):
                                    apple_music_url = actions[0].get('uri', '')

                        if shazam_url and isinstance(shazam_url, str) and shazam_url.startswith(('http://', 'https://')):
                            shazam_button = discord.ui.Button(label="Listen on Shazam", url=shazam_url)
                            view.add_item(shazam_button)

                        # Ensure the Apple Music URL is valid
                        if apple_music_url and isinstance(apple_music_url, str) and apple_music_url.startswith(('http://', 'https://')):
                            apple_music_button = discord.ui.Button(label="Open in Apple Music", url=apple_music_url)
                            view.add_item(apple_music_button)

                        # Send the embed without the JSON file
                        await message.reply(embed=embed, view=view)
                except Exception as e:
                    logging.exception("Error processing message: %s", getattr(message, "content", ""), exc_info=True)
                    # Do not raise, just fail gracefully
                    continue
