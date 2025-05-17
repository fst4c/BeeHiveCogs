import aiohttp # type: ignore
import asyncio
import discord # type: ignore
import re
from redbot.core import commands, Config, checks # type: ignore

class VirusTotal(commands.Cog):
    """VirusTotal file upload and analysis via Discord"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.config.register_guild(
            auto_scan_enabled=True,
            submission_history={},
            log_channel=None,
            malware_action="none",  # none, kick, ban, timeout
            malware_action_threshold=11,  # Default threshold for action
            malware_action_timeout=600,   # Default timeout in seconds (if timeout is chosen)
        )
        self.submission_history = {}

    async def initialize(self):
        for guild in self.bot.guilds:
            guild_data = await self.config.guild(guild).all()
            await self.config.guild(guild).auto_scan_enabled.set(guild_data["auto_scan_enabled"])
            await self.config.guild(guild).submission_history.set(guild_data["submission_history"])
            await self.config.guild(guild).log_channel.set(guild_data.get("log_channel", None))
            await self.config.guild(guild).malware_action.set(guild_data.get("malware_action", "none"))
            await self.config.guild(guild).malware_action_threshold.set(guild_data.get("malware_action_threshold", 11))
            await self.config.guild(guild).malware_action_timeout.set(guild_data.get("malware_action_timeout", 600))

    @commands.group(name="virustotal", invoke_without_command=True)
    async def virustotal(self, ctx):
        """
        Use VirusTotal to automatically scan and monitor for malware in your server
        
        [Check the documentation to learn more](<https://sentri.beehive.systems/integrations/virustotal>)
        """
        await ctx.send_help(ctx.command)

    @checks.admin_or_permissions(manage_guild=True)
    @virustotal.command(name="autoscan")
    async def toggle_auto_scan(self, ctx):
        """Toggle automatic file scanning on or off"""
        guild = ctx.guild
        auto_scan_enabled = await self.config.guild(guild).auto_scan_enabled()
        new_status = not auto_scan_enabled
        await self.config.guild(guild).auto_scan_enabled.set(new_status)
        status = "enabled" if new_status else "disabled"
        await ctx.send(f"Automatic file scanning has been {status}.")

    @checks.admin_or_permissions(manage_guild=True)
    @virustotal.command(name="logs")
    async def set_log_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel where auto scan logs are sent. Use without argument to clear."""
        if channel is not None:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            await ctx.send(f"Auto scan log channel set to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel.set(None)
            await ctx.send("Auto scan log channel cleared.")

    @checks.admin_or_permissions(manage_guild=True)
    @virustotal.group(name="action", invoke_without_command=True)
    async def set_malware_action(self, ctx, action: str):
        """
        Set the action to take if a user sends a file commonly rated as malware.
        Valid actions: none, kick, ban, timeout
        """
        action = action.lower()
        if action not in ("none", "kick", "ban", "timeout"):
            await ctx.send("Invalid action. Valid actions: `none`, `kick`, `ban`, `timeout`.")
            return
        await self.config.guild(ctx.guild).malware_action.set(action)
        await ctx.send(f"Malware action set to `{action}`.")

    @checks.admin_or_permissions(manage_guild=True)
    @virustotal.command(name="threshold")
    async def set_malware_action_threshold(self, ctx, threshold: int):
        """
        Set the minimum number of 'malicious' detections required to trigger the action.
        """
        if threshold < 1:
            await ctx.send("Threshold must be at least 1.")
            return
        await self.config.guild(ctx.guild).malware_action_threshold.set(threshold)
        await ctx.send(f"Malware action threshold set to `{threshold}` malicious detections.")

    @checks.admin_or_permissions(manage_guild=True)
    @virustotal.command(name="duration")
    async def set_malware_action_timeout(self, ctx, seconds: int):
        """
        Set the timeout duration (in seconds) if the action is 'timeout'.
        """
        if seconds < 1:
            await ctx.send("Timeout must be at least 1 second.")
            return
        await self.config.guild(ctx.guild).malware_action_timeout.set(seconds)
        await ctx.send(f"Malware action timeout set to `{seconds}` seconds.")

    @checks.admin_or_permissions(manage_guild=True)
    @virustotal.command(name="settings")
    async def settings(self, ctx):
        """Show current settings for VirusTotal"""
        guild = ctx.guild
        auto_scan_enabled = await self.config.guild(guild).auto_scan_enabled()
        auto_scan_status = "Enabled" if auto_scan_enabled else "Disabled"
        
        version = "1.3.0"
        last_update = "May 17th, 2025"

        log_channel_id = await self.config.guild(guild).log_channel()
        log_channel_status = f"<#{log_channel_id}>" if log_channel_id else "Not set"

        malware_action = await self.config.guild(guild).malware_action()
        malware_action_threshold = await self.config.guild(guild).malware_action_threshold()
        malware_action_timeout = await self.config.guild(guild).malware_action_timeout()
        action_desc = f"Action: `{malware_action}`"
        if malware_action != "none":
            action_desc += f"\nThreshold: `{malware_action_threshold}`"
            if malware_action == "timeout":
                action_desc += f"\nTimeout: `{malware_action_timeout}` seconds"
        
        embed = discord.Embed(title="VirusTotal settings", colour=discord.Colour(0x394eff))
        embed.add_field(name="Overview", value="", inline=False)
        embed.add_field(name="Automatic scanning", value=auto_scan_status, inline=True)
        embed.add_field(name="Log channel", value=log_channel_status, inline=True)
        embed.add_field(name="Detection action", value=action_desc, inline=False)
        embed.add_field(name="About this cog", value="", inline=False)
        embed.add_field(name="Version", value=version, inline=True)
        embed.add_field(name="Last updated", value=last_update, inline=True)
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Automatically scan files if auto_scan is enabled"""
        guild = message.guild
        if guild is None:
            return  # Ignore messages not in a guild

        auto_scan_enabled = await self.config.guild(guild).auto_scan_enabled()
        if auto_scan_enabled and message.attachments:
            ctx = await self.bot.get_context(message)
            if ctx.valid:
                await self.silent_scan(ctx, message.attachments, message=message)

    def extract_hashes(self, text):
        """Extract potential file hashes from the text"""
        patterns = {
            'sha1': r'\b[0-9a-fA-F]{40}\b',
            'sha256': r'\b[0-9a-fA-F]{64}\b',
            'md5': r'\b[0-9a-fA-F]{32}\b',
            'imphash': r'\b[0-9a-fA-F]{32}\b',
            'ssdeep': r'\b[0-9a-zA-Z/+]{1,64}==\b'
        }
        hashes = []
        for pattern in patterns.values():
            hashes.extend(re.findall(pattern, text))
        return hashes

    async def silent_scan(self, ctx, attachments, message=None):
        """Scan files silently and alert/log if they're malicious or suspicious"""
        vt_key = await self.bot.get_shared_api_tokens("virustotal")
        if not vt_key.get("api_key"):
            return  # No API key set, silently return

        guild = ctx.guild
        log_channel_id = await self.config.guild(guild).log_channel()
        log_channel = None
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel is None:
                # Try to fetch if not cached
                try:
                    log_channel = await guild.fetch_channel(log_channel_id)
                except Exception:
                    log_channel = None

        malware_action = await self.config.guild(guild).malware_action()
        malware_action_threshold = await self.config.guild(guild).malware_action_threshold()
        malware_action_timeout = await self.config.guild(guild).malware_action_timeout()

        async with aiohttp.ClientSession() as session:
            for attachment in attachments:
                if attachment.size > 30 * 1024 * 1024:  # 30 MB limit
                    continue  # Skip files that are too large

                async with session.get(attachment.url) as response:
                    if response.status != 200:
                        continue  # Skip files that can't be downloaded

                    file_content = await response.read()
                    file_name = attachment.filename

                    async with session.post(
                        "https://www.virustotal.com/api/v3/files",
                        headers={"x-apikey": vt_key["api_key"]},
                        data={"file": file_content},
                    ) as vt_response:
                        if vt_response.status != 200:
                            continue  # Skip files that can't be uploaded

                        data = await vt_response.json()
                        analysis_id = data.get("data", {}).get("id")
                        if not analysis_id:
                            continue  # Skip files without a valid analysis ID

                        # Check the analysis results
                        while True:
                            await asyncio.sleep(15)  # Wait for the analysis to complete
                            async with session.get(
                                f"https://www.virustotal.com/api/v3/analyses/{analysis_id}",
                                headers={"x-apikey": vt_key["api_key"]},
                            ) as result_response:
                                if result_response.status != 200:
                                    continue  # Skip files that can't be checked

                                result_data = await result_response.json()
                                status = result_data.get("data", {}).get("attributes", {}).get("status")
                                if status == "completed":
                                    stats = result_data.get("data", {}).get("attributes", {}).get("stats", {})
                                    malicious = stats.get("malicious", 0)
                                    suspicious = stats.get("suspicious", 0)
                                    undetected = stats.get("undetected", 0)
                                    harmless = stats.get("harmless", 0)
                                    failure = stats.get("failure", 0)
                                    unsupported = stats.get("type-unsupported", 0)
                                    total = malicious + suspicious + undetected + harmless + failure + unsupported
                                    percent = round((malicious / total) * 100, 2) if total > 0 else 0

                                    # Try to get hashes for logging
                                    meta = result_data.get("meta", {}).get("file_info", {})
                                    sha256 = meta.get("sha256", "Unknown")
                                    sha1 = meta.get("sha1", "Unknown")
                                    md5 = meta.get("md5", "Unknown")

                                    # Compose embed for log
                                    embed = discord.Embed(
                                        title="VirusTotal Auto Scan Result",
                                        description=f"File: `{file_name}`",
                                        color=discord.Colour(0xff4545) if malicious > 0 else (discord.Colour(0xff9144) if suspicious > 0 else discord.Colour(0x2BBD8E))
                                    )
                                    embed.add_field(name="Malicious", value=str(malicious), inline=True)
                                    embed.add_field(name="Suspicious", value=str(suspicious), inline=True)
                                    embed.add_field(name="Harmless", value=str(harmless), inline=True)
                                    embed.add_field(name="Undetected", value=str(undetected), inline=True)
                                    embed.add_field(name="Failure", value=str(failure), inline=True)
                                    embed.add_field(name="Unsupported", value=str(unsupported), inline=True)
                                    embed.add_field(name="Detection %", value=f"{percent}%", inline=True)
                                    embed.add_field(name="SHA256", value=sha256, inline=False)
                                    embed.add_field(name="SHA1", value=sha1, inline=False)
                                    embed.add_field(name="MD5", value=md5, inline=False)
                                    embed.add_field(name="VirusTotal Link", value=f"[View results](https://www.virustotal.com/gui/file/{sha256})", inline=False)
                                    if message:
                                        embed.add_field(name="Submitted By", value=message.author.mention, inline=True)
                                        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
                                        embed.timestamp = message.created_at

                                    # --- Malware action logic ---
                                    action_taken = False
                                    if malicious >= malware_action_threshold and malware_action != "none" and message:
                                        member = message.author
                                        reason = f"VirusTotal: Sent file flagged as malware by {malicious} vendors."
                                        try:
                                            if malware_action == "kick":
                                                await member.kick(reason=reason)
                                                await ctx.send(f":warning: {member.mention} was **kicked** for sending a file flagged as malware by {malicious} vendors.")
                                                action_taken = True
                                            elif malware_action == "ban":
                                                await member.ban(reason=reason, delete_message_days=0)
                                                await ctx.send(f":warning: {member.mention} was **banned** for sending a file flagged as malware by {malicious} vendors.")
                                                action_taken = True
                                            elif malware_action == "timeout":
                                                # Discord timeouts require discord.py 2.0+ and permissions
                                                if hasattr(member, "timed_out_until"):
                                                    from datetime import timedelta, datetime, timezone
                                                    until = datetime.now(timezone.utc) + timedelta(seconds=malware_action_timeout)
                                                    await member.edit(timeout=until, reason=reason)
                                                    await ctx.send(f":warning: {member.mention} was **timed out** for {malware_action_timeout} seconds for sending a file flagged as malware by {malicious} vendors.")
                                                    action_taken = True
                                                else:
                                                    await ctx.send(":warning: Timeout action is not supported on this version of discord.py.")
                                        except discord.Forbidden:
                                            await ctx.send(f":warning: I do not have permission to {malware_action} {member.mention}.")
                                        except Exception as e:
                                            await ctx.send(f":warning: Failed to {malware_action} {member.mention}: {e}")

                                    if malicious > 0 or suspicious > 0:
                                        # Alert in context channel
                                        await ctx.send(f"Alert: The file `{file_name}` is flagged as malicious or suspicious.")
                                        # Log to log channel if set
                                        if log_channel:
                                            try:
                                                await log_channel.send(embed=embed)
                                            except Exception:
                                                pass
                                    else:
                                        # Log all scans to log channel if set
                                        if log_channel:
                                            try:
                                                await log_channel.send(embed=embed)
                                            except Exception:
                                                pass
                                    break
                                else:
                                    await asyncio.sleep(15)  # Wait a bit before checking again

    @virustotal.group(name="scan", invoke_without_command=True)
    async def scan(self, ctx):
        """Submit a file or URL to VirusTotal for analysis"""
        await ctx.send_help(ctx.command)

    @scan.command(name="url")
    async def scan_url(self, ctx, file_url: str):
        """Submit a URL to VirusTotal for analysis"""
        async with ctx.typing():
            vt_key = await self.bot.get_shared_api_tokens("virustotal")
            if not vt_key.get("api_key"):
                await self.send_error(ctx, "No VirusTotal API Key set", "Your Red instance doesn't have an API key set for VirusTotal.\n\nUntil you add an API key using `[p]set api`, the VirusTotal API will refuse your requests and this cog won't work.")
                return

            async with aiohttp.ClientSession() as session:
                try:
                    await self.submit_url_for_analysis(ctx, session, vt_key, file_url)
                except (aiohttp.ClientResponseError, ValueError) as e:
                    await self.send_error(ctx, "Failed to submit URL", str(e))
                except asyncio.TimeoutError:
                    await self.send_error(ctx, "Request timed out", "The bot was unable to complete the request due to a timeout.")

    @scan.command(name="file")
    async def scan_file(self, ctx):
        """Submit a file to VirusTotal for analysis"""
        async with ctx.typing():
            vt_key = await self.bot.get_shared_api_tokens("virustotal")
            if not vt_key.get("api_key"):
                await self.send_error(ctx, "No VirusTotal API Key set", "Your Red instance doesn't have an API key set for VirusTotal.\n\nUntil you add an API key using `[p]set api`, the VirusTotal API will refuse your requests and this cog won't work.")
                return

            async with aiohttp.ClientSession() as session:
                try:
                    attachments = ctx.message.attachments
                    if ctx.message.reference and not attachments:
                        ref_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                        attachments = ref_message.attachments

                    if attachments:
                        await self.submit_attachment_for_analysis(ctx, session, vt_key, attachments[0])
                    else:
                        await self.send_error(ctx, "No file provided", "The bot was unable to find content to submit for analysis!\nPlease provide one of the following when using this command:\n- Drag-and-drop a file less than 30mb in size\n- Reply to a message containing a file")
                except (aiohttp.ClientResponseError, ValueError) as e:
                    await self.send_error(ctx, "Failed to submit file", str(e))
                except asyncio.TimeoutError:
                    await self.send_error(ctx, "Request timed out", "The bot was unable to complete the request due to a timeout.")

    async def submit_url_for_analysis(self, ctx, session, vt_key, file_url):
        async with session.post("https://www.virustotal.com/api/v3/urls", headers={"x-apikey": vt_key["api_key"]}, data={"url": file_url}) as response:
            if response.status != 200:
                raise aiohttp.ClientResponseError(response.request_info, response.history, status=response.status, message=f"HTTP error {response.status}", headers=response.headers)
            data = await response.json()
            permalink = data.get("data", {}).get("id")
            if permalink:
                await ctx.send(f"Permalink: https://www.virustotal.com/gui/url/{permalink}")
                await self.check_results(ctx, permalink, ctx.author.id, file_url, None)
            else:
                raise ValueError("No permalink found in the response.")

    async def submit_attachment_for_analysis(self, ctx, session, vt_key, attachment):
        if attachment.size > 30 * 1024 * 1024:  # 30 MB limit
            await self.send_error(ctx, "File too large", "The file you provided exceeds the 30MB size limit for analysis.")
            return
        async with session.get(attachment.url) as response:
            if response.status != 200:
                raise aiohttp.ClientResponseError(response.request_info, response.history, status=response.status, message=f"HTTP error {response.status}", headers=response.headers)
            file_content = await response.read()
            file_name = attachment.filename  # Get the file name from the attachment
            await self.send_info(ctx, "Starting analysis", "This could take a few minutes, please be patient. You'll be mentioned when results are available.")
            async with session.post("https://www.virustotal.com/api/v3/files", headers={"x-apikey": vt_key["api_key"]}, data={"file": file_content}) as response:
                if response.status != 200:
                    raise aiohttp.ClientResponseError(response.request_info, response.history, status=response.status, message=f"HTTP error {response.status}", headers=response.headers)
                data = await response.json()
                analysis_id = data.get("data", {}).get("id")
                if analysis_id:
                    await self.check_results(ctx, analysis_id, ctx.author.id, attachment.url, file_name)
                    await ctx.message.delete()
                else:
                    raise ValueError("No analysis ID found in the response.")

    async def send_error(self, ctx, title, description):
        if ctx.channel.permissions_for(ctx.guild.me).embed_links:
            embed = discord.Embed(title=f'Error: {title}', description=description, colour=discord.Colour(0xff4545))
            embed.set_thumbnail(url="https://www.beehive.systems/hubfs/Icon%20Packs/Red/close.png")
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"Error: {title}. {description}")

    async def send_info(self, ctx, title, description):
        if ctx.channel.permissions_for(ctx.guild.me).embed_links:
            embed = discord.Embed(title=title, description=description, colour=discord.Colour(0x2BBD8E))
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"{title}. {description}")

    async def check_results(self, ctx, analysis_id, presid, file_url, file_name):
        vt_key = await self.bot.get_shared_api_tokens("virustotal")
        headers = {"x-apikey": vt_key["api_key"]}

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f'https://www.virustotal.com/api/v3/analyses/{analysis_id}', headers=headers) as response:
                    if response.status != 200:
                        raise aiohttp.ClientResponseError(response.request_info, response.history, status=response.status, message=f"HTTP error {response.status}", headers=response.headers)
                    data = await response.json()
                    attributes = data.get("data", {}).get("attributes", {})
                    while attributes.get("status") != "completed":
                        await asyncio.sleep(3)
                        async with session.get(f'https://www.virustotal.com/api/v3/analyses/{analysis_id}', headers=headers) as response:
                            if response.status != 200:
                                raise aiohttp.ClientResponseError(response.request_info, response.history, status=response.status, message=f"HTTP error {response.status}", headers=response.headers)
                            data = await response.json()
                            attributes = data.get("data", {}).get("attributes", {})
                    
                    stats = attributes.get("stats", {})
                    malicious_count = stats.get("malicious", 0)
                    suspicious_count = stats.get("suspicious", 0)
                    undetected_count = stats.get("undetected", 0)
                    harmless_count = stats.get("harmless", 0)
                    failure_count = stats.get("failure", 0)
                    unsupported_count = stats.get("type-unsupported", 0)
                    meta = data.get("meta", {}).get("file_info", {})
                    sha256 = meta.get("sha256")
                    sha1 = meta.get("sha1")
                    md5 = meta.get("md5")

                    total_count = malicious_count + suspicious_count + undetected_count + harmless_count + failure_count + unsupported_count
                    safe_count = harmless_count + undetected_count
                    percent = round((malicious_count / total_count) * 100, 2) if total_count > 0 else 0
                    if sha256 and sha1 and md5:
                        await self.send_analysis_results(ctx, presid, sha256, sha1, file_name, malicious_count, total_count, percent, safe_count)
                        self.log_submission(ctx.author.id, f"`{file_name}` - **{malicious_count}/{total_count}** - [View results](https://www.virustotal.com/gui/file/{sha256})")
                    else:
                        raise ValueError("Required hash values not found in the analysis response.")
            except (aiohttp.ClientResponseError, ValueError) as e:
                await self.send_error(ctx, "Analysis failed", str(e))
            except asyncio.TimeoutError:
                await self.send_error(ctx, "Request timed out", "The bot was unable to complete the request due to a timeout.")

    async def send_analysis_results(self, ctx, presid, sha256, sha1, file_name, malicious_count, total_count, percent, safe_count):
        content = f"||<@{presid}>||"
        if ctx.channel.permissions_for(ctx.guild.me).embed_links:
            embed = discord.Embed()
            if malicious_count >= 11:
                embed.title = "Analysis complete"
                embed.description = f"**{int(percent)}%** of vendors rated this file dangerous! You should avoid this file completely, and delete it from your systems to ensure security."
                embed.color = discord.Colour(0xff4545)
                embed.set_footer(text=f"SHA1 | {sha1}")
            elif 1 < malicious_count < 11:
                embed.title = "Analysis complete"
                embed.description = f"**{int(percent)}%** of vendors rated this file dangerous. While there are malicious ratings available for this file, there aren't many, so this could be a false positive. **You should investigate further before coming to a decision.**"
                embed.color = discord.Colour(0xff9144)
                embed.set_footer(text=f"SHA1 | {sha1}")
            else:
                embed.title = "Analysis complete"
                embed.color = discord.Colour(0x2BBD8E)
                embed.description = f"**{safe_count}** vendors say this file is malware-free"
                embed.set_footer(text=f"{sha1}")
            button = discord.ui.Button(label="View results on VirusTotal", url=f"https://www.virustotal.com/gui/file/{sha256}", style=discord.ButtonStyle.url)
            button2 = discord.ui.Button(label="Get a second opinion", url="https://discord.gg/6PbaH6AfvF", style=discord.ButtonStyle.url)
            view = discord.ui.View()
            view.add_item(button)
            view.add_item(button2)
            await ctx.send(content=content, embed=embed, view=view)
        else:
            if malicious_count >= 11:
                await ctx.send(f"{content}\nAnalysis complete: **{int(percent)}%** of vendors rated this file dangerous! You should avoid this file completely, and delete it from your systems to ensure security.\nSHA1: {sha1}\nView results on VirusTotal: https://www.virustotal.com/gui/file/{sha256}\nGet a second opinion: https://discord.gg/6PbaH6AfvF")
            elif 1 < malicious_count < 11:
                await ctx.send(f"{content}\nAnalysis complete: **{int(percent)}%** of vendors rated this file dangerous. While there are malicious ratings available for this file, there aren't many, so this could be a false positive. **You should investigate further before coming to a decision.**\nSHA1: {sha1}\nView results on VirusTotal: https://www.virustotal.com/gui/file/{sha256}\nGet a second opinion: https://discord.gg/6PbaH6AfvF")
            else:
                await ctx.send(f"{content}\nAnalysis complete: **{safe_count}** vendors say this file is malware-free\nSHA1: {sha1}\nView results on VirusTotal: https://www.virustotal.com/gui/file/{sha256}\nGet a second opinion: https://discord.gg/6PbaH6AfvF")

    def log_submission(self, user_id, summary):
        if user_id not in self.submission_history:
            self.submission_history[user_id] = []
        self.submission_history[user_id].append(summary)

    @virustotal.command(name="history", aliases=["sh"])
    async def submission_history(self, ctx):
        """View files recently submitted by you"""
        user_id = ctx.author.id
        if user_id in self.submission_history and self.submission_history[user_id]:
            history = "\n".join(self.submission_history[user_id])
            embed = discord.Embed(title="Your recent VirusTotal submissions", description=history, colour=discord.Colour(0x2BBD8E))
        else:
            embed = discord.Embed(title="No recent submissions", description="You have not submitted any files for analysis yet. Submissions reset when the bot restarts.", colour=discord.Colour(0xff4545))
        await ctx.send(embed=embed)




