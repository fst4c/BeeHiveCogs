import logging
import os

import discord  # type: ignore
import yt_dlp  # type: ignore
from redbot.core import commands, Config  # type: ignore

# Add PIL for color extraction
from PIL import Image
import io
import subprocess

class TikTokLiveCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=11111111111111)
        self.config.register_guild(auto_download=False)

    @commands.guild_only()
    @commands.group()
    async def tiktoklive(self, ctx):
        """TikTok video commands."""
        pass

    @commands.guild_only()
    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def tiktokliveset(self, ctx):
        """TikTok video settings commands."""
        pass

    @tiktokliveset.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def settings(self, ctx):
        """Show the current TikTok video settings."""
        try:
            auto_download = await self.config.guild(ctx.guild).auto_download()
            embed = discord.Embed(
                title="Current TikTok settings",
                color=0xfffffe
            )
            embed.add_field(name="Auto download", value="Enabled" if auto_download else "Disabled", inline=False)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"Failed to retrieve settings: {e}")

    @tiktoklive.command()
    async def download(self, ctx, url: str):
        """Download a TikTok video and send it in the channel."""
        await self.download_video(ctx, url)

    async def _get_primary_color_from_video(self, video_path):
        """
        Extract the primary color from the first frame of the video.
        Returns an integer suitable for discord.Embed color.
        """
        try:
            # Use ffmpeg to extract the first frame as a PNG image in memory
            ffmpeg_cmd = [
                "ffmpeg",
                "-i", video_path,
                "-vf", "select=eq(n\\,0)",
                "-vframes", "1",
                "-f", "image2pipe",
                "-vcodec", "png",
                "pipe:1"
            ]
            proc = subprocess.run(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=True
            )
            image_bytes = proc.stdout
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            # Resize to reduce computation, then get most common color
            small_image = image.resize((64, 64))
            result = small_image.getcolors(64*64)
            if not result:
                return 0xfffffe  # fallback
            # Get the most common color
            most_common = max(result, key=lambda x: x[0])[1]
            # Convert to int
            color_int = (most_common[0] << 16) + (most_common[1] << 8) + most_common[2]
            return color_int
        except Exception as e:
            logging.error(f"Failed to extract primary color: {e}")
            return 0xfffffe  # fallback

    async def download_video(self, ctx, url: str, *, user_display_name: str = None):
        """Helper function to download a TikTok video and send it in the channel.

        If user_display_name is provided, it will be shown in the embed footer.
        """
        ydl_opts = {
            'format': 'best',
            'outtmpl': '/tmp/%(id)s.%(ext)s',  # Use a temporary directory and unique ID to avoid long file names
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=True)
                if 'formats' not in info_dict:
                    embed = discord.Embed(
                        title="Warning",
                        description="This video may contain potentially sensitive or graphic content, and TikTok has restricted access to it behind a login. Unfortunately, the bot cannot log in to access such content.",
                        color=discord.Color.orange()
                    )
                    await ctx.send(embed=embed)
                    return
                video_title = info_dict.get('title', 'video')
                video_uploader = info_dict.get('uploader', 'unknown')
                video_duration = info_dict.get('duration', 0)
                video_path = ydl.prepare_filename(info_dict)

                # Extract hashtags from the title
                hashtags = [word for word in video_title.split() if word.startswith('#')]
                # Remove hashtags from the title
                clean_title = ' '.join(word for word in video_title.split() if not word.startswith('#'))

                # Get the primary color from the first frame of the video
                color_int = await self._get_primary_color_from_video(video_path)

                embed = discord.Embed(
                    title="Here's that TikTok",
                    description=clean_title,
                    color=color_int
                )
                if hashtags:
                    embed.add_field(name="Hashtags", value=' '.join(hashtags), inline=False)

                # Set the footer if user_display_name is provided
                if user_display_name:
                    embed.set_footer(text=f"Sent by {user_display_name}")

                view = discord.ui.View()
                view.add_item(discord.ui.Button(label="Visit creator", url=f"https://www.tiktok.com/@{video_uploader}"))

                await ctx.send(embed=embed, file=discord.File(video_path), view=view)
                os.remove(video_path)
        except ValueError as ve:
            await ctx.send(f"Failed to download video: {ve}")
        except Exception as e:
            if "No video formats found" in str(e):
                embed = discord.Embed(
                    title="This TikTok is restricted",
                    description="This video may contain potentially sensitive or graphic content, and TikTok has restricted access to it behind a login. Unfortunately, the bot cannot log in to access such content.",
                    color=0xff4545
                )
                await ctx.send(embed=embed)
            else:
                await ctx.send(f"Failed to download video: {e}")

    @tiktokliveset.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def auto(self, ctx):
        """Toggle automatic downloading of TikTok videos."""
        try:
            current_setting = await self.config.guild(ctx.guild).auto_download()
            new_setting = not current_setting
            await self.config.guild(ctx.guild).auto_download.set(new_setting)
            status = "enabled" if new_setting else "disabled"
            await ctx.send(f"Automatic downloading of TikTok videos has been {status}.")
        except Exception as e:
            await ctx.send(f"Failed to toggle automatic downloading: {e}")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return

        try:
            guild_id = message.guild.id
            auto_download = await self.config.guild_from_id(guild_id).auto_download()
            if auto_download:
                # Check for complete TikTok URLs
                urls = [word for word in message.content.split() if word.startswith("https://www.tiktok.com/") or word.startswith("https://vt.tiktok.com/") or word.startswith("https://vm.tiktok.com/")]
                if urls:
                    # Only process the first TikTok URL found
                    # Pass the display name of the user who sent the message
                    await self.download_video(message.channel, urls[0], user_display_name=message.author.display_name)
                    await message.delete()
        except Exception as e:
            logging.error(f"Error in on_message: {e}")

    async def cog_load(self):
        pass

    async def cog_unload(self):
        pass

