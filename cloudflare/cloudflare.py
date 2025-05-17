import discord #type: ignore
import asyncio
import time
import tempfile
from datetime import datetime
from PIL import Image #type: ignore
from redbot.core import commands, Config #type: ignore
import aiohttp #type: ignore
import ipaddress
import json
import re
import io

class Cloudflare(commands.Cog):
    """A Red-Discordbot cog to interact with the Cloudflare API."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_global = {
            "api_key": None,
            "email": None,
            "bearer_token": None,
            "account_id": None,
        }
        default_guild = {
            "auto_scan": False,
            "log_channel": None,
        }
        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)
        self.session = aiohttp.ClientSession()

    @commands.group()
    async def urlscanner(self, ctx):
        """
        Use the Cloudflare API to scan websites for threats via Discord.

        Learn more at https://developers.cloudflare.com/radar/investigate/url-scanner/
        """

    @urlscanner.command(name="logs")
    @commands.has_permissions(administrator=True)
    async def set_log_channel(self, ctx, channel: discord.TextChannel = None):
        """
        Set or clear the logging channel for urlscanner alerts.
        Use without a channel to clear.
        """
        if channel is None:
            await self.config.guild(ctx.guild).log_channel.set(None)
            await ctx.send("Logging channel cleared.")
        else:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            await ctx.send(f"Logging channel set to {channel.mention}.")

    @commands.admin_or_permissions() 
    @urlscanner.command(name="search")
    async def search_url_scan(self, ctx, query: str):
        """Search for URL scans by date and webpage requests."""
        api_tokens = await self.bot.get_shared_api_tokens("cloudflare")
        account_id = api_tokens.get("account_id")
        bearer_token = api_tokens.get("bearer_token")

        if not account_id or not bearer_token:
            embed = discord.Embed(
                title="Configuration Error",
                description="Missing account ID or bearer token. Please check your configuration.",
                color=0xff4545
            )
            await ctx.send(embed=embed)
            return

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json"
        }

        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/urlscanner/scan"
        params = {"query": query}

        try:
            async with self.session.get(url, headers=headers, params=params) as response:
                data = await response.json()
                if not data.get("success", False):
                    error_message = data.get("errors", [{"message": "Unknown error"}])[0].get("message")
                    embed = discord.Embed(
                        title="Failed to Search URL Scans",
                        description=f"**Error:** {error_message}",
                        color=0xff4545
                    )
                    await ctx.send(embed=embed)
                    return

                results = data.get("result", {}).get("tasks", [])
                if not results:
                    embed = discord.Embed(
                        title="No Results",
                        description="No URL scans found for the given query.",
                        color=0xff4545
                    )
                    await ctx.send(embed=embed)
                    return

                pages = []
                current_page = discord.Embed(
                    title="URL Scan Results",
                    description=f"Search results for query: **`{query}`**",
                    color=0xFF6633
                )
                total_size = len(current_page.description)
                for result in results:
                    field_value = (
                        f"**Country:** {result.get('country', 'Unknown')}\n"
                        f"**Success:** {result.get('success', False)}\n"
                        f"**Time:** {result.get('time', 'Unknown')}\n"
                        f"**UUID:** {result.get('uuid', 'Unknown')}\n"
                        f"**Visibility:** {result.get('visibility', 'Unknown')}"
                    )
                    field_name = result.get("url", "Unknown URL")
                    if len(field_name) > 256:
                        field_name = field_name[:253] + "..."
                    field_size = len(field_name) + len(field_value)
                    if len(current_page.fields) == 25 or (total_size + field_size) > 6000:
                        pages.append(current_page)
                        current_page = discord.Embed(
                            title="URL Scan Results",
                            description=f"Search results for query: **`{query}`** (cont.)",
                            color=0x2BBD8E
                        )
                        total_size = len(current_page.description)
                    current_page.add_field(
                        name=field_name,
                        value=field_value,
                        inline=False
                    )
                    total_size += field_size
                pages.append(current_page)

                message = await ctx.send(embed=pages[0])
                if len(pages) > 1:
                    await message.add_reaction("◀️")
                    await message.add_reaction("❌")
                    await message.add_reaction("▶️")

                    def check(reaction, user):
                        return user == ctx.author and str(reaction.emoji) in ["◀️", "❌", "▶️"] and reaction.message.id == message.id

                    current_page_index = 0
                    while True:
                        try:
                            reaction, user = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)

                            if str(reaction.emoji) == "▶️" and current_page_index < len(pages) - 1:
                                current_page_index += 1
                                await message.edit(embed=pages[current_page_index])
                                await message.remove_reaction(reaction, user)

                            elif str(reaction.emoji) == "◀️" and current_page_index > 0:
                                current_page_index -= 1
                                await message.edit(embed=pages[current_page_index])
                                await message.remove_reaction(reaction, user)

                            elif str(reaction.emoji) == "❌":
                                await message.delete()
                                break

                        except asyncio.TimeoutError:
                            await message.clear_reactions()
                            break
        except Exception as e:
            await ctx.send(embed=discord.Embed(
                title="Error",
                description=f"An error occurred: {str(e)}",
                color=0xff4545
            ))

    @urlscanner.command(name="create")
    async def scan_url(self, ctx, url: str):
        """Start a new scan for the provided URL."""
        api_tokens = await self.bot.get_shared_api_tokens("cloudflare")
        account_id = api_tokens.get("account_id")
        bearer_token = api_tokens.get("bearer_token")

        if not account_id or not bearer_token:
            embed = discord.Embed(
                title="Configuration Error",
                description="Missing account ID or bearer token. Please check your configuration.",
                color=0xff4545
            )
            await ctx.send(embed=embed)
            return

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json"
        }

        payload = {
            "url": url
        }

        api_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/urlscanner/scan"

        try:
            async with self.session.post(api_url, headers=headers, json=payload) as response:
                data = await response.json()
                if not data.get("success", False):
                    error_message = data.get("errors", [{"message": "Unknown error"}])[0].get("message")
                    embed = discord.Embed(
                        title="Failed to Start URL Scan",
                        description=f"**Error:** {error_message}",
                        color=0xff4545
                    )
                    await ctx.send(embed=embed)
                    return

                result = data.get("result", {})
                embed = discord.Embed(
                    title="URL Scan Started",
                    description=f"Scan started successfully.",
                    color=0xFF6633
                )
                embed.add_field(name="UUID", value=f"**`{result.get('uuid', 'Unknown')}`**", inline=True)
                embed.add_field(name="Visibility", value=f"**`{result.get('visibility', 'Unknown')}`**", inline=True)
                embed.add_field(name="Target", value=f"**`{url}`**", inline=True)
                time_value = result.get('time', 'Unknown')
                if time_value != 'Unknown':
                    from datetime import datetime
                    dt = datetime.fromisoformat(time_value.replace('Z', '+00:00'))
                    time_value = f"<t:{int(dt.timestamp())}:F>"
                embed.add_field(name="Time", value=f"**`{time_value}`**", inline=True)
                await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(embed=discord.Embed(
                title="Error",
                description=f"An error occurred: {str(e)}",
                color=0xff4545
            ))

    @urlscanner.command(name="results")
    async def get_scan_result(self, ctx, scan_id: str):
        """Get the result of a URL scan by its ID."""
        api_tokens = await self.bot.get_shared_api_tokens("cloudflare")
        account_id = api_tokens.get("account_id")
        bearer_token = api_tokens.get("bearer_token")

        if not all([account_id, bearer_token]):
            embed = discord.Embed(
                title="Configuration Error",
                description="Missing one or more required API tokens. Please check your configuration.",
                color=0xff4545
            )
            await ctx.send(embed=embed)
            return

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json"
        }

        api_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/urlscanner/scan/{scan_id}"

        try:
            async with self.session.get(api_url, headers=headers) as response:
                data = await response.json()
                if not data.get("success", False):
                    error_message = data.get("errors", [{"message": "Unknown error"}])[0].get("message")
                    embed = discord.Embed(
                        title="Failed to Retrieve URL Scan Result",
                        description=f"**Error:** {error_message}",
                        color=0xff4545
                    )
                    await ctx.send(embed=embed)
                    return

                result = data.get("result", {}).get("scan", {})
                if not result:
                    await ctx.send(embed=discord.Embed(
                        title="No Data",
                        description="No relevant data found in the scan result.",
                        color=0xFF6633
                    ))
                    return

                task = result.get('task', {})
                verdicts = result.get('verdicts', {})
                meta = result.get('meta', {})
                processors = meta.get('processors', {})
                tech = processors.get('tech', [])
                task_url = task.get('url', 'Unknown')
                task_domain = task_url.split('/')[2] if task_url != 'Unknown' else 'Unknown'
                categories = []
                domains = result.get('domains', {})
                if task_domain in domains:
                    domain_data = domains[task_domain]
                    content_categories = domain_data.get('categories', {}).get('content', [])
                    inherited_categories = domain_data.get('categories', {}).get('inherited', {}).get('content', [])
                    categories.extend(content_categories + inherited_categories)

                embed = discord.Embed(
                    title="Scan results",
                    description=f"### Scan result for ID\n```{scan_id}```",
                    color=0x2BBD8E
                )
                embed.add_field(name="Target URL", value=f"```{task_url}```", inline=False)
                embed.add_field(name="Effective URL", value=f"```{task.get('effectiveUrl', 'Unknown')}```", inline=False)
                embed.add_field(name="Status", value=f"**`{task.get('status', 'Unknown')}`**", inline=True)
                embed.add_field(name="Visibility", value=f"**`{task.get('visibility', 'Unknown')}`**", inline=True)
                malicious_result = verdicts.get('overall', {}).get('malicious', 'Unknown')
                embed.add_field(name="Malicious", value=f"**`{malicious_result}`**", inline=True)
                embed.add_field(name="Tech", value=f"**`{', '.join([tech_item['name'] for tech_item in tech])}`**", inline=True)
                embed.add_field(name="Categories", value=f"**`{', '.join([category['name'] for category in categories])}`**", inline=True)
                await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(embed=discord.Embed(
                title="Error",
                description=f"An error occurred: {str(e)}",
                color=0xff4545
            ))

    @urlscanner.command(name="har")
    async def fetch_har(self, ctx, scan_id: str):
        """Fetch the HAR of a scan by the scan ID"""
        api_tokens = await self.bot.get_shared_api_tokens("cloudflare")
        email = api_tokens.get("email")
        api_key = api_tokens.get("api_key")
        bearer_token = api_tokens.get("bearer_token")
        account_id = api_tokens.get("account_id")

        if not all([email, api_key, bearer_token, account_id]):
            embed = discord.Embed(title="Configuration Error", description="Missing one or more required API tokens. Please check your configuration.", color=0xff4545)
            await ctx.send(embed=embed)
            return

        headers = {
            "X-Auth-Email": email,
            "X-Auth-Key": api_key,
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json"
        }

        api_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/urlscanner/scan/{scan_id}/har"

        try:
            async with self.session.get(api_url, headers=headers) as response:
                data = await response.json()
                if not data.get("success", False):
                    error_message = data.get("errors", [{"message": "Unknown error"}])[0].get("message")
                    embed = discord.Embed(
                        title="Failed to Retrieve HAR",
                        description=f"**Error:** {error_message}",
                        color=0xff4545
                    )
                    await ctx.send(embed=embed)
                    return

                har_data = data.get("result", {}).get("har", {})
                if not har_data:
                    await ctx.send(embed=discord.Embed(
                        title="No Data",
                        description="No HAR data found for the given scan ID.",
                        color=0xff4545
                    ))
                    return

                # Send HAR data as a file
                har_json = json.dumps(har_data, indent=4)
                har_file = discord.File(io.StringIO(har_json), filename=f"{scan_id}_har.json")
                await ctx.send(file=har_file)

        except Exception as e:
            await ctx.send(embed=discord.Embed(
                title="Error",
                description=f"An error occurred: {str(e)}",
                color=0xff4545
            ))

    @urlscanner.command(name="screenshot")
    async def get_scan_screenshot(self, ctx, scan_id: str):
        """Get the screenshot of a scan by its scan ID"""
        api_tokens = await self.bot.get_shared_api_tokens("cloudflare")
        email = api_tokens.get("email")
        api_key = api_tokens.get("api_key")
        bearer_token = api_tokens.get("bearer_token")
        account_id = api_tokens.get("account_id")

        if not all([email, api_key, bearer_token, account_id]):
            embed = discord.Embed(title="Configuration Error", description="Missing one or more required API tokens. Please check your configuration.", color=0xff4545)
            await ctx.send(embed=embed)
            return

        headers = {
            "X-Auth-Email": email,
            "X-Auth-Key": api_key,
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json"
        }

        screenshot_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/urlscanner/scan/{scan_id}/screenshot"

        try:
            async with self.session.get(screenshot_url, headers=headers) as screenshot_response:
                if screenshot_response.content_type == "image/png":
                    screenshot_data = await screenshot_response.read()
                    screenshot_file = discord.File(io.BytesIO(screenshot_data), filename=f"{scan_id}_screenshot.png")
                    embed = discord.Embed(
                        title="Screenshot fetched from scan",
                        description=f"### Screenshot for scan ID\n```{scan_id}```",
                        color=0x2BBD8E
                    )
                    screenshot_size = len(screenshot_data)
                    embed.add_field(name="File Size", value=f"**`{screenshot_size} bytes`**", inline=True)

                    # Assuming the resolution can be derived from the image data
                    image = Image.open(io.BytesIO(screenshot_data))
                    resolution = f"**`{image.width}`x`{image.height}`**"
                    embed.add_field(name="Resolution", value=resolution, inline=True)
                    embed.set_image(url=f"attachment://{scan_id}_screenshot.png")
                    await ctx.send(embed=embed, file=screenshot_file)
                else:
                    screenshot_data = await screenshot_response.json()
                    if not screenshot_data.get("success", False):
                        error_message = screenshot_data.get("errors", [{"message": "Unknown error"}])[0].get("message")
                        embed = discord.Embed(
                            title="Failed to retrieve screenshot",
                            description=f"**`{error_message}`**",
                            color=0xff4545
                        )
                        await ctx.send(embed=embed)
                        return
        except Exception as e:
            await ctx.send(embed=discord.Embed(
                title="Error",
                description=f"An error occurred: {str(e)}",
                color=0xff4545
            ))

    @urlscanner.command(name="scan")
    async def scan_url(self, ctx, url: str):
        """Scan a URL using Cloudflare URL Scanner and return the verdict."""
        api_tokens = await self.bot.get_shared_api_tokens("cloudflare")
        email = api_tokens.get("email")
        api_key = api_tokens.get("api_key")
        bearer_token = api_tokens.get("bearer_token")
        account_id = api_tokens.get("account_id")

        if not all([email, api_key, bearer_token, account_id]):
            embed = discord.Embed(title="Configuration Error", description="Missing one or more required API tokens. Please check your configuration.", color=0xff4545)
            await ctx.send(embed=embed)
            return

        headers = {
            "X-Auth-Email": email,
            "X-Auth-Key": api_key,
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json"
        }

        # Submit the URL for scanning
        submit_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/urlscanner/scan"
        payload = {"url": url}

        try:
            async with self.session.post(submit_url, headers=headers, json=payload) as response:
                if response.status == 409:
                    embed = discord.Embed(title="Domain on cooldown", description="The domain was too recently scanned. Please try again in a few minutes.", color=0xff4545)
                    await ctx.send(embed=embed)
                    return
                elif response.status != 200:
                    embed = discord.Embed(title="Error", description=f"Failed to submit URL for scanning: {response.status}", color=0xff4545)
                    await ctx.send(embed=embed)
                    return

                data = await response.json()
                if not data.get("success", False):
                    embed = discord.Embed(title="Error", description="Failed to submit URL for scanning.", color=0xff4545)
                    await ctx.send(embed=embed)
                    return

                scan_id = data["result"]["uuid"]
                embed = discord.Embed(title="Cloudflare is scanning your URL", description=f"This scan may take a few moments to complete, please wait patiently.", color=0xFF6633)
                embed.set_footer(text=f"{scan_id}")
                await ctx.send(embed=embed)
                await ctx.typing()

        except Exception as e:
            await ctx.send(embed=discord.Embed(
                title="Error",
                description=f"An error occurred while submitting the URL: {str(e)}",
                color=0xff4545
            ))
            return

        # Check the scan status every 10-15 seconds
        status_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/urlscanner/scan/{scan_id}"
        while True:
            await asyncio.sleep(15)
            try:
                async with self.session.get(status_url, headers=headers) as response:
                    if response.status == 202:
                        await ctx.typing()
                        continue
                    elif response.status != 200:
                        embed = discord.Embed(title="Error", description=f"Failed to check scan status: {response.status}", color=0xff4545)
                        await ctx.send(embed=embed)
                        return

                    data = await response.json()
                    if not data.get("success", False):
                        embed = discord.Embed(title="Error", description="Failed to check scan status.", color=0xff4545)
                        await ctx.send(embed=embed)
                        return

                    if response.status == 200:
                        scan_result = data["result"]["scan"]
                        verdict = scan_result["verdicts"]["overall"]
                        malicious = verdict["malicious"]
                        categories = ", ".join([cat["name"] for cat in verdict["categories"]])
                        phishing = ", ".join(verdict.get("phishing", []))

                        if malicious:
                            embed = discord.Embed(
                                title="Cloudflare detected a threat",
                                description=f"A URL scan has completed and Cloudflare has detected one or more threats",
                                color=0xff4545
                            )
                            embed.set_footer(text=f"{scan_id}")
                        else:
                            embed = discord.Embed(
                                title="Cloudflare detected no threats",
                                description=f"A URL scan has finished with no detections to report.",
                                color=0x2BBD8E
                            )
                            embed.set_footer(text=f"{scan_id}")

                        if categories:
                            embed.add_field(name="Categories", value=f"{categories}", inline=False)
                        if phishing:
                            embed.add_field(name="Phishing", value=f"{phishing}", inline=False)

                        # Add a URL button to view the report
                        view = discord.ui.View()
                        report_url = f"https://radar.cloudflare.com/scan/{scan_id}"
                        report_button = discord.ui.Button(label="View on Cloudflare Radar", url=report_url, style=discord.ButtonStyle.link)
                        view.add_item(report_button)
                        await ctx.send(embed=embed, view=view)
                        return

            except Exception as e:
                await ctx.send(embed=discord.Embed(
                    title="Error",
                    description=f"An error occurred while checking the scan status: {str(e)}",
                    color=0xff4545
                ))
                return

    @urlscanner.command(name="autoscan")
    @commands.has_permissions(administrator=True)
    async def set_autoscan(self, ctx: commands.Context, enabled: bool):
        """
        Enable or disable automatic URL scans.
        """
        await self.config.guild(ctx.guild).auto_scan.set(enabled)
        status = "enabled" if enabled else "disabled"
        embed = discord.Embed(
            title='Settings changed',
            description=f"Automatic URL scans utilizing Cloudflare threat intelligence have been **{status}**.",
            colour=0xffd966,
        )
        await ctx.send(embed=embed)
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Check if the message.guild is None
        if message.guild is None:
            return

        # Check if autoscan is enabled
        auto_scan_enabled = await self.config.guild(message.guild).auto_scan()
        if not auto_scan_enabled:
            return

        urls = [word for word in message.content.split() if word.startswith("http://") or word.startswith("https://")]
        if not urls:
            return

        api_tokens = await self.bot.get_shared_api_tokens("cloudflare")
        account_id = api_tokens.get("account_id")
        bearer_token = api_tokens.get("bearer_token")

        if not account_id or not bearer_token:
            return

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json"
        }

        # Get log channel for this guild, if set
        log_channel_id = await self.config.guild(message.guild).log_channel()
        log_channel = None
        if log_channel_id:
            log_channel = message.guild.get_channel(log_channel_id)
            if log_channel is None:
                # Try to fetch if not cached
                try:
                    log_channel = await message.guild.fetch_channel(log_channel_id)
                except Exception:
                    log_channel = None

        for url in urls:
            payload = {"url": url}
            api_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/urlscanner/scan"

            try:
                async with self.session.post(api_url, headers=headers, json=payload) as response:
                    data = await response.json()
                    if not data.get("success", False):
                        continue

                    scan_id = data.get("result", {}).get("uuid")
                    if not scan_id:
                        continue

                    await asyncio.sleep(120)

                    scan_result_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/urlscanner/scan/{scan_id}"
                    async with self.session.get(scan_result_url, headers=headers) as scan_response:
                        scan_data = await scan_response.json()
                        if not scan_data.get("success", False):
                            continue

                        result = scan_data.get("result", {}).get("scan", {})
                        verdicts = result.get("verdicts", {})
                        malicious = verdicts.get("overall", {}).get("malicious", False)

                        if malicious:
                            await message.delete()
                            embed = discord.Embed(
                                title="Cloudflare detected a threat!",
                                description=f"Cloudflare detected a threat in a message sent in this channel and removed it to safeguard the community.",
                                color=0xFF6633
                            )
                            await message.channel.send(embed=embed)
                            # Send alert to log channel if set and different from the message channel
                            if log_channel and log_channel.id != message.channel.id:
                                try:
                                    log_embed = discord.Embed(
                                        title="Cloudflare detected a threat",
                                        description=f"A message containing a malicious URL was detected and deleted in {message.channel.mention}.",
                                        color=0xF38020,
                                        timestamp=datetime.utcnow()
                                    )
                                    log_embed.add_field(
                                        name="User",
                                        value=f"{message.author.mention} (`{message.author.id}`)",
                                        inline=False
                                    )
                                    log_embed.add_field(
                                        name="URL",
                                        value=url,
                                        inline=False
                                    )
                                    log_embed.add_field(
                                        name="Scan ID",
                                        value=f"`{scan_id}`",
                                        inline=False
                                    )
                                    log_embed.add_field(
                                        name="Content",
                                        value=message.content[:1024],
                                        inline=False
                                    )
                                    log_embed.set_footer(text=f"User ID: {message.author.id}")
                                    await log_channel.send(embed=log_embed)
                                except Exception:
                                    pass
                            return

            except Exception as e:
                await message.channel.send(embed=discord.Embed(
                    title="Error",
                    description=f"An error occurred while processing the URL scan: {str(e)}",
                    color=0xff4545
                ))


    @commands.group(invoke_without_command=False)
    async def intel(self, ctx):
        """
        Utilize security & network intelligence powered by Cloudflare's global distributed network to assist in your investigations.
        
        Learn more at [cloudflare.com](<https://www.cloudflare.com/application-services/products/cloudforceone/>)
        """

    @intel.command(name="whois")
    async def whois(self, ctx, domain: str):
        """
        View available WHOIS info
        """

        api_tokens = await self.bot.get_shared_api_tokens("cloudflare")
        email = api_tokens.get("email")
        api_key = api_tokens.get("api_key")
        bearer_token = api_tokens.get("bearer_token")
        account_id = api_tokens.get("account_id")

        # Check if any required token is missing
        if not all([email, api_key, bearer_token, account_id]):
            embed = discord.Embed(
                title="Configuration Error",
                description="Missing one or more required API tokens. Please check your configuration.",
                color=discord.Color.from_str("#ff4545")
            )
            await ctx.send(embed=embed)
            return

        headers = {
            "X-Auth-Email": email,
            "X-Auth-Key": api_key,
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json"
        }

        async with self.session.get(f"https://api.cloudflare.com/client/v4/accounts/{account_id}/intel/whois?domain={domain}", headers=headers) as response:
            if response.status != 200:
                embed = discord.Embed(
                    title="Error",
                    description=f"Failed to fetch WHOIS information: {response.status}",
                    color=discord.Color.from_str("#ff4545")
                )
                await ctx.send(embed=embed)
                return

            data = await response.json()
            if not data.get("success", False):
                embed = discord.Embed(
                    title="Error",
                    description="Failed to fetch WHOIS information.",
                    color=discord.Color.from_str("#ff4545")
                )
                await ctx.send(embed=embed)
                return

            whois_info = data.get("result", {})

            # Check if the domain is found
            if whois_info.get("found", True) is False:
                embed = discord.Embed(
                    title="Domain not registered",
                    description="The domain doesn't seem to be registered. Please check the query and try again.",
                    color=0xff4545
                )
                await ctx.send(embed=embed)
                return

            pages = []
            page = discord.Embed(title=f"WHOIS query for {domain}", color=0xFF6633)
            page.set_footer(text="WHOIS information provided by Cloudflare", icon_url="https://cdn.brandfetch.io/idJ3Cg8ymG/w/400/h/400/theme/dark/icon.jpeg?c=1dxbfHSJFAPEGdCLU4o5B")
            field_count = 0

            def add_field_to_page(page, name, value):
                nonlocal field_count, pages
                page.add_field(name=name, value=value, inline=False)
                field_count += 1
                if field_count == 10:
                    pages.append(page)
                    page = discord.Embed(title=f"WHOIS query for {domain}", color=0xFF6633)
                    field_count = 0
                return page

            if "registrar" in whois_info:
                registrar_value = f"{whois_info['registrar']}"
                page.add_field(name="Registered with", value=registrar_value, inline=True)

            if "created_date" in whois_info:
                created_date = whois_info["created_date"]
                if isinstance(created_date, str):
                    from datetime import datetime
                    try:
                        created_date = datetime.strptime(created_date, "%Y-%m-%dT%H:%M:%S.%fZ")
                    except ValueError:
                        created_date = datetime.strptime(created_date, "%Y-%m-%dT%H:%M:%S")
                unix_timestamp = int(created_date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
                discord_timestamp = f"<t:{unix_timestamp}:d>"
                page.add_field(name="Created on", value=discord_timestamp, inline=True)

            if "updated_date" in whois_info:
                try:
                    updated_date = int(datetime.strptime(whois_info["updated_date"], "%Y-%m-%dT%H:%M:%S.%fZ").timestamp())
                    page.add_field(name="Updated on", value=f"<t:{updated_date}:d>", inline=True)
                except ValueError:
                    pass
                except AttributeError:
                    pass

            if "expiration_date" in whois_info:
                expiration_date = whois_info["expiration_date"]
                if isinstance(expiration_date, str):
                    try:
                        expiration_date = datetime.strptime(expiration_date, "%Y-%m-%dT%H:%M:%S.%fZ")
                    except ValueError:
                        expiration_date = datetime.strptime(expiration_date, "%Y-%m-%dT%H:%M:%S")
                unix_timestamp = int(expiration_date.timestamp())
                discord_timestamp = f"<t:{unix_timestamp}:d>"
                page.add_field(name="Expires on", value=discord_timestamp, inline=True)

            if "dnssec" in whois_info:
                dnssec_value = whois_info["dnssec"]
                if dnssec_value is True:
                    dnssec_value = ":white_check_mark: Enabled"
                elif dnssec_value is False:
                    dnssec_value = ":x: Disabled"
                else:
                    dnssec_value = f":grey_question: Unknown"
                page.add_field(name="DNSSEC", value=dnssec_value, inline=True)

            if "whois_server" in whois_info:
                whois_server = f"{whois_info['whois_server']}"
                page.add_field(name="Lookup via", value=whois_server, inline=True)

            if "nameservers" in whois_info:
                nameservers_list = "\n".join(f"- {ns}" for ns in whois_info["nameservers"])
                page = add_field_to_page(page, "Nameservers", nameservers_list)
                
            if "status" in whois_info:
                status_explainers = {
                    "clienttransferprohibited": ":lock: **Transfer prohibited**",
                    "clientdeleteprohibited": ":no_entry: **Deletion prohibited**",
                    "clientupdateprohibited": ":pencil2: **Update prohibited**",
                    "clientrenewprohibited": ":credit_card: **Renewal prohibited**",
                    "clienthold": ":pause_button: **Held by registrar**",
                    "servertransferprohibited": ":lock: **Server locked**",
                    "serverdeleteprohibited": ":no_entry: **Server deletion prohibited**",
                    "serverupdateprohibited": ":pencil2: **Server update prohibited**",
                    "serverhold": ":pause_button: **Server on hold**",
                    "pendingtransfer": ":hourglass: **Pending transfer**",
                    "pendingdelete": ":hourglass: **Pending deletion**",
                    "pendingupdate": ":hourglass: **Pending update**",
                    "ok": ":white_check_mark: **Active**"
                }
                status_list = "\n".join(
                    f"- `{status}` \n> {status_explainers.get(status.lower(), ':grey_question: *Unknown status*')}" 
                    for status in whois_info["status"]
                )
                page = add_field_to_page(page, "Status", status_list)

            contact_methods = []

            # Order: Name, Organization, ID, Email, Phone, Fax, Address
            if "registrar_name" in whois_info:
                contact_methods.append(f":office: {whois_info['registrar_name']}")
            if "registrar_org" in whois_info:
                contact_methods.append(f":busts_in_silhouette: {whois_info['registrar_org']}")
            if "registrar_id" in whois_info:
                contact_methods.append(f":id: {whois_info['registrar_id']}")
            if "registrar_email" in whois_info:
                contact_methods.append(f":incoming_envelope: {whois_info['registrar_email']}")
            if "registrar_phone" in whois_info:
                phone_number = whois_info['registrar_phone']
                contact_methods.append(f":telephone_receiver: {phone_number}")
            if "registrar_phone_ext" in whois_info:
                contact_methods.append(f":1234: {whois_info['registrar_phone_ext']}")
            if "registrar_fax" in whois_info:
                contact_methods.append(f":fax: {whois_info['registrar_fax']}")
            if "registrar_fax_ext" in whois_info:
                contact_methods.append(f":1234: {whois_info['registrar_fax_ext']}")
            if "registrar_street" in whois_info:
                contact_methods.append(f":house: {whois_info['registrar_street']}")
            if "registrar_province" in whois_info:
                contact_methods.append(f":map: {whois_info['registrar_province']}")
            if "registrar_postal_code" in whois_info:
                contact_methods.append(f":mailbox: {whois_info['registrar_postal_code']}")

            if contact_methods:
                contact_info = "\n".join(contact_methods)
                page = add_field_to_page(page, "To report abuse", contact_info)

            if page.fields:
                pages.append(page)

            # Create a view with buttons
            view = discord.ui.View()
            if "administrative_referral_url" in whois_info:
                button = discord.ui.Button(label="Admin", url=whois_info["administrative_referral_url"])
                view.add_item(button)
            if "billing_referral_url" in whois_info:
                button = discord.ui.Button(label="Billing", url=whois_info["billing_referral_url"])
                view.add_item(button)
            if "registrant_referral_url" in whois_info:
                button = discord.ui.Button(label="Registrant", url=whois_info["registrant_referral_url"])
                view.add_item(button)
            if "registrar_referral_url" in whois_info:
                button = discord.ui.Button(label="Visit registrar", url=whois_info["registrar_referral_url"])
                view.add_item(button)
            if "technical_referral_url" in whois_info:
                button = discord.ui.Button(label="Technical", url=whois_info["technical_referral_url"])
                view.add_item(button)            

            async def download_report(interaction: discord.Interaction):
                try:
                    html_content = f"""
                    <html>
                        <head>
                            <title>WHOIS Report for {domain}</title>
                            <meta name="viewport" content="width=device-width, initial-scale=1.0">
                            <link rel="preconnect" href="https://fonts.googleapis.com">
                            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
                            <link href="https://fonts.googleapis.com/css2?family=Inter+Tight:ital,wght@0,100..900;1,100..900&display=swap" rel="stylesheet">
                            <style>
                                body {{
                                    font-family: 'Inter Tight', sans-serif;
                                    margin: 20px;
                                    background-color: #f4f4f9;
                                    color: #333;
                                }}
                                h1, h2, h3 {{
                                    color: #000000;
                                    text-align: left;
                                }}
                                h1 {{
                                    font-size: 2em;
                                    margin-bottom: 10px;
                                }}
                                h2 {{
                                    font-size: 1.5em;
                                    margin-bottom: 5px;
                                }}
                                h3 {{
                                    font-size: 1.2em;
                                    margin-bottom: 5px;
                                }}
                                .header {{
                                    text-align: left;
                                    margin-bottom: 30px;
                                }}
                                .content {{
                                    max-width: 800px;
                                    margin: 0 auto;
                                    padding: 20px;
                                    background-color: #ffffff;
                                    border-radius: 8px;
                                    box-shadow: 0 0 15px rgba(0, 0, 0, 0.1);
                                }}
                                .section {{
                                    margin-bottom: 20px;
                                }}
                                .card-container {{
                                    display: flex;
                                    flex-wrap: wrap;
                                    justify-content: space-between;
                                    gap: 10px; /* Add gap to ensure space between cards */
                                }}
                                .card {{
                                    background-color: #f0f4f9;
                                    border-radius: 10px;
                                    padding: 15px;
                                    margin-bottom: 10px;
                                    box-shadow: 0 0 10px rgba(0, 0, 0, 0.05);
                                    flex: 1 1 calc(50% - 10px); /* Ensure cards take up half the container width minus the gap */
                                    box-sizing: border-box; /* Include padding and border in the element's total width and height */
                                }}
                                .key {{
                                    font-weight: bold;
                                    color: #000000;
                                    font-size: 1em;
                                }}
                                .value {{
                                    color: #000000;
                                    font-size: 1em;
                                }}
                                hr {{
                                    border: 0;
                                    height: 1px;
                                    background: #ddd;
                                    margin: 20px 0;
                                }}
                            </style>
                        </head>
                        <body>
                            <div class="content">
                                <div class="header">
                                    <h1>WHOIS Report for {domain}</h1>
                                    <p>Data provided by Cloudflare Intel and the respective registrar's WHOIS server</p>
                                </div>
                                <hr>
                                <div class="section">
                                    <h2>Report information</h2>
                                    <div class="card">
                                        <p><span class="key">Domain queried</span></p>
                                        <p><span class="value">{domain}</span></p>
                                    </div>
                                </div>
                                <div class="section">
                                    <h2>WHOIS</h2>
                                    <div class="card-container">
                    """
                    for key, value in whois_info.items():
                        html_content += f"""
                                        <div class='card'>
                                            <p><span class='key'>{key.replace('_', ' ').title()}</span></p>
                                            <p><span class='value'>{value}</span></p>
                                        </div>
                        """

                    html_content += """
                                    </div>
                                </div>
                            </div>
                        </body>
                    </html>
                    """

                    # Use a temporary file
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as temp_file:
                        temp_file.write(html_content.encode('utf-8'))
                        temp_file_path = temp_file.name

                    # Send the HTML file
                    await interaction.response.send_message(
                        content="Please open the attached file in a web browser to view the report.",
                        file=discord.File(temp_file_path),
                        ephemeral=True
                    )
                except Exception as e:
                    await interaction.response.send_message(
                        content="Failed to generate or send the HTML report.",
                        ephemeral=True
                    )

            download_button = discord.ui.Button(label="Download full report", style=discord.ButtonStyle.grey)
            download_button.callback = download_report
            view.add_item(download_button)

            message = await ctx.send(embed=pages[0], view=view)

            current_page = 0
            if len(pages) > 1:
                await message.add_reaction("◀️")
                await message.add_reaction("❌")
                await message.add_reaction("▶️")

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in ["◀️", "❌", "▶️"] and reaction.message.id == message.id

                while True:
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)

                        if str(reaction.emoji) == "▶️" and current_page < len(pages) - 1:
                            current_page += 1
                            await message.edit(embed=pages[current_page])
                            await message.remove_reaction(reaction, user)

                        elif str(reaction.emoji) == "◀️" and current_page > 0:
                            current_page -= 1
                            await message.edit(embed=pages[current_page])
                            await message.remove_reaction(reaction, user)

                        elif str(reaction.emoji) == "❌":
                            await message.delete()
                            break

                    except asyncio.TimeoutError:
                        await message.clear_reactions()
                        break

    @intel.command(name="domain")
    async def querydomain(self, ctx, domain: str):
        """View information about a domain"""
        
        # Check if the input is an IP address
        try:
            ip_obj = ipaddress.ip_address(domain)
            embed = discord.Embed(title="Error", description="The input appears to be an IP address. Please use the `ip` subcommand for IP address queries.", color=0xff4545)
            await ctx.send(embed=embed)
            return
        except ValueError:
            pass  # Not an IP address, continue with query

        # Fetch the blocklist from the web
        blocklist_url = "https://www.beehive.systems/hubfs/blocklist/blocklist.json"
        async with self.session.get(blocklist_url) as blocklist_response:
            if blocklist_response.status == 200:
                blocklist = await blocklist_response.json()
            else:
                embed = discord.Embed(title="Error", description="Failed to fetch the blocklist.", color=0xff4545)
                await ctx.send(embed=embed)
                return
        
        is_blocked = domain in blocklist

        api_tokens = await self.bot.get_shared_api_tokens("cloudflare")
        email = api_tokens.get("email")
        api_key = api_tokens.get("api_key")
        bearer_token = api_tokens.get("bearer_token")
        account_id = api_tokens.get("account_id")
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/intel/domain"
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "X-Auth-Email": email,
            "X-Auth-Key": api_key,
            "Content-Type": "application/json",
        }
        params = {
            "domain": domain
        }

        async with self.session.get(url, headers=headers, params=params) as response:
            data = await response.json()
            if response.status == 200 and data.get("success", False):
                result = data.get("result", {})
                embed = discord.Embed(title=f"Domain intelligence for {result.get('domain', 'N/A')}", color=0xFF6633)
                
                domain = result.get('domain')
                if domain:
                    embed.add_field(name="Domain", value=f"{domain}", inline=False)
                
                risk_score = result.get('risk_score')
                if risk_score is not None:
                    embed.add_field(name="Risk score", value=f"{risk_score}", inline=False)
                
                popularity_rank = result.get('popularity_rank')
                if popularity_rank is not None:
                    embed.add_field(name="Popularity rank", value=f"{popularity_rank}", inline=False)
                
                application = result.get("application", {})
                application_name = application.get('name')
                if application_name:
                    embed.add_field(name="Application", value=f"{application_name}", inline=False)
                
                additional_info = result.get("additional_information", {})
                suspected_malware_family = additional_info.get('suspected_malware_family')
                if suspected_malware_family:
                    embed.add_field(name="Suspected malware family", value=f"{suspected_malware_family}", inline=False)
                
                content_categories = result.get("content_categories", [])
                if content_categories:
                    categories_list = "\n".join([f"- {cat.get('name', 'N/A')}" for cat in content_categories])
                    embed.add_field(name="Content categories", value=categories_list, inline=False)
                
                resolves_to_refs = result.get("resolves_to_refs", [])
                if resolves_to_refs:
                    embed.add_field(name="Resolves to", value=", ".join([f"{ref.get('value', 'N/A')}" for ref in resolves_to_refs]), inline=False)
                
                inherited_content_categories = result.get("inherited_content_categories", [])
                if inherited_content_categories:
                    embed.add_field(name="Inherited content categories", value=", ".join([f"{cat.get('name', 'N/A')}" for cat in inherited_content_categories]), inline=False)
                
                inherited_from = result.get('inherited_from')
                if inherited_from:
                    embed.add_field(name="Inherited from", value=f"`{inherited_from}`", inline=False)
                
                inherited_risk_types = result.get("inherited_risk_types", [])
                if inherited_risk_types:
                    embed.add_field(name="Inherited risk types", value=", ".join([f"{risk.get('name', 'N/A')}" for risk in inherited_risk_types]), inline=False)
                
                risk_types = result.get("risk_types", [])
                if risk_types:
                    embed.add_field(name="Risk types", value=", ".join([f"{risk.get('name', 'N/A')}" for risk in risk_types]), inline=False)

                # Add blocklist status
                blocklist_status = ":white_check_mark: Yes" if is_blocked else ":x: No"
                embed.add_field(name="On BeeHive blocklist", value=f"{blocklist_status}", inline=False)

                # Create a view with a download button
                view = discord.ui.View()

                async def download_report(interaction: discord.Interaction):
                    try:
                        # Generate the report content
                        report_content = f"Domain Intelligence Report for {domain}\n\n"
                        report_content += f"Domain: {result.get('domain', 'N/A')}\n"
                        report_content += f"Risk Score: {result.get('risk_score', 'N/A')}\n"
                        report_content += f"Popularity Rank: {result.get('popularity_rank', 'N/A')}\n"
                        report_content += f"Application: {application.get('name', 'N/A')}\n"
                        report_content += f"Suspected Malware Family: {additional_info.get('suspected_malware_family', 'N/A')}\n"
                        report_content += f"Content Categories: {', '.join([cat.get('name', 'N/A') for cat in content_categories])}\n"
                        report_content += f"Resolves To: {', '.join([ref.get('value', 'N/A') for ref in resolves_to_refs])}\n"
                        report_content += f"Inherited Content Categories: {', '.join([cat.get('name', 'N/A') for cat in inherited_content_categories])}\n"
                        report_content += f"Inherited From: {result.get('inherited_from', 'N/A')}\n"
                        report_content += f"Inherited Risk Types: {', '.join([risk.get('name', 'N/A') for risk in inherited_risk_types])}\n"
                        report_content += f"Risk Types: {', '.join([risk.get('name', 'N/A') for risk in risk_types])}\n"
                        report_content += f"On BeeHive Blocklist: {'Yes' if is_blocked else 'No'}\n"

                        # Use a temporary file
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as temp_file:
                            temp_file.write(report_content.encode('utf-8'))
                            temp_file_path = temp_file.name

                        # Send the TXT file
                        await interaction.response.send_message(file=discord.File(temp_file_path))
                    except Exception as e:
                        await interaction.response.send_message(
                            content="Failed to generate or send the TXT report.",
                            ephemeral=True
                        )

                download_button = discord.ui.Button(label="Download full report", style=discord.ButtonStyle.grey)
                download_button.callback = download_report
                view.add_item(download_button)

                embed.set_footer(text="Data provided by BeeHive and Cloudflare")
                await ctx.send(embed=embed, view=view)
            else:
                error_message = data.get("errors", [{"message": "Unknown error"}])[0].get("message", "Unknown error")
                error_embed = discord.Embed(title="Error", description=f"Error: {error_message}", color=0xff4545)
                await ctx.send(embed=error_embed)

    @intel.command(name="ip")
    async def queryip(self, ctx, ip: str):
        """View information about an IP address"""

        api_tokens = await self.bot.get_shared_api_tokens("cloudflare")
        email = api_tokens.get("email")
        api_key = api_tokens.get("api_key")
        bearer_token = api_tokens.get("bearer_token")
        account_id = api_tokens.get("account_id")
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/intel/ip"
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "X-Auth-Email": email,
            "X-Auth-Key": api_key,
            "Content-Type": "application/json",
        }
        params = {}
        try:
            ip_obj = ipaddress.ip_address(ip)
            if ip_obj.is_private:
                embed = discord.Embed(title="Local IP Address", description="The IP address you entered is a local IP address and cannot be queried.", color=0xff4545)
                await ctx.send(embed=embed)
                return
            if ip_obj.version == 4:
                params["ipv4"] = ip
            elif ip_obj.version == 6:
                params["ipv6"] = ip
        except ValueError:
            embed = discord.Embed(title="Error", description="Invalid IP address format.", color=0xff4545)
            await ctx.send(embed=embed)
            return

        async with self.session.get(url, headers=headers, params=params) as response:
            data = await response.json()
            if response.status == 200 and data.get("success", False):
                result = data.get("result", [{}])[0]
                embed = discord.Embed(title=f"IP intelligence for {result.get('ip', 'N/A')}", color=0xFF6633)
                
                ip_value = result.get('ip')
                if ip_value:
                    embed.add_field(name="IP", value=f"{ip_value}", inline=True)
                
                belongs_to = result.get("belongs_to_ref", {})
                description = belongs_to.get('description')
                if description:
                    embed.add_field(name="Belongs to", value=f"{description}", inline=True)
                
                country = belongs_to.get('country')
                if country:
                    embed.add_field(name="Country", value=f"{country}", inline=True)
                
                type_value = belongs_to.get('type')
                if type_value:
                    embed.add_field(name="Type", value=f"{type_value.upper()}", inline=True)
                
                risk_types = result.get("risk_types", [])
                if risk_types:
                    risk_types_str = ", ".join([f"{risk.get('name', 'N/A')}" for risk in risk_types if risk.get('name')])
                    if risk_types_str:
                        embed.add_field(name="Risk types", value=risk_types_str, inline=True)
                
                if "ptr_lookup" in result and result["ptr_lookup"] and "ptr_domains" in result["ptr_lookup"] and result["ptr_lookup"]["ptr_domains"]:
                    ptr_domains = "\n".join([f"- {domain}" for domain in result["ptr_lookup"]["ptr_domains"]])
                    embed.add_field(name="PTR domains", value=ptr_domains, inline=True)
                
                result_info = data.get("result_info", {})
                total_count = result_info.get('total_count')
                if total_count:
                    embed.add_field(name="Total count", value=f"{total_count}", inline=False)
                
                page = result_info.get('page')
                if page:
                    embed.add_field(name="Page", value=f"{page}", inline=False)
                
                per_page = result_info.get('per_page')
                if per_page:
                    embed.add_field(name="Per page", value=f"{per_page}", inline=False)
                
                embed.set_footer(text="IP intelligence provided by Cloudflare")
                await ctx.send(embed=embed)
            else:
                error_message = data.get("errors", [{"message": "Unknown error"}])[0].get("message", "Unknown error")
                embed = discord.Embed(title="Error", description=f"Error: {error_message}", color=0xff4545)
                await ctx.send(embed=embed)

    @intel.command(name="domainhistory")
    async def domainhistory(self, ctx, domain: str):
        """
        View information about a domain's history
        """
        # Check if the input is an IP address
        try:
            ip_obj = ipaddress.ip_address(domain)
            embed = discord.Embed(title="Error", description="The input appears to be an IP address. Please use the `ip` subcommand for IP address queries.", color=0xff4545)
            await ctx.send(embed=embed)
            return
        except ValueError:
            pass  # Not an IP address, continue with query

        api_tokens = await self.bot.get_shared_api_tokens("cloudflare")
        email = api_tokens.get("email")
        api_key = api_tokens.get("api_key")
        bearer_token = api_tokens.get("bearer_token")
        account_id = api_tokens.get("account_id")

        # Check if any required token is missing
        if not all([email, api_key, bearer_token, account_id]):
            embed = discord.Embed(title="Configuration Error", description="Missing one or more required API tokens. Please check your configuration.", color=0xff4545)
            await ctx.send(embed=embed)
            return

        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/intel/domain-history"
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "X-Auth-Email": email,
            "X-Auth-Key": api_key,
            "Content-Type": "application/json",
        }
        params = {"domain": domain}

        async with self.session.get(url, headers=headers, params=params) as response:
            if response.status == 200:
                data = await response.json()
                if data["success"] and data["result"]:
                    result = data["result"][0]
                    categorizations = result.get("categorizations", [])
                    pages = [categorizations[i:i + 5] for i in range(0, len(categorizations), 5)]
                    current_page = 0

                    def create_embed(page):
                        embed = discord.Embed(title=f"Domain history for {domain}", color=0xFF6633)
                        if "domain" in result:
                            embed.add_field(name="Domain", value=f"{result['domain']}", inline=True)
                        for categorization in page:
                            categories = ", ".join([f"- {category['name']}\n" for category in categorization["categories"]])
                            embed.add_field(name="Categories", value=categories, inline=True)
                            if "start" in categorization:
                                start_timestamp = discord.utils.format_dt(discord.utils.parse_time(categorization['start']), style='d')
                                embed.add_field(name="Beginning", value=f"{start_timestamp}", inline=True)
                            if "end" in categorization:
                                end_timestamp = discord.utils.format_dt(discord.utils.parse_time(categorization['end']), style='d')
                                embed.add_field(name="Ending", value=f"{end_timestamp}", inline=True)
                        return embed

                    message = await ctx.send(embed=create_embed(pages[current_page]))

                    if len(pages) > 1:
                        await message.add_reaction("◀️")
                        await message.add_reaction("❌")
                        await message.add_reaction("▶️")

                        def check(reaction, user):
                            return user == ctx.author and str(reaction.emoji) in ["◀️", "❌", "▶️"] and reaction.message.id == message.id

                        while True:
                            try:
                                reaction, user = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)

                                if str(reaction.emoji) == "▶️" and current_page < len(pages) - 1:
                                    current_page += 1
                                    await message.edit(embed=create_embed(pages[current_page]))
                                    await message.remove_reaction(reaction, user)

                                elif str(reaction.emoji) == "◀️" and current_page > 0:
                                    current_page -= 1
                                    await message.edit(embed=create_embed(pages[current_page]))
                                    await message.remove_reaction(reaction, user)

                                elif str(reaction.emoji) == "❌":
                                    await message.delete()
                                    break

                            except asyncio.TimeoutError:
                                break

                        try:
                            await message.clear_reactions()
                        except discord.Forbidden:
                            pass
                else:
                    embed = discord.Embed(title="No data available", description="There is no domain history available for this domain. Please try this query again later, as results are subject to update.", color=0xff4545)
                    await ctx.send(embed=embed)
            elif response.status == 400:
                embed = discord.Embed(title="Bad Request", description="The server could not understand the request due to invalid syntax.", color=0xff4545)
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(title="Failed to query Cloudflare API", description=f"Status code: {response.status}", color=0xff4545)
                await ctx.send(embed=embed)

    @intel.command(name="asn")
    async def asnintel(self, ctx, asn: int):
        """
        View information about an ASN
        """
        api_tokens = await self.bot.get_shared_api_tokens("cloudflare")
        email = api_tokens.get("email")
        api_key = api_tokens.get("api_key")
        bearer_token = api_tokens.get("bearer_token")
        account_id = api_tokens.get("account_id")

        # Check if any required token is missing
        if not all([email, api_key, bearer_token, account_id]):
            embed = discord.Embed(title="Configuration Error", description="Missing one or more required API tokens. Please check your configuration.", color=0xff4545)
            await ctx.send(embed=embed)
            return

        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/intel/asn/{asn}"
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "X-Auth-Email": email,
            "X-Auth-Key": api_key,
            "Content-Type": "application/json",
        }

        async with self.session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                if data["success"]:
                    result = data["result"]
                    embed = discord.Embed(title=f"Intelligence for ASN#{asn}", color=0xFF6633)
                    
                    if "asn" in result:
                        embed.add_field(name="ASN Number", value=f"{result['asn']}", inline=True)
                    if "description" in result:
                        owner_query = result['description'].replace(' ', '+')
                        google_search_url = f"https://www.google.com/search?q={owner_query}"
                        embed.add_field(name="Owner", value=f"[{result['description']}]({google_search_url})", inline=True)
                    if "country" in result:
                        embed.add_field(name="Region", value=f":flag_{result['country'].lower()}: {result['country']}", inline=True)
                    if "type" in result:
                        embed.add_field(name="Type", value=f"{result['type'].capitalize()}", inline=True)
                    if "risk_score" in result:
                        embed.add_field(name="Risk score", value=f"{result['risk_score']}", inline=True)
                    embed.set_footer(text="ASN intelligence provided by Cloudflare")
                    await ctx.send(embed=embed)
                else:
                    embed = discord.Embed(title="Error", description=f"Error: {data['errors']}", color=0xff4545)
                    await ctx.send(embed=embed)
            elif response.status == 400:
                embed = discord.Embed(title="Bad Request", description="The server could not understand the request due to invalid syntax.", color=0xff4545)
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(title="Failed to query Cloudflare API", description=f"Status code: {response.status}", color=0xff4545)
                await ctx.send(embed=embed)

    @intel.command(name="subnets")
    async def asnsubnets(self, ctx, asn: int):
        """
        View information for ASN subnets
        """
        api_tokens = await self.bot.get_shared_api_tokens("cloudflare")
        email = api_tokens.get("email")
        api_key = api_tokens.get("api_key")
        bearer_token = api_tokens.get("bearer_token")
        account_id = api_tokens.get("account_id")

        # Check if any required token is missing
        if not all([email, api_key, bearer_token, account_id]):
            embed = discord.Embed(title="Configuration Error", description="Missing one or more required API tokens. Please check your configuration.", color=0xff4545)
            await ctx.send(embed=embed)
            return

        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/intel/asn/{asn}/subnets"
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "X-Auth-Email": email,
            "X-Auth-Key": api_key,
            "Content-Type": "application/json",
        }

        async with self.session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                if data["success"]:
                    result = data["result"]
                    subnets = result.get("subnets", [])
                    
                    if subnets:
                        pages = [subnets[i:i + 10] for i in range(0, len(subnets), 10)]
                        current_page = 0
                        embed = discord.Embed(title=f"Subnets for ASN#{asn}", color=0xFF6633)
                        embed.add_field(name="Subnets", value="\n".join([f"- {subnet}" for subnet in pages[current_page]]), inline=False)
                        message = await ctx.send(embed=embed)

                        if len(pages) > 1:
                            await message.add_reaction("◀️")
                            await message.add_reaction("❌")
                            await message.add_reaction("▶️")

                            def check(reaction, user):
                                return user == ctx.author and str(reaction.emoji) in ["◀️", "❌", "▶️"] and reaction.message.id == message.id

                            while True:
                                try:
                                    reaction, user = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)

                                    if str(reaction.emoji) == "▶️" and current_page < len(pages) - 1:
                                        current_page += 1
                                        embed.clear_fields()
                                        for subnet in pages[current_page]:
                                            embed.add_field(name="Subnet", value=f"**`{subnet}`**", inline=False)
                                        await message.edit(embed=embed)
                                        await message.remove_reaction(reaction, user)

                                    elif str(reaction.emoji) == "◀️" and current_page > 0:
                                        current_page -= 1
                                        embed.clear_fields()
                                        for subnet in pages[current_page]:
                                            embed.add_field(name="Subnet", value=f"**`{subnet}`**", inline=False)
                                        await message.edit(embed=embed)
                                        await message.remove_reaction(reaction, user)

                                    elif str(reaction.emoji) == "❌":
                                        await message.delete()
                                        break

                                except asyncio.TimeoutError:
                                    await message.clear_reactions()
                                    break
                    else:
                        embed = discord.Embed(title=f"Subnets for ASN#{asn}", color=0xFF6633)
                        embed.add_field(name="Subnets", value="No subnets found for this ASN.", inline=False)
                        await ctx.send(embed=embed)
                else:
                    embed = discord.Embed(title="Error", description=f"Error: {data['errors']}", color=0xff4545)
                    await ctx.send(embed=embed)
            elif response.status == 400:
                embed = discord.Embed(title="Bad Request", description="The server could not understand the request due to invalid syntax.", color=0xff4545)
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(title="Failed to query Cloudflare API", description=f"Status code: {response.status}", color=0xff4545)
                await ctx.send(embed=embed)