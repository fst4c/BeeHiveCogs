# Copyright (C) 2020-2023 Hatching B.V
# All rights reserved.

import discord
from redbot.core import commands, Config, app_commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, pagify, humanize_list

from io import BytesIO
from triage.pagination import Paginator
from .__version__ import __version__
from requests import Request, Session, exceptions, utils

import binascii
import urllib3
import json
import os
import platform
import asyncio

urllib3.disable_warnings()

class TriageAnalysis(commands.Cog):
    """
    Triage Analysis - Interact with the Triage API from Discord.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xDEADBEEF, force_registration=True)
        default_guild = {
            "token": None
        }
        self.config.register_guild(**default_guild)

    async def get_client(self, guild):
        # Use the triage api_key stored in Red's shared API tokens
        api_key = await self.bot.get_shared_api_tokens("triage")
        token = api_key.get("api_key")
        if not token:
            raise RuntimeError("Triage API key not set. Use `[p]set api triage api_key,<token>` to set it.")
        return Client(token)

    @commands.group()
    async def triage(self, ctx):
        """Triage API commands."""
        pass

    @triage.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def settoken(self, ctx, token: str):
        """Set the Triage API token for this server (deprecated, use `[p]set api triage api_key,<token>` instead)."""
        await self.config.guild(ctx.guild).token.set(token)
        await ctx.send("Triage API token set for this server. (Note: This is now deprecated, use `[p]set api triage api_key,<token>` instead.)")

    @triage.command()
    async def submiturl(self, ctx, url: str):
        """Submit a URL for analysis."""
        try:
            client = await self.get_client(ctx.guild)
            data = client.submit_sample_url(url)
            await ctx.send(f"Sample submitted! ID: `{data.get('id')}` Status: `{data.get('status')}`")
        except Exception as e:
            await ctx.send(f"Error: {e}")

    @triage.command()
    async def sample(self, ctx, sample_id: str):
        """Get info about a sample by ID."""
        try:
            client = await self.get_client(ctx.guild)
            data = client.sample_by_id(sample_id)
            await ctx.send(box(json.dumps(data, indent=2), lang="json"))
        except Exception as e:
            await ctx.send(f"Error: {e}")

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
                results.append(f"{sample.get('id', 'N/A')}: {sample.get('status', 'N/A')}")
            if results:
                await ctx.send(box("\n".join(results)))
            else:
                await ctx.send("No results found.")
        except Exception as e:
            await ctx.send(f"Error: {e}")

    @triage.command()
    async def staticreport(self, ctx, sample_id: str):
        """Get the static report for a sample."""
        try:
            client = await self.get_client(ctx.guild)
            data = client.static_report(sample_id)
            for page in pagify(json.dumps(data, indent=2), page_length=1900):
                await ctx.send(box(page, lang="json"))
        except Exception as e:
            await ctx.send(f"Error: {e}")

    @triage.command()
    async def overview(self, ctx, sample_id: str):
        """Get the overview report for a sample."""
        try:
            client = await self.get_client(ctx.guild)
            data = client.overview_report(sample_id)
            for page in pagify(json.dumps(data, indent=2), page_length=1900):
                await ctx.send(box(page, lang="json"))
        except Exception as e:
            await ctx.send(f"Error: {e}")

    @triage.command()
    async def download(self, ctx, sample_id: str):
        """Download the sample file."""
        try:
            client = await self.get_client(ctx.guild)
            file_bytes = client.get_sample_file(sample_id)
            await ctx.send(file=discord.File(BytesIO(file_bytes), filename=f"{sample_id}.bin"))
        except Exception as e:
            await ctx.send(f"Error: {e}")

    @triage.command()
    async def events(self, ctx, sample_id: str):
        """Stream events of a running sample (first 10 events)."""
        try:
            client = await self.get_client(ctx.guild)
            events = client.sample_events(sample_id)
            lines = []
            for i, event in enumerate(events):
                if i >= 10:
                    break
                lines.append(json.dumps(event))
            if lines:
                await ctx.send(box("\n".join(lines), lang="json"))
            else:
                await ctx.send("No events found.")
        except Exception as e:
            await ctx.send(f"Error: {e}")

    @triage.command()
    async def submitfile(self, ctx):
        """Submit a file for analysis. Attach a file to this command."""
        if not ctx.message.attachments:
            await ctx.send("Please attach a file to submit.")
            return
        attachment = ctx.message.attachments[0]
        try:
            client = await self.get_client(ctx.guild)
            file_bytes = await attachment.read()
            filename = attachment.filename
            # Use BytesIO for file-like object
            data = client.submit_sample_file(filename, BytesIO(file_bytes))
            await ctx.send(f"Sample submitted! ID: `{data.get('id')}` Status: `{data.get('status')}`")
        except Exception as e:
            await ctx.send(f"Error: {e}")

    @triage.command()
    async def analyze(self, ctx):
        """
        Submit a file for analysis and return the results (score, tags, etc) after analysis is complete.
        Attach a file to this command.
        """
        if not ctx.message.attachments:
            await ctx.send("Please attach a file to analyze.")
            return
        attachment = ctx.message.attachments[0]
        try:
            client = await self.get_client(ctx.guild)
            file_bytes = await attachment.read()
            filename = attachment.filename
            await ctx.send("Submitting file for analysis...")
            data = client.submit_sample_file(filename, BytesIO(file_bytes))
            sample_id = data.get("id")
            if not sample_id:
                await ctx.send("Failed to submit file for analysis.")
                return
            await ctx.send(f"Sample submitted! ID: `{sample_id}`. Waiting for analysis to complete...")

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
                await ctx.send(f"Analysis did not complete in {max_wait} seconds. Status: `{status}`")
                return

            # Try to get overview report
            try:
                overview = client.overview_report(sample_id)
            except Exception as e:
                await ctx.send(f"Analysis finished, but failed to fetch overview report: {e}")
                return

            # Send the entire overview as a JSON file
            overview_json = json.dumps(overview, indent=2)
            overview_bytes = BytesIO(overview_json.encode("utf-8"))
            overview_bytes.seek(0)

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

            # Compose signatures summary as "SCORE | text (TTP's)"
            sigs = []
            for sig in signatures:
                score_ = sig.get("score")
                text = sig.get("name") or sig.get("label") or ""
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
                    sigs.append(f"**{score_}** | *{text}*{ttps_str}\n")
                elif text:
                    sigs.append(f"{text}{ttps_str}")
            sigs_str = humanize_list(sigs) if sigs else "None"

            # Compose IOC summary (URLs, domains, IPs)
            iocs = {}
            if targets and "iocs" in targets[0]:
                iocs = targets[0]["iocs"]
            urls = iocs.get("urls", []) if iocs else []
            domains = iocs.get("domains", []) if iocs else []
            ips = iocs.get("ips", []) if iocs else []

            # Compose tags
            tags_str = ", ".join(tags) if tags else "None"

            # Compose tasks summary
            task_lines = []
            for t in tasks:
                t_name = t.get("name", "N/A")
                t_kind = t.get("kind", "N/A")
                t_score = t.get("score", "N/A")
                t_tags = ", ".join(t.get("tags", [])) if t.get("tags") else ""
                task_lines.append(f"{t_name} ({t_kind}) - Score: {t_score}" + (f" | Tags: {t_tags}" if t_tags else ""))
            tasks_str = "\n".join(task_lines) if task_lines else "None"

            # Compose embed
            embed = discord.Embed(
                title="Triage Analysis Results",
                description=f"Sample ID: `{sample_id}`\nStatus: `{status}`",
                color=discord.Color.orange() if score and score >= 5 else discord.Color.green() if score and score < 5 else discord.Color.default()
            )
            if target_name:
                embed.add_field(name="Target", value=target_name, inline=True)
            if sample_size:
                embed.add_field(name="Size", value=f"{sample_size:,} bytes", inline=True)
            if score is not None:
                embed.add_field(name="Score", value=str(score), inline=True)
            if verdict:
                embed.add_field(name="Verdict", value=verdict, inline=True)
            if family:
                embed.add_field(name="Family", value=family, inline=True)
            if tags_str:
                embed.add_field(name="Tags", value=tags_str, inline=False)
            if md5:
                embed.add_field(name="MD5", value=md5, inline=False)
            if sha1:
                embed.add_field(name="SHA1", value=sha1, inline=False)
            if sha256:
                embed.add_field(name="SHA256", value=sha256, inline=False)
            if ssdeep:
                embed.add_field(name="SSDEEP", value=ssdeep, inline=False)
            if created:
                embed.add_field(name="Created", value=created, inline=True)
            if completed:
                embed.add_field(name="Completed", value=completed, inline=True)
            if tasks_str:
                embed.add_field(name="Tasks", value=tasks_str, inline=False)
            if sigs_str:
                embed.add_field(name="Signatures", value=sigs_str, inline=False)
            if urls:
                embed.add_field(name="URLs", value="\n".join(urls[:5]) + (f"\n...and {len(urls)-5} more" if len(urls) > 5 else ""), inline=False)
            if domains:
                embed.add_field(name="Domains", value=", ".join(domains[:5]) + (f", ...and {len(domains)-5} more" if len(domains) > 5 else ""), inline=False)
            if ips:
                embed.add_field(name="IPs", value=", ".join(ips[:5]) + (f", ...and {len(ips)-5} more" if len(ips) > 5 else ""), inline=False)

            embed.set_footer(text="Full overview report attached as JSON.")

            await ctx.send(
                content="Analysis complete! See below for summary and attached overview report.",
                embed=embed,
                file=discord.File(overview_bytes, filename=f"{sample_id}_overview.json")
            )
        except Exception as e:
            await ctx.send(f"Error: {e}")

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
