# Portions of this code copyright (C) 2020-2023 Hatching B.V
# All rights reserved.

import discord
from discord import ui
from redbot.core import commands, Config, app_commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, pagify, humanize_list

from io import BytesIO
from .pagination import Paginator
from .__version__ import __version__
from requests import Request, Session, exceptions, utils

import binascii
import urllib3
import json
import os
import platform
import asyncio

import datetime
import re
import pytz

urllib3.disable_warnings()

class TriageAnalysis(commands.Cog):
    """
    Triage Analysis - Interact with the Triage API from Discord.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xDEADBEEF, force_registration=True)
        default_guild = {
            "autoscan_enabled": True,
            "autoscan_score_threshold": 7,
            "autoscan_punishment": "ban",  # none, kick, ban, timeout
            "autoscan_timeout_seconds": 600,
            "autoscan_log_channel": None,  # Channel ID for logging autoscan events
        }
        self.config.register_guild(**default_guild)

    async def get_client(self, guild):
        # Use the triage api_key stored in Red's shared API tokens
        api_key = await self.bot.get_shared_api_tokens("triage")
        token = api_key.get("api_key")
        if not token:
            raise RuntimeError("Triage API key not set. Use `[p]set api triage api_key,<token>` to set it")
        return Client(token)

    @commands.group()
    async def triage(self, ctx):
        """
        Tria.ge is a dynamic analysis sandbox where suspicious files can be safely run in an isolated environment to be checked for malware

        **[Visit the docs to learn more](<https://sentri.beehive.systems/features/tria.ge>)**
        """
        pass

    # --- Admin only commands ---
    @triage.command(name="enable")
    @commands.admin_or_permissions(administrator=True)
    async def triage_autoscan_enable(self, ctx):
        """
        Enable automatic file analysis
        
        """
        await self.config.guild(ctx.guild).autoscan_enabled.set(True)
        embed = discord.Embed(
            title="Autoscan enabled",
            description="Automatic background file scanning is now **enabled**.",
            color=0x2bbd8e
        )
        await ctx.send(embed=embed)

    @triage.command(name="disable")
    @commands.admin_or_permissions(administrator=True)
    async def triage_autoscan_disable(self, ctx):
        """
        Disable automatic file analysis
        
        """
        await self.config.guild(ctx.guild).autoscan_enabled.set(False)
        embed = discord.Embed(
            title="Autoscan disabled",
            description="Automatic background file scanning is now **disabled**.",
            color=0xff4545
        )
        await ctx.send(embed=embed)

    @triage.command(name="threshold")
    @commands.mod_or_permissions(manage_guild=True)
    async def triage_autoscan_threshold(self, ctx, score: int):
        """
        Set a detection threshold for automatic actions
        
        """
        if score < 0 or score > 10:
            embed = discord.Embed(
                title="Invalid threshold",
                description="Score threshold must be between 0 and 10.",
                color=0xff4545
            )
            await ctx.send(embed=embed)
            return
        await self.config.guild(ctx.guild).autoscan_score_threshold.set(score)
        embed = discord.Embed(
            title="Threshold set",
            description=f"Score threshold for punishment set to **{score}**.",
            color=0x2bbd8e
        )
        await ctx.send(embed=embed)

    @triage.command(name="action")
    @commands.admin_or_permissions(administrator=True)
    async def triage_autoscan_punishment(self, ctx, punishment: str, timeout_seconds: int = 600):
        """
        Set a punishment for sharing malware

        """
        punishment = punishment.lower()
        if punishment not in ("none", "kick", "ban", "timeout"):
            embed = discord.Embed(
                title="Invalid punishment",
                description="Punishment must be one of: none, kick, ban, timeout.",
                color=0xff4545
            )
            await ctx.send(embed=embed)
            return
        await self.config.guild(ctx.guild).autoscan_punishment.set(punishment)
        if punishment == "timeout":
            if timeout_seconds < 1 or timeout_seconds > 28 * 24 * 3600:
                embed = discord.Embed(
                    title="Invalid timeout",
                    description="Timeout seconds must be between 1 and 2419200 (28 days).",
                    color=0xff4545
                )
                await ctx.send(embed=embed)
                return
            await self.config.guild(ctx.guild).autoscan_timeout_seconds.set(timeout_seconds)
            embed = discord.Embed(
                title="Punishment set",
                description=f"Punishment set to **timeout** for {timeout_seconds} seconds.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                title="Punishment set",
                description=f"Punishment set to **{punishment}**.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)

    @triage.command(name="logs")
    @commands.admin_or_permissions(administrator=True)
    async def triage_autoscan_logchannel(self, ctx, channel: discord.TextChannel = None):
        """
        Set the log channel
        """
        if channel is None:
            await self.config.guild(ctx.guild).autoscan_log_channel.set(None)
            embed = discord.Embed(
                title="Logging disabled",
                description="Autoscan log channel disabled.",
                color=0xff4545
            )
            await ctx.send(embed=embed)
        else:
            await self.config.guild(ctx.guild).autoscan_log_channel.set(channel.id)
            embed = discord.Embed(
                title="Log channel set",
                description=f"Autoscan log channel set to {channel.mention}.",
                color=0x2bbd8e
            )
            await ctx.send(embed=embed)

    @triage.command(name="settings")
    @commands.mod_or_permissions(manage_guild=True)
    async def triage_autoscan_status(self, ctx):
        """Show the current settings"""
        conf = await self.config.guild(ctx.guild).all()
        enabled = conf.get("autoscan_enabled", False)
        threshold = conf.get("autoscan_score_threshold", 5)
        punishment = conf.get("autoscan_punishment", "none")
        timeout_seconds = conf.get("autoscan_timeout_seconds", 600)
        log_channel_id = conf.get("autoscan_log_channel")
        log_channel = None
        if log_channel_id:
            log_channel = ctx.guild.get_channel(log_channel_id)
        embed = discord.Embed(
            title="Triage settings",
            color=0xfffffe
        )
        embed.add_field(name="Automatic file scanning", value="Enabled" if enabled else "Disabled", inline=True)
        embed.add_field(name="Score threshold", value=str(threshold), inline=True)
        embed.add_field(name="Punishment", value=punishment, inline=True)
        if punishment == "timeout":
            embed.add_field(name="Timeout seconds", value=str(timeout_seconds), inline=True)
        if log_channel:
            embed.add_field(name="Log channel", value=log_channel.mention, inline=False)
        elif log_channel_id:
            embed.add_field(name="Log channel", value=f"(ID: {log_channel_id}, not found)", inline=False)
        else:
            embed.add_field(name="Log channel", value="Not set", inline=False)
        await ctx.send(embed=embed)

    # --- All users commands ---
    @triage.command()
    async def url(self, ctx, url: str):
        """Donate file from URL for analysis."""
        try:
            client = await self.get_client(ctx.guild)
            data = client.submit_sample_url(url)
            embed = discord.Embed(
                title="URL submitted",
                description=f"Sample submitted!\n**ID:** `{data.get('id')}`\n**Status:** `{data.get('status')}`",
                color=0x2bbd8e
            )
            await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Error",
                description=f"{e}",
                color=0xff4545
            )
            await ctx.send(embed=embed)

    @triage.command()
    async def sample(self, ctx, sample_id: str):
        """Get info about a sample by ID."""
        try:
            client = await self.get_client(ctx.guild)
            data = client.sample_by_id(sample_id)
            embed = discord.Embed(
                title=f"Sample Info: {sample_id}",
                description="See below for JSON details.",
                color=discord.Color.blue()
            )
            for page in pagify(json.dumps(data, indent=2), page_length=1000):
                embed.add_field(name="Data", value=f"```json\n{page}\n```", inline=False)
            await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Error",
                description=f"{e}",
                color=0xff4545
            )
            await ctx.send(embed=embed)

    @triage.command()
    async def search(self, ctx, *, query: str):
        """Search for samples."""
        try:
            client = await self.get_client(ctx.guild)
            paginator = client.search(query)
            results = []
            for i, sample in enumerate(paginator):
                if i >= 10:
                    break
                results.append(f"`{sample.get('id', 'N/A')}`: {sample.get('status', 'N/A')}")
            embed = discord.Embed(
                title="Triage Search Results",
                description="\n".join(results) if results else "No results found.",
                color=discord.Color.blue() if results else discord.Color.orange()
            )
            await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Error",
                description=f"{e}",
                color=0xff4545
            )
            await ctx.send(embed=embed)

    @triage.command()
    async def static(self, ctx, sample_id: str):
        """Get the static report for a sample."""
        try:
            client = await self.get_client(ctx.guild)
            data = client.static_report(sample_id)
            embed = discord.Embed(
                title=f"Static Report: {sample_id}",
                color=discord.Color.blue()
            )
            for page in pagify(json.dumps(data, indent=2), page_length=1000):
                embed.add_field(name="Data", value=f"```json\n{page}\n```", inline=False)
            await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Error",
                description=f"{e}",
                color=0xff4545
            )
            await ctx.send(embed=embed)

    @triage.command()
    async def overview(self, ctx, sample_id: str):
        """Get the overview report for a sample."""
        try:
            client = await self.get_client(ctx.guild)
            data = client.overview_report(sample_id)
            embed = discord.Embed(
                title=f"Overview Report: {sample_id}",
                color=discord.Color.blue()
            )
            for page in pagify(json.dumps(data, indent=2), page_length=1000):
                embed.add_field(name="Data", value=f"```json\n{page}\n```", inline=False)
            await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Error",
                description=f"{e}",
                color=0xff4545
            )
            await ctx.send(embed=embed)

    @triage.command()
    async def download(self, ctx, sample_id: str):
        """Download the sample file."""
        try:
            client = await self.get_client(ctx.guild)
            file_bytes = client.get_sample_file(sample_id)
            embed = discord.Embed(
                title="Sample Download",
                description=f"Here is the file for sample `{sample_id}`.",
                color=0x2bbd8e
            )
            await ctx.send(embed=embed, file=discord.File(BytesIO(file_bytes), filename=f"{sample_id}.bin"))
        except Exception as e:
            embed = discord.Embed(
                title="Error",
                description=f"{e}",
                color=0xff4545
            )
            await ctx.send(embed=embed)

    @triage.command()
    async def events(self, ctx, sample_id: str):
        """Stream events of a running sample"""
        try:
            client = await self.get_client(ctx.guild)
            events = client.sample_events(sample_id)
            lines = []
            for i, event in enumerate(events):
                if i >= 10:
                    break
                lines.append(json.dumps(event))
            embed = discord.Embed(
                title=f"Sample Events: {sample_id}",
                color=discord.Color.blue()
            )
            if lines:
                for page in pagify("\n".join(lines), page_length=1000):
                    embed.add_field(name="Events", value=f"```json\n{page}\n```", inline=False)
            else:
                embed.description = "No events found."
            await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Error",
                description=f"{e}",
                color=0xff4545
            )
            await ctx.send(embed=embed)

    @triage.command()
    async def file(self, ctx):
        """Donate a file for analysis."""
        if not ctx.message.attachments:
            embed = discord.Embed(
                title="Include a file",
                description="You didn't upload a file for me to analyze. Try again!",
                color=0xff4545
            )
            await ctx.send(embed=embed)
            return
        attachment = ctx.message.attachments[0]
        try:
            client = await self.get_client(ctx.guild)
            file_bytes = await attachment.read()
            filename = attachment.filename
            # Use BytesIO for file-like object
            data = client.submit_sample_file(filename, BytesIO(file_bytes))
            embed = discord.Embed(
                title="File submitted",
                description=f"Sample submitted!\n**ID:** `{data.get('id')}`\n**Status:** `{data.get('status')}`",
                color=0x2bbd8e
            )
            await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Error",
                description=f"{e}",
                color=0xff4545
            )
            await ctx.send(embed=embed)

    @triage.command()
    async def analyze(self, ctx):
        """
        Analyze and return info about a file
        """
        if not ctx.message.attachments:
            embed = discord.Embed(
                title="No attachment",
                description="Please attach a file to analyze.",
                color=0xff4545
            )
            await ctx.send(embed=embed)
            return
        attachment = ctx.message.attachments[0]
        try:
            client = await self.get_client(ctx.guild)
            file_bytes = await attachment.read()
            filename = attachment.filename
            embed = discord.Embed(
                title="Uploading file",
                description="Your sample is uploading, please wait...",
                color=0xfffffe
            )
            await ctx.send(embed=embed)
            data = client.submit_sample_file(filename, BytesIO(file_bytes))
            sample_id = data.get("id")

            # Delete the file from chat after upload
            try:
                await attachment.delete()
            except Exception:
                pass  # Ignore if we can't delete (e.g., permissions)

            if not sample_id:
                embed = discord.Embed(
                    title="Submission failed",
                    description="Failed to submit file for analysis. Please try again later, the service may be experiencing an outage.",
                    color=0xff4545
                )
                await ctx.send(embed=embed)
                return
            embed = discord.Embed(
                title="Analysis starting",
                description=f"File was uploaded successfully, and analysis is starting.\n> Please wait, your results will be ready momentarily.",
                color=0xfffffe
            )
            await ctx.send(embed=embed)

            # Send typing while polling for analysis completion
            max_wait = 600  # seconds
            poll_interval = 10  # seconds
            waited = 0
            status = None
            async with ctx.typing():
                while waited < max_wait:
                    sample_info = client.sample_by_id(sample_id)
                    status = sample_info.get("status")
                    if status in ("reported", "failed", "finished", "complete"):
                        break
                    await asyncio.sleep(poll_interval)
                    waited += poll_interval

            if status not in ("reported", "finished", "complete"):
                embed = discord.Embed(
                    title="There was an issue during analysis",
                    description=f"Analysis did not complete in {max_wait} seconds. Status: `{status}`",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
                return

            # Try to get overview report
            try:
                overview = client.overview_report(sample_id)
            except Exception as e:
                embed = discord.Embed(
                    title="The overview wasn't available",
                    description=f"Analysis finished, but failed to fetch overview report: {e}",
                    color=0xff4545
                )
                await ctx.send(embed=embed)
                return

            # --- Compose the embed with rich info ---
            # Try to extract as much as possible from the overview structure
            sample_info = overview.get("sample", {})
            analysis_info = overview.get("analysis", {})
            targets = overview.get("targets", [])
            signatures = overview.get("signatures", [])
            tasks = overview.get("tasks", [])

            # Fallbacks for top-level info
            score = analysis_info.get("score") or overview.get("score")
            tags = analysis_info.get("tags") or overview.get("tags", [])
            verdict = overview.get("verdict", "N/A")
            family = overview.get("family", "N/A")
            target_name = sample_info.get("target") or (targets[0].get("target") if targets else None)
            sample_size = sample_info.get("size") or (targets[0].get("size") if targets else None)
            md5 = sample_info.get("md5") or (targets[0].get("md5") if targets else None)
            sha1 = sample_info.get("sha1") or (targets[0].get("sha1") if targets else None)
            sha256 = sample_info.get("sha256") or (targets[0].get("sha256") if targets else None)
            ssdeep = sample_info.get("ssdeep") or (targets[0].get("ssdeep") if targets else None)
            created = sample_info.get("created")
            completed = sample_info.get("completed")
            sample_id = sample_info.get("id") or sample_id

            # Convert created/completed to Discord dynamic timestamps if possible
            def to_discord_timestamp(dtstr):
                # dtstr is expected to be ISO8601, e.g. "2024-06-07T12:34:56.789Z"
                if not dtstr:
                    return None
                try:
                    # Remove Z and microseconds if present
                    dtstr_clean = re.sub(r"\.\d+", "", dtstr).replace("Z", "")
                    dt = datetime.datetime.fromisoformat(dtstr_clean)
                    # If no tzinfo, treat as UTC
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    unix_ts = int(dt.timestamp())
                    return f"<t:{unix_ts}:R>"
                except Exception:
                    return dtstr

            created_disp = to_discord_timestamp(created)
            completed_disp = to_discord_timestamp(completed)

            # Compose signatures summary as "SCORE | text (TTP's)"
            sigs = []
            for sig in signatures:
                score_ = sig.get("score")
                name = sig.get("name") or sig.get("label") or ""
                desc = sig.get("desc")
                # If desc is present, put it next to the name
                if name and desc:
                    text = f"{name}: {desc}"
                else:
                    text = name
                # Try both "ttp" and "ttps" for TTPs
                ttps = sig.get("ttp") or sig.get("ttps") or []
                if isinstance(ttps, str):
                    ttps = [ttps]
                if ttps:
                    ttps_joined = ', '.join(ttps)
                    ttps_str = f" ({ttps_joined})"
                else:
                    ttps_str = ""
                if score_ is not None and text:
                    sigs.append(f"**{score_}** | *{text}*{ttps_str}")
                elif text:
                    sigs.append(f"{text}{ttps_str}")

            # Add signature fields, splitting into multiple fields if needed
            # Each field value must be <= 1024 chars
            sig_field_values = []
            if sigs:
                current_value = ""
                for sig in sigs:
                    # +1 for newline if not first
                    add_len = len(sig) + (1 if current_value else 0)
                    if len(current_value) + add_len > 1024:
                        sig_field_values.append(current_value)
                        current_value = sig
                    else:
                        if current_value:
                            current_value += "\n"
                        current_value += sig
                if current_value or not sig_field_values:
                    sig_field_values.append(current_value if current_value else "None")
            else:
                sig_field_values = ["None"]

            # Compose IOC summary (URLs, domains, IPs)
            iocs = {}
            if targets and "iocs" in targets[0]:
                iocs = targets[0]["iocs"]
            urls = iocs.get("urls", []) if iocs else []
            domains = iocs.get("domains", []) if iocs else []
            ips = iocs.get("ips", []) if iocs else []

            # Compose tags
            tags_str = ", ".join(tags) if tags else "None"
            if len(tags_str) > 1024:
                tags_str = tags_str[:1021] + "..."

            # Compose tasks summary
            task_lines = []
            for t in tasks:
                t_name = t.get("name", "N/A")
                t_kind = t.get("kind", "N/A")
                t_score = t.get("score", "N/A")
                t_tags = ", ".join(t.get("tags", [])) if t.get("tags") else ""
                task_lines.append(f"{t_name} ({t_kind}) - Score: {t_score}" + (f" | Tags: {t_tags}" if t_tags else ""))
            tasks_str = "\n".join(task_lines) if task_lines else "None"
            if len(tasks_str) > 1024:
                cutoff = tasks_str.rfind('\n', 0, 1024)
                if cutoff == -1:
                    tasks_str = tasks_str[:1021] + "..."
                else:
                    tasks_str = tasks_str[:cutoff] + "\n... (truncated)"

            # Compose URLs, Domains, IPs fields, each max 1024 chars
            def join_and_truncate(items, sep, max_items=5, field_limit=1024):
                if not items:
                    return None
                joined = sep.join(items[:max_items])
                if len(items) > max_items:
                    joined += f"{sep}...and {len(items)-max_items} more"
                if len(joined) > field_limit:
                    # Truncate at a separator before limit if possible
                    cutoff = joined.rfind(sep, 0, field_limit)
                    if cutoff == -1:
                        joined = joined[:field_limit-3] + "..."
                    else:
                        joined = joined[:cutoff] + f"{sep}...(truncated)"
                return joined

            urls_str = join_and_truncate(urls, "\n", 5, 1024)
            domains_str = join_and_truncate(domains, ", ", 5, 1024)
            ips_str = join_and_truncate(ips, ", ", 5, 1024)

            # Compose embed
            embed = discord.Embed(
                title="Analysis complete",
                description=f"Static and dynamic analysis of your file has finished",
                color=0xff4545 if score and score >= 7 else 0x2bbd8e if score and score < 5 else discord.Color.orange()
            )
            if target_name:
                embed.add_field(name="File name", value=target_name, inline=True)
            if sample_size:
                size_mb = sample_size / (1024 * 1024)
                embed.add_field(name="File size", value=f"{size_mb:.2f} MB", inline=True)
            if score is not None:
                embed.add_field(name="Score", value=f"{str(score)}/10", inline=True)
            if verdict and verdict != "N/A":
                embed.add_field(name="Verdict", value=verdict, inline=True)
            if family and family != "N/A":
                embed.add_field(name="Family", value=family, inline=True)
            if tags_str:
                embed.add_field(name="Tags", value=tags_str, inline=False)
            if sha1:
                embed.add_field(name="SHA1", value=f"-# {sha1}", inline=False)
            if sha256:
                embed.add_field(name="SHA256", value=f"-# {sha256}", inline=False)
            if ssdeep:
                embed.add_field(name="SSDEEP", value=f"-# {ssdeep}", inline=False)
            if created_disp:
                embed.add_field(name="Created", value=created_disp, inline=True)
            if completed_disp:
                embed.add_field(name="Completed", value=completed_disp, inline=True)
            if tasks_str:
                embed.add_field(name="Tasks", value=tasks_str, inline=False)
            # Add all signature fields, splitting as needed
            for i, sig_field in enumerate(sig_field_values):
                field_name = "Signatures" if i == 0 else f"Signatures ({i})"
                embed.add_field(name=field_name, value=sig_field, inline=False)
            if urls_str:
                embed.add_field(name="URLs", value=urls_str, inline=False)
            if domains_str:
                embed.add_field(name="Domains", value=domains_str, inline=False)
            if ips_str:
                embed.add_field(name="IPs", value=ips_str, inline=False)

            embed.set_footer(text="Full overview report available on tria.ge.")

            # Add a URL button to view the report on tria.ge
            try:
                view = ui.View()
                view.add_item(
                    ui.Button(
                        label="View on tria.ge",
                        url=f"https://tria.ge/{sample_id}",
                        style=discord.ButtonStyle.link
                    )
                )
                await ctx.send(
                    embed=embed,
                    view=view
                )
            except Exception:
                # Fallback if discord.ui is not available or fails
                await ctx.send(
                    content=f"Analysis complete! See below for summary.\nView on tria.ge: https://tria.ge/{sample_id}",
                    embed=embed
                )
        except Exception as e:
            embed = discord.Embed(
                title="Error",
                description=f"{e}",
                color=0xff4545
            )
            await ctx.send(embed=embed)

    # --- Background file scan listener ---
    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        # Only scan in guilds, not DMs, and ignore bots
        if not message.guild or message.author.bot:
            return

        guild = message.guild
        conf = await self.config.guild(guild).all()
        if not conf.get("autoscan_enabled", False):
            return

        # Only scan if there are attachments
        if not message.attachments:
            return

        # Only scan files that are not images (to avoid scanning memes, etc)
        # You can adjust this logic as needed
        suspicious_exts = (
            ".exe", ".dll", ".scr", ".bat", ".cmd", ".js", ".vbs", ".jar", ".ps1", ".msi", ".com", ".cpl", ".sys",
            ".zip", ".rar", ".7z", ".iso", ".img", ".apk", ".bin", ".elf", ".py", ".sh", ".hta", ".lnk", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".pdf"
        )
        for attachment in message.attachments:
            filename = attachment.filename.lower()
            if not any(filename.endswith(ext) for ext in suspicious_exts):
                continue  # skip non-suspicious files

            # Start background scan
            asyncio.create_task(self._background_scan_file(message, attachment, conf))

    async def _background_scan_file(self, message: discord.Message, attachment: discord.Attachment, conf: dict):
        try:
            # Get log channel if set
            log_channel_id = conf.get("autoscan_log_channel")
            log_channel = None
            if log_channel_id:
                log_channel = message.guild.get_channel(log_channel_id)
            # Get client
            client = await self.get_client(message.guild)
            file_bytes = await attachment.read()
            filename = attachment.filename
            # Submit file for analysis
            notify_msg = None
            if log_channel:
                try:
                    embed = discord.Embed(
                        title="Analyzing file in the background",
                        description=f"Scanning file `{filename}` from {message.author.mention} for malware...",
                        color=0xfffffe
                    )
                    notify_msg = await log_channel.send(embed=embed)
                except Exception:
                    notify_msg = None
            data = client.submit_sample_file(filename, BytesIO(file_bytes))
            sample_id = data.get("id")
            if not sample_id:
                if notify_msg:
                    embed = discord.Embed(
                        title="Submission Failed",
                        description=f"❌ Failed to submit `{filename}` for analysis.",
                        color=0xff4545
                    )
                    await notify_msg.edit(content=None, embed=embed)
                return

            # Poll for completion
            max_wait = 600  # seconds
            poll_interval = 5  # seconds
            waited = 0
            status = None
            while waited < max_wait:
                sample_info = client.sample_by_id(sample_id)
                status = sample_info.get("status")
                if status in ("reported", "failed", "finished", "complete"):
                    break
                await asyncio.sleep(poll_interval)
                waited += poll_interval

            if status not in ("reported", "finished", "complete"):
                if notify_msg:
                    embed = discord.Embed(
                        title="Analysis Timeout",
                        description=f"⚠️ Analysis for `{filename}` did not complete in {max_wait} seconds. Status: `{status}`",
                        color=discord.Color.orange()
                    )
                    await notify_msg.edit(content=None, embed=embed)
                return

            # Get overview report
            try:
                overview = client.overview_report(sample_id)
            except Exception as e:
                if notify_msg:
                    embed = discord.Embed(
                        title="Overview Fetch Failed",
                        description=f"⚠️ Analysis finished, but failed to fetch overview report for `{filename}`: {e}",
                        color=0xff4545
                    )
                    await notify_msg.edit(content=None, embed=embed)
                return

            # Extract score
            analysis_info = overview.get("analysis", {})
            score = analysis_info.get("score") or overview.get("score", 0)
            verdict = overview.get("verdict", "N/A")
            tags = analysis_info.get("tags") or overview.get("tags", [])
            threshold = conf.get("autoscan_score_threshold", 5)
            punishment = conf.get("autoscan_punishment", "none")
            timeout_seconds = conf.get("autoscan_timeout_seconds", 600)

            # Compose result embed
            embed = discord.Embed(
                title="Autoscan Result",
                description=f"🦠 Scan complete for `{filename}` from {message.author.mention}.",
                color=discord.Color.orange() if score and score >= threshold else 0x2bbd8e
            )
            embed.add_field(name="Score", value=str(score), inline=True)
            embed.add_field(name="Verdict", value=verdict, inline=True)
            embed.add_field(name="Tags", value=', '.join(tags) if tags else "None", inline=False)
            embed.add_field(name="Sample ID", value=f"`{sample_id}`", inline=False)

            # If score is above threshold, take action
            punishment_msg = ""
            if score is not None and score >= threshold:
                embed.add_field(name="Threshold", value=f"⚠️ Score exceeds threshold ({threshold})!", inline=False)
                # Take punishment action
                try:
                    tags_str = ', '.join(tags) if tags else "None"
                    audit_reason = (
                        f"Triage autoscan: user {message.author} ({message.author.id}) shared file '{filename}' "
                        f"(sample ID: {sample_id}) which scored {score} (threshold: {threshold}); "
                        f"tags: {tags_str}"
                    )
                    if punishment == "kick":
                        await message.guild.kick(message.author, reason=audit_reason)
                        punishment_msg = f"🚫 User {message.author.mention} has been **kicked**."
                    elif punishment == "ban":
                        await message.guild.ban(message.author, reason=audit_reason, delete_message_days=0)
                        punishment_msg = f"🚫 User {message.author.mention} has been **banned**."
                    elif punishment == "timeout":
                        # Discord timeouts require discord.py 2.0+ and permissions
                        until = discord.utils.utcnow() + datetime.timedelta(seconds=timeout_seconds)
                        try:
                            await message.author.edit(timeout=until, reason=audit_reason)
                            punishment_msg = f"⏲️ User {message.author.mention} has been **timed out** for {timeout_seconds} seconds."
                        except Exception as e:
                            punishment_msg = f"⚠️ Failed to timeout user: {e}"
                    elif punishment == "none":
                        punishment_msg = "(No punishment configured.)"
                except Exception as e:
                    punishment_msg = f"⚠️ Failed to apply punishment: {e}"
            if punishment_msg:
                embed.add_field(name="Punishment", value=punishment_msg, inline=False)

            if notify_msg:
                await notify_msg.edit(content=None, embed=embed)
            elif log_channel:
                try:
                    await log_channel.send(embed=embed)
                except Exception:
                    pass
            # If no log channel, be absolutely silent

        except Exception as e:
            # Only log to log_channel if set, otherwise be silent
            log_channel_id = conf.get("autoscan_log_channel")
            log_channel = None
            if log_channel_id:
                log_channel = message.guild.get_channel(log_channel_id)
            if log_channel:
                try:
                    embed = discord.Embed(
                        title="Autoscan Error",
                        description=f"⚠️ Error during autoscan: {e}",
                        color=0xff4545
                    )
                    await log_channel.send(embed=embed)
                except Exception:
                    pass
            # If no log channel, be absolutely silent

# --- Below is the original Client and helpers, unchanged except for moving into the cog file ---

class Client:
    def __init__(self, token, root_url='https://api.tria.ge'):
        self.token = token
        self.root_url = root_url.rstrip('/')

    def _new_request(self, method, path, j=None, b=None, headers=None):
        if headers is None:
            headers = {}

        headers = {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": f"Python/{platform.python_version()} "
                          f"Triage Python Client/{__version__}",
            **headers
        }
        if j:
            return Request(method, self.root_url + path, data=json.dumps(j), headers=headers)
        return Request(method, self.root_url + path, data=b, headers=headers)

    def _req_file(self, method, path):
        r = self._new_request(method, path)
        with Session() as s:
            settings = s.merge_environment_settings(r.url, {}, None, False, None)
            return s.send(r.prepare(), **settings).content

    def _req_json(self, method, path, data=None):
        if data is None:
            r = self._new_request(method, path, data)
        else:
            r = self._new_request(method, path, data,
                headers={'Content-Type': 'application/json'})

        try:
            with Session() as s:
                settings = s.merge_environment_settings(r.url, {}, None, False, None)
                res = s.send(r.prepare(), **settings)
                res.raise_for_status()
                return res.json()
        except exceptions.HTTPError as err:
            raise ServerError(err)

    def submit_sample_file(self, filename, file, interactive=False, profiles=None, password=None, timeout=150, network="internet", escape_filename=True, tags=None):
        if profiles is None:
            profiles = []

        d = {
            'kind': 'file',
            'interactive': interactive,
            'profiles': profiles,
            'defaults': {
                'timeout': timeout,
                'network': network
            }
        }
        if tags:
            d['user_tags'] = tags

        if escape_filename:
            filename = filename.replace('"', '\\"')
        if password:
            d['password'] = password
        body, content_type = encode_multipart_formdata({
            '_json': json.dumps(d),
            'file': (filename, file),
        })
        r = self._new_request('POST', '/v0/samples', b=body,
            headers={"Content-Type": content_type}
        )
        try:
            with Session() as s:
                settings = s.merge_environment_settings(r.url, {}, None, False, None)
                res = s.send(r.prepare(), **settings)
                res.raise_for_status()
                return res.json()
        except exceptions.HTTPError as err:
            raise ServerError(err)

    def submit_sample_url(self, url, interactive=False, profiles=None):
        if profiles is None:
            profiles = []
        return self._req_json('POST', '/v0/samples', {
            'kind': 'url',
            'url': url,
            'interactive': interactive,
            'profiles': profiles,
        })

    def set_sample_profile(self, sample_id, profiles):
        return self._req_json('POST', '/v0/samples/%s/profile' % sample_id, {
            'auto': False,
            'profiles': profiles,
        })

    def set_sample_profile_automatically(self, sample_id, pick=None):
        if pick is None:
            pick = []
        return self._req_json('POST', '/v0/samples/%s/profile' % sample_id, {
            'auto': True,
            'pick': pick,
        })

    def org_samples(self, max=20):
        return Paginator(self, '/v0/samples?subset=org', max)

    def owned_samples(self, max=20):
        return Paginator(self, '/v0/samples?subset=owned', max)

    def public_samples(self, max=20):
        return Paginator(self, '/v0/samples?subset=public', max)

    def sample_by_id(self, sample_id):
        return self._req_json('GET', '/v0/samples/{0}'.format(sample_id))

    def get_sample_file(self, sample_id):
        return self._req_file("GET", "/v0/samples/{0}/sample".format(sample_id))

    def delete_sample(self, sample_id):
        return self._req_json('DELETE', '/v0/samples/{0}'.format(sample_id))

    def search(self, query, max=20):
        params = utils.quote(query)
        return Paginator(self, '/v0/search?query={0}'.format(params), max)

    def static_report(self, sample_id):
        return self._req_json(
            'GET', '/v0/samples/{0}/reports/static'.format(sample_id)
        )

    def overview_report(self, sample_id):
        return self._req_json(
            'GET', '/v1/samples/{0}/overview.json'.format(sample_id)
        )

    def kernel_report(self, sample_id, task_id):
        overview = self.overview_report(sample_id)
        for t in overview.get("tasks", []):
            if t.get("name") == task_id:
                task = t
                break
        else:
            raise ValueError("Task does not exist")

        log_file = None
        platform = task.get("platform") or task.get("os")
        if "windows" in platform:
            log_file = "onemon"
        elif "linux" in platform or "ubuntu" in platform:
            log_file = "stahp"
        elif "macos" in platform:
            log_file = "bigmac"
        elif "android" in platform:
            log_file = "droidy"
        else:
            raise ValueError("Platform not supported")

        r = self._new_request(
            'GET', '/v0/samples/{0}/{1}/logs/{2}.json'.format(
                sample_id, task_id, log_file)
        )

        with Session() as s:
            settings = s.merge_environment_settings(r.url, {}, None, False, None)
            res = s.send(r.prepare(), **settings)
            res.raise_for_status()
            for entry in res.content.split(b"\n"):
                if entry.strip() == b"":
                    break
                yield json.loads(entry)

    def task_report(self, sample_id, task_id):
        return self._req_json(
            'GET', '/v0/samples/{0}/{1}/report_triage.json'.format(
                sample_id, task_id)
        )

    def sample_task_file(self, sample_id, task_id, filename):
        return self._req_file(
            "GET", "/v0/samples/{0}/{1}/{2}".format(
                sample_id, task_id, filename)
        )

    def sample_archive_tar(self, sample_id):
        return self._req_file(
            "GET", "/v0/samples/{0}/archive".format(sample_id)
        )

    def sample_archive_zip(self, sample_id):
        return self._req_file(
            "GET", "/v0/samples/{0}/archive.zip".format(sample_id)
        )

    def create_profile(self, name, tags, network, timeout):
        return self._req_json("POST", "/v0/profiles", data={
            "name": name,
            "tags": tags,
            "network": network,
            "timeout": timeout
        })

    def delete_profile(self, profile_id):
        return self._req_json('DELETE', '/v0/profiles/{0}'.format(profile_id))

    def profiles(self, max=20):
        return Paginator(self, '/v0/profiles', max)

    def sample_events(self, sample_id):
        events = self._new_request("GET", "/v0/samples/"+sample_id+"/events")
        with Session() as s:
            settings = s.merge_environment_settings(events.url, {}, None, False, None)
            if 'stream' in settings:
                del settings['stream']
            res = s.send(events.prepare(), stream=True, **settings)
            for line in res.iter_lines():
                if line:
                    yield json.loads(line)

def PrivateClient(token):
    return Client(token, "https://private.tria.ge/api")

class ServerError(Exception):
    def __init__(self, err):
        try:
            b = err.response.json()
        except json.JSONDecodeError:
            b = {}

        self.status = err.response.status_code
        self.kind = b.get("error", "")
        self.message = b.get("message", "")

    def __str__(self):
        return 'triage: {0} {1}: {2}'.format(
            self.status, self.kind, self.message)


def encode_multipart_formdata(fields):
    boundary = binascii.hexlify(os.urandom(16)).decode('ascii')

    body = BytesIO()
    for field, value in fields.items(): # (name, file)
        if isinstance(value, tuple):
            filename, file = value
            body.write('--{boundary}\r\nContent-Disposition: form-data; '
                       'filename="{filename}"; name=\"{field}\"\r\n\r\n'
                .format(boundary=boundary, field=field, filename=filename)
                .encode('utf-8'))
            b = file.read()
            if isinstance(b, str):  # If the file was opened in text mode
                b = b.encode('ascii')
            body.write(b)
            body.write(b'\r\n')
        else:
            body.write('--{boundary}\r\nContent-Disposition: form-data;'
                       'name="{field}"\r\n\r\n{value}\r\n'
                .format(boundary=boundary, field=field, value=value)
                .encode('utf-8'))
    body.write('--{0}--\r\n'.format(boundary).encode('utf-8'))
    body.seek(0)

    return body, "multipart/form-data; boundary=" + boundary
