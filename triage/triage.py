import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box
import asyncio

# Use the triage package (https://pypi.org/project/triage/)
from triage import Client as TriageClient

class Triage(commands.Cog):
    """
    Malware analysis for files using tria.ge API.
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
        self._triage_clients = {}

    async def cog_unload(self):
        # No explicit close needed for triage.Client
        self._triage_clients.clear()

    async def _get_api_key(self, guild):
        # Use the global keystore for the triage apikey
        tokens = await self.bot.get_shared_api_tokens("triage")
        api_key = tokens.get("apikey")
        if not api_key or not isinstance(api_key, str) or not api_key.strip():
            return None
        return api_key.strip()

    async def _get_log_channel(self, guild):
        channel_id = await self.config.guild(guild).log_channel()
        if channel_id:
            return guild.get_channel(channel_id)
        return None

    def _get_triage_client(self, api_key):
        # Cache TriageClient per API key
        if api_key not in self._triage_clients:
            self._triage_clients[api_key] = TriageClient(token=api_key)
        return self._triage_clients[api_key]

    async def _submit_file(
        self,
        api_key,
        file_bytes,
        filename,
        *,
        interactive=None,
        profiles=None,
    ):
        """
        Submit a file to Triage using triage.Client.
        """
        triage = self._get_triage_client(api_key)
        import io
        file_obj = io.BytesIO(file_bytes)
        # triage.Client expects a file-like object with a name attribute
        file_obj.name = filename

        # triage.Client.submit_sample_file expects: filename, file, interactive, profiles
        # interactive: bool, default False
        # profiles: list of str, default []
        kwargs = {}
        if interactive is not None:
            kwargs["interactive"] = bool(interactive)
        if profiles:
            kwargs["profiles"] = profiles
        else:
            kwargs["profiles"] = []

        # submit_sample_file is a coroutine
        return await triage.submit_sample_file(filename, file_obj, **kwargs)

    async def _wait_for_reported(self, api_key, sample_id, poll_interval=10, timeout=300):
        """
        Polls the Triage API for the sample status until it is 'reported' or timeout is reached.
        """
        triage = self._get_triage_client(api_key)
        elapsed = 0
        while elapsed < timeout:
            sample = await triage.sample_by_id(sample_id)
            status = sample.get("status")
            if status == "reported":
                return True
            elif status in ("failed", "error"):
                raise RuntimeError(f"Triage analysis failed: {status}")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise RuntimeError("Timed out waiting for Triage analysis to complete.")

    async def _get_static_report(self, api_key, sample_id):
        """
        Fetches the static report for the sample.
        """
        triage = self._get_triage_client(api_key)
        try:
            return await triage.static_report(sample_id)
        except Exception as e:
            raise RuntimeError(f"Triage API static report error: {e}")

    async def _analyze_attachment(
        self,
        guild,
        attachment,
        submitter=None,
        *,
        interactive=None,
        profiles=None,
    ):
        """
        Analyze a Discord attachment using Triage.
        """
        api_key = await self._get_api_key(guild)
        if not api_key:
            return "Triage API key not set in Red's keystore. Use `[p]triage apikey <key>` to set it."
        try:
            file_bytes = await attachment.read()
            submit_result = await self._submit_file(
                api_key,
                file_bytes,
                attachment.filename,
                interactive=interactive,
                profiles=profiles,
            )
            sample_id = submit_result.get("id")
            if not sample_id:
                return "Failed to submit file to Triage."

            # Wait for the sample to be fully analyzed (status == "reported")
            try:
                await self._wait_for_reported(api_key, sample_id)
            except Exception as e:
                return f"Error waiting for analysis: {e}"

            # Fetch static report
            try:
                static_report = await self._get_static_report(api_key, sample_id)
            except Exception as e:
                return f"Error fetching static report: {e}"

            # Parse static report for summary
            summary_lines = []
            sample_info = static_report.get("sample", {})
            static = static_report.get("static", {})
            pe = static.get("pe", {})
            elf = static.get("elf", {})
            macho = static.get("macho", {})
            tags = static_report.get("tags", [])
            verdict = static_report.get("verdict", "unknown")
            score = static_report.get("score", "unknown")

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
            summary_lines.append(f"**Verdict:** `{verdict}`")
            summary_lines.append(f"**Score:** `{score}`")

            # Tags
            if tags:
                summary_lines.append("**Tags:** " + ", ".join(f"`{tag}`" for tag in tags))

            # PE info
            if pe:
                imphash = pe.get("imphash")
                entrypoint = pe.get("entrypoint")
                compile_ts = pe.get("compile_ts")
                sections = pe.get("sections", [])
                if imphash:
                    summary_lines.append(f"**PE Imphash:** `{imphash}`")
                if entrypoint:
                    summary_lines.append(f"**PE Entrypoint:** `{entrypoint}`")
                if compile_ts:
                    summary_lines.append(f"**PE Compile Time:** `{compile_ts}`")
                if sections:
                    summary_lines.append(f"**PE Sections:** {', '.join(s.get('name', '') for s in sections[:5])}")

            # ELF info
            if elf:
                entrypoint = elf.get("entrypoint")
                arch = elf.get("arch")
                if entrypoint:
                    summary_lines.append(f"**ELF Entrypoint:** `{entrypoint}`")
                if arch:
                    summary_lines.append(f"**ELF Arch:** `{arch}`")

            # Mach-O info
            if macho:
                entrypoint = macho.get("entrypoint")
                arch = macho.get("arch")
                if entrypoint:
                    summary_lines.append(f"**Mach-O Entrypoint:** `{entrypoint}`")
                if arch:
                    summary_lines.append(f"**Mach-O Arch:** `{arch}`")

            # Save to submission history
            async with self.config.guild(guild).submission_history() as history:
                history[attachment.filename] = {
                    "sample_id": sample_id,
                    "static_report": static_report,
                    "submitter": str(submitter) if submitter else None,
                }
            return "\n".join(summary_lines)
        except Exception as e:
            return f"Error analyzing file: {e}"

    @commands.group(name="triage", invoke_without_command=True)
    async def triage(self, ctx):
        """
        Analyze files for malware using tria.ge API.
        """
        await ctx.send_help()

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
            static_report = data.get("static_report")
            if static_report:
                score = static_report.get("score", "unknown")
                verdict = static_report.get("verdict", "unknown")
                verdict_str = f"Score: {score}, Verdict: {verdict}"
            else:
                verdict_str = data.get("verdict", "unknown")
            submitter = data.get("submitter", "unknown")
            lines.append(f"**{filename}** - `{verdict_str}` (by {submitter})")
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
        interactive: bool = None,
        profiles: str = None,
    ):
        """
        Manually scan attachments in your message for malware.

        Optional arguments:
        [--interactive true|false] [--profiles prof1,prof2,...]

        Example:
        [p]triage scan --interactive true --profiles win7,win10
        """
        if not ctx.message.attachments:
            await ctx.send("Please attach a file to scan.")
            return
        api_key = await self._get_api_key(ctx.guild)
        if not api_key:
            await ctx.send("Triage API key not set in Red's keystore. Use `[p]triage apikey <key>` to set it.")
            return

        # Parse profiles if provided as comma-separated string
        profiles_list = None
        if profiles:
            profiles_list = [p.strip() for p in profiles.split(",") if p.strip()]
        else:
            profiles_list = []

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
                interactive=interactive_bool,
                profiles=profiles_list,
            )
            await ctx.send(result)
