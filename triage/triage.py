import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box
import aiohttp
import asyncio
import json

class Triage(commands.Cog):
    """
    Malware analysis for files using hatchling-triage.
    Analyze files manually and automatically for malware.
    """

    __author__ = "BeeHive"
    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xBEEBEEBEEBEEBEE)
        self.config.register_guild(
            auto_scan_enabled=False,
            log_channel=None,
            submission_history={},
        )
        self.session = aiohttp.ClientSession()
        self.triage_api_url = "https://api.tria.ge/v0/samples"

    async def cog_unload(self):
        await self.session.close()

    async def _get_api_key(self, guild):
        # Use the global keystore for the triage apikey
        # The key is stored as "triage" -> "apikey"
        # This is a global key, not per-guild
        tokens = await self.bot.get_shared_api_tokens("triage")
        return tokens.get("apikey")

    async def _get_log_channel(self, guild):
        channel_id = await self.config.guild(guild).log_channel()
        if channel_id:
            return guild.get_channel(channel_id)
        return None

    async def _submit_file(
        self,
        api_key,
        file_bytes,
        filename,
        *,
        target=None,
        password=None,
        user_tags=None,
        timeout=None,
        network=None,
        interactive=None,
        profiles=None,
    ):
        """
        Submit a file to Triage with optional parameters.

        :param api_key: Triage API key
        :param file_bytes: File content (bytes)
        :param filename: Name of the file
        :param target: Optional custom filename for the sample
        :param password: Optional password for archive
        :param user_tags: Optional list of user tags
        :param timeout: Optional timeout (int, seconds)
        :param network: Optional network type ("internet", "drop", "tor")
        :param interactive: Optional bool, if true, manual profile selection
        :param profiles: Optional list of profiles
        """
        headers = {
            "Authorization": f"Bearer {api_key}",
        }
        data = aiohttp.FormData()
        data.add_field("file", file_bytes, filename=filename)

        # If any nested/complex fields are present, use _json
        use_json = profiles is not None

        if use_json:
            # Build the JSON payload
            payload = {"kind": "file"}
            if target:
                payload["target"] = target
            if password:
                payload["password"] = password
            if user_tags:
                payload["user_tags"] = user_tags
            if interactive is not None:
                payload["interactive"] = interactive
            if profiles:
                payload["profiles"] = profiles
            defaults = {}
            if timeout is not None:
                defaults["timeout"] = timeout
            if network:
                defaults["network"] = network
            if defaults:
                payload["defaults"] = defaults
            data.add_field("_json", json.dumps(payload))
        else:
            # Use simple form fields
            data.add_field("kind", "file")
            if target:
                data.add_field("target", target)
            if password:
                data.add_field("password", password)
            if user_tags:
                for tag in user_tags:
                    data.add_field("user_tags", tag)
            if interactive is not None:
                data.add_field("interactive", str(interactive).lower())
            if timeout is not None:
                data.add_field("defaults.timeout", str(timeout))
            if network:
                data.add_field("defaults.network", network)

        async with self.session.post(self.triage_api_url, headers=headers, data=data) as resp:
            if resp.status == 201:
                return await resp.json()
            else:
                text = await resp.text()
                raise RuntimeError(f"Triage API error: {resp.status} {text}")

    async def _get_overview(self, api_key, sample_id):
        """
        Waits 3 minutes, then fetches the overview report for the sample.
        """
        await asyncio.sleep(180)  # Wait 3 minutes
        headers = {
            "Authorization": f"Bearer {api_key}",
        }
        url = f"{self.triage_api_url}/{sample_id}/overview.json"
        async with self.session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.json()
            elif resp.status == 404:
                raise RuntimeError("Sample not found in Triage overview.")
            else:
                text = await resp.text()
                raise RuntimeError(f"Triage API overview error: {resp.status} {text}")

    async def _analyze_attachment(
        self,
        guild,
        attachment,
        submitter=None,
        *,
        target=None,
        password=None,
        user_tags=None,
        timeout=None,
        network=None,
        interactive=None,
        profiles=None,
    ):
        """
        Analyze a Discord attachment using Triage.

        :param guild: Discord guild
        :param attachment: Discord attachment
        :param submitter: User who submitted
        :param target: Optional custom filename
        :param password: Optional password for archive
        :param user_tags: Optional list of user tags
        :param timeout: Optional timeout (int, seconds)
        :param network: Optional network type
        :param interactive: Optional bool
        :param profiles: Optional list of profiles
        """
        api_key = await self._get_api_key(guild)
        if not api_key:
            return "Triage API key not set in Red's keystore. Use `[p]set api triage apikey,<key>` to set it."
        try:
            file_bytes = await attachment.read()
            submit_result = await self._submit_file(
                api_key,
                file_bytes,
                attachment.filename,
                target=target,
                password=password,
                user_tags=user_tags,
                timeout=timeout,
                network=network,
                interactive=interactive,
                profiles=profiles,
            )
            sample_id = submit_result.get("id")
            if not sample_id:
                return "Failed to submit file to Triage."

            # Wait 3 minutes and fetch overview
            try:
                overview = await self._get_overview(api_key, sample_id)
            except Exception as e:
                return f"Error fetching overview: {e}"

            # Parse overview for summary
            summary_lines = []
            sample_info = overview.get("sample", {})
            analysis = overview.get("analysis", {})
            targets = overview.get("targets", [])
            extracted = overview.get("extracted", [])
            tasks = overview.get("tasks", {})

            # Basic info
            summary_lines.append(f"**Sample ID:** `{sample_id}`")
            if "target" in sample_info:
                summary_lines.append(f"**Target:** `{sample_info.get('target')}`")
            if "sha256" in sample_info:
                summary_lines.append(f"**SHA256:** `{sample_info.get('sha256')}`")
            if "md5" in sample_info:
                summary_lines.append(f"**MD5:** `{sample_info.get('md5')}`")
            if "size" in sample_info:
                summary_lines.append(f"**Size:** `{sample_info.get('size')} bytes`")
            if "score" in analysis:
                summary_lines.append(f"**Analysis Score:** `{analysis.get('score')}`")

            # Tags, families, signatures
            if targets:
                t = targets[0]
                tags = t.get("tags", [])
                family = t.get("family", [])
                signatures = t.get("signatures", [])
                if tags:
                    summary_lines.append("**Tags:** " + ", ".join(f"`{tag}`" for tag in tags))
                if family:
                    summary_lines.append("**Family:** " + ", ".join(f"`{fam}`" for fam in family))
                if signatures:
                    sig_lines = []
                    for sig in signatures[:3]:  # Show up to 3 signatures
                        label = sig.get("label", "")
                        name = sig.get("name", "")
                        score = sig.get("score", "")
                        desc = sig.get("desc", "")
                        sig_lines.append(f"- **{name}** (`{label}`), Score: `{score}`\n  {desc}")
                    summary_lines.append("**Signatures:**\n" + "\n".join(sig_lines))
            # Extracted configs
            if extracted:
                for ex in extracted:
                    config = ex.get("config")
                    if config:
                        family = config.get("family", "")
                        rule = config.get("rule", "")
                        c2s = config.get("c2", [])
                        version = config.get("version", "")
                        botnet = config.get("botnet", "")
                        summary_lines.append(f"**Extracted Config:** Family: `{family}` Rule: `{rule}` Version: `{version}` Botnet: `{botnet}`")
                        if c2s:
                            summary_lines.append("**C2s:**\n" + "\n".join(f"`{c2}`" for c2 in c2s[:10]))  # Show up to 10 C2s
                        break  # Only show first config for brevity

            # Tasks and their tags/scores
            if tasks:
                task_lines = []
                for task_id, task in tasks.items():
                    kind = task.get("kind", "")
                    status = task.get("status", "")
                    score = task.get("score", "")
                    tags = task.get("tags", [])
                    task_lines.append(f"- **{task_id}**: Kind: `{kind}` Status: `{status}` Score: `{score}` Tags: {', '.join(f'`{tag}`' for tag in tags)}")
                summary_lines.append("**Tasks:**\n" + "\n".join(task_lines[:5]))  # Show up to 5 tasks

            # Save to submission history
            async with self.config.guild(guild).submission_history() as history:
                history[attachment.filename] = {
                    "sample_id": sample_id,
                    "overview": overview,
                    "submitter": str(submitter) if submitter else None,
                }
            return "\n".join(summary_lines)
        except Exception as e:
            return f"Error analyzing file: {e}"

    @commands.group(name="triage", invoke_without_command=True)
    async def triage(self, ctx):
        """
        Analyze files for malware using hatchling-triage.
        """
        await ctx.send_help()

    @triage.command(name="apikey")
    @commands.admin_or_permissions(manage_guild=True)
    async def triage_apikey(self, ctx, api_key: str):
        """
        Set the Triage API key for this bot (global, Red keystore).
        """
        # Store the API key in Red's keystore, not in config
        await self.bot.set_shared_api_tokens("triage", apikey=api_key)
        await ctx.send("Triage API key set in Red's keystore (global).")

    @triage.command(name="autolog")
    @commands.admin_or_permissions(manage_guild=True)
    async def triage_autolog(self, ctx, channel: discord.TextChannel = None):
        """
        Set the log channel for Triage results.
        """
        if channel:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            await ctx.send(f"Triage log channel set to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel.set(None)
            await ctx.send("Triage log channel unset.")

    @triage.command(name="autoscan")
    @commands.admin_or_permissions(manage_guild=True)
    async def triage_autoscan(self, ctx, enabled: bool):
        """
        Enable or disable automatic file scanning.
        """
        await self.config.guild(ctx.guild).auto_scan_enabled.set(enabled)
        await ctx.send(f"Automatic file scanning {'enabled' if enabled else 'disabled'}.")

    @triage.command(name="history")
    async def triage_history(self, ctx):
        """
        Show the last 5 Triage file analysis results.
        """
        history = await self.config.guild(ctx.guild).submission_history()
        if not history:
            await ctx.send("No file analysis history found.")
            return
        items = list(history.items())[-5:]
        lines = []
        for filename, data in items:
            overview = data.get("overview")
            if overview:
                analysis = overview.get("analysis", {})
                verdict = f"Score: {analysis.get('score', 'unknown')}"
            else:
                verdict = data.get("verdict", "unknown")
            submitter = data.get("submitter", "unknown")
            lines.append(f"**{filename}** - `{verdict}` (by {submitter})")
        await ctx.send("\n".join(lines))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if not message.attachments:
            return
        enabled = await self.config.guild(message.guild).auto_scan_enabled()
        if not enabled:
            return
        api_key = await self._get_api_key(message.guild)
        if not api_key:
            return
        log_channel = await self._get_log_channel(message.guild)
        for attachment in message.attachments:
            # You could add more advanced parameter parsing here if desired
            result = await self._analyze_attachment(message.guild, attachment, submitter=message.author)
            embed = discord.Embed(
                title="Triage Malware Scan Result",
                description=result,
                color=discord.Color.red() if "malicious" in result.lower() or "score: 10" in result.lower() else discord.Color.green(),
            )
            embed.add_field(name="File", value=attachment.filename)
            embed.add_field(name="User", value=message.author.mention)
            if log_channel:
                await log_channel.send(embed=embed)
            else:
                try:
                    await message.channel.send(embed=embed)
                except Exception:
                    pass

    @triage.command(name="scan")
    async def triage_scan(
        self,
        ctx,
        *,
        target: str = None,
        password: str = None,
        user_tags: str = None,
        timeout: int = None,
        network: str = None,
        interactive: bool = None,
    ):
        """
        Manually scan attachments in your message for malware.

        Optional arguments:
        [--target FILENAME] [--password PASSWORD] [--user_tags TAG1,TAG2,...] [--timeout SECONDS] [--network internet|drop|tor] [--interactive true|false]

        Example:
        [p]triage scan --target myfile.exe --user_tags id:123,source:smtp --timeout 60 --network tor
        """
        if not ctx.message.attachments:
            await ctx.send("Please attach a file to scan.")
            return
        api_key = await self._get_api_key(ctx.guild)
        if not api_key:
            await ctx.send("Triage API key not set in Red's keystore. Use `[p]triage apikey <key>` to set it.")
            return

        # Parse user_tags if provided as comma-separated string
        tags = None
        if user_tags:
            tags = [t.strip() for t in user_tags.split(",") if t.strip()]

        # Parse interactive if provided as string
        interactive_bool = None
        if interactive is not None:
            if isinstance(interactive, bool):
                interactive_bool = interactive
            elif isinstance(interactive, str):
                interactive_bool = interactive.lower() in ("true", "yes", "1")

        for attachment in ctx.message.attachments:
            await ctx.send(f"Analyzing `{attachment.filename}`...")
            result = await self._analyze_attachment(
                ctx.guild,
                attachment,
                submitter=ctx.author,
                target=target,
                password=password,
                user_tags=tags,
                timeout=timeout,
                network=network,
                interactive=interactive_bool,
            )
            await ctx.send(result)
