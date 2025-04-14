import discord
from redbot.core import commands, Config
import typing
import os
from datetime import timedelta
import asyncio
import random

class Honeypot(commands.Cog, name="Honeypot"):
    """Create a channel at the top of the server to attract self bots/scammers and notify/mute/kick/ban them immediately!"""

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        default_guild = {
            "enabled": False,
            "action": None,
            "logs_channel": None,
            "ping_role": None,
            "honeypot_channel": None,
            "mute_role": None,
            "ban_delete_message_days": 3,
            "scam_stats": {"nitro": 0, "steam": 0, "other": 0, "csam": 0},
        }
        self.config.register_guild(**default_guild)
        self.global_scam_stats = {"nitro": 0, "steam": 0, "other": 0, "csam": 0}
        self.bot.loop.create_task(self.randomize_honeypot_name())

    async def randomize_honeypot_name(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            for guild in self.bot.guilds:
                config = await self.config.guild(guild).all()
                honeypot_channel_id = config.get("honeypot_channel")
                honeypot_channel = guild.get_channel(honeypot_channel_id) if honeypot_channel_id else None

                if honeypot_channel:
                    dictionary_words = [
                        "level-up", "boss-fight", "loot-box", "quest", "avatar", "guild", "raid", 
                        "dungeon", "pvp", "pve", "respawn", "checkpoint", "leaderboard", "achievement", 
                        "skill-tree", "power-up", "gamepad", "joystick", "console", "arcade", "multiplayer", 
                        "singleplayer", "sandbox", "open-world", "rpg", "fps", "mmo", "strategy", 
                        "simulation", "platformer", "indie", "esports", "tournament", "speedrun", 
                        "modding", "patch", "update", "expansion", "dlc", "beta", "alpha", "early-access", 
                        "game-jam", "pixel-art", "retro", "8-bit", "16-bit", "soundtrack", "cutscene", 
                        "npc", "ai", "game-engine", "physics", "graphics", "rendering", "animation", 
                        "storyline", "narrative", "dialogue", "character-design", "level-design", 
                        "gameplay", "mechanics", "balance", "difficulty", "tutorial", "walkthrough", 
                        "cheat-code", "easter-egg", "glitch", "bug", "patch-notes", "server", "lag", 
                        "ping", "fps-drop", "frame-rate", "resolution", "texture", "shader", "voxel", 
                        "polygon", "vertex", "mesh", "rigging", "skinning", "motion-capture", "voice-acting", 
                        "sound-effects", "ambient-sound", "background-music", "game-theory", "game-design", 
                        "user-interface", "hud", "cross-platform", "cloud-gaming", "streaming", "vr", 
                        "ar", "mixed-reality", "haptic-feedback", "game-economy", "microtransactions", 
                        "in-game-currency", "loot-crate", "battle-pass", "season-pass", "skins", "cosmetics", 
                        "emotes", "dance", "taunt", "clan", "faction", "alliance", "team", "co-op", 
                        "competitive", "ranked", "casual", "hardcore", "permadeath", "roguelike", "metroidvania",
                        "tourist", "sightseeing", "landmark", "itinerary", "excursion", "souvenir", 
                        "travel-guide", "backpacking", "adventure", "resort", "cruise", "destination", 
                        "vacation", "holiday", "tour", "expedition", "journey", "exploration", "getaway"
                    ]
                    random_name = random.choice(dictionary_words)
                    try:
                        await honeypot_channel.edit(name=random_name, reason="Changing channel name to impede honeypot evasion efforts")
                    except discord.HTTPException:
                        pass

            await asyncio.sleep(4 * 60 * 60)  # Wait for 4 hours

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return

        config = await self.config.guild(message.guild).all()
        honeypot_channel_id = config.get("honeypot_channel")
        logs_channel_id = config.get("logs_channel")
        logs_channel = message.guild.get_channel(logs_channel_id) if logs_channel_id else None

        if not config["enabled"] or not honeypot_channel_id or not logs_channel or message.channel.id != honeypot_channel_id:
            return

        if message.author.id in self.bot.owner_ids or message.author.guild_permissions.manage_guild or message.author.top_role >= message.guild.me.top_role:
            return

        try:
            await message.delete()
        except discord.HTTPException:
            pass

        # Track scam type based on message content
        scam_type = "other"
        content_lower = message.content.lower()
        if "nitro" in content_lower:
            scam_type = "nitro"
        elif "steam" in content_lower:
            scam_type = "steam"
        elif any(word in content_lower for word in ["nude", "nudes", "teen", "teens"]):
            scam_type = "csam"

        # Update scam stats
        scam_stats = config["scam_stats"]
        scam_stats[scam_type] += 1
        self.global_scam_stats[scam_type] += 1
        await self.config.guild(message.guild).scam_stats.set(scam_stats)

        action = config["action"]
        embed = discord.Embed(
            title="Honeypot was triggered by a suspicious user",
            description=f">>> {message.content}",
            color=0xff4545,
            timestamp=message.created_at,
        )
        embed.add_field(name="User display name", value=message.author.display_name, inline=True)
        embed.add_field(name="User mention", value=message.author.mention, inline=True)
        embed.add_field(name="User ID", value=message.author.id, inline=True)

        failed = None
        if action:
            try:
                if action == "mute":
                    mute_role_id = config.get("mute_role")
                    mute_role = message.guild.get_role(mute_role_id) if mute_role_id else None
                    if mute_role:
                        await message.author.add_roles(mute_role, reason="User triggered honeypot defenses")
                    else:
                        failed = "**Failed:** The mute role is not set or doesn't exist anymore."
                elif action == "kick":
                    await message.author.kick(reason="User triggered honeypot defenses")
                elif action == "ban":
                    await message.author.ban(reason="User triggered honeypot defenses", delete_message_days=config["ban_delete_message_days"])
                elif action == "timeout":
                    timeout_duration = timedelta(days=7)  # 7 day timeout
                    # Since `timeout_for` is not available, we will use `edit` to set a timeout
                    await message.author.edit(timed_out_until=discord.utils.utcnow() + timeout_duration, reason="User triggered honeypot defenses")
            except discord.HTTPException as e:
                failed = f"**Failed:** An error occurred while trying to take action against the member:\n{e}"
            else:
                # Log the action (this is a placeholder for actual logging)
                print(f"Action {action} taken against {message.author}")

            action_result = {
                "mute": "I assigned the user the configured mute/suppress role",
                "kick": "The user was kicked from the server",
                "ban": "The user was banned from the server",
                "timeout": "The user was timed out for a week"
            }.get(action, "No action taken.")

            embed.add_field(name="Action taken", value=failed or action_result, inline=False)

        embed.set_footer(text=message.guild.name, icon_url=message.guild.icon.url)
        ping_role_id = config.get("ping_role")
        ping_role = message.guild.get_role(ping_role_id) if ping_role_id else None
        await logs_channel.send(content=ping_role.mention if ping_role else None, embed=embed)

    @commands.guild_only()
    @commands.admin_or_permissions()
    @commands.group()
    async def honeypot(self, ctx: commands.Context) -> None:
        """Honeypots are channels that attracted automated scam bots to detect and remove them from your server before they can hurt you or your members."""
        pass

    @commands.admin_or_permissions()
    @honeypot.command()
    async def create(self, ctx: commands.Context) -> None:
        """Create the honeypot channel."""
        async with ctx.typing():
            honeypot_channel_id = await self.config.guild(ctx.guild).honeypot_channel()
            honeypot_channel = ctx.guild.get_channel(honeypot_channel_id) if honeypot_channel_id else None

            if honeypot_channel:
                embed = discord.Embed(
                    title="Honeypot channel exists",
                    description=f"The honeypot channel already exists: {honeypot_channel.mention} ({honeypot_channel.id}).",
                    color=0xff4545
                )
                await ctx.send(embed=embed)
                return

            honeypot_channel = await ctx.guild.create_text_channel(
                name="honeypot",
                position=0,
                overwrites={
                    ctx.guild.me: discord.PermissionOverwrite(
                        view_channel=True,
                        read_messages=True,
                        send_messages=True,
                        manage_messages=True,
                        manage_channels=True,
                    ),
                    ctx.guild.default_role: discord.PermissionOverwrite(
                        view_channel=True, read_messages=True, send_messages=True
                    ),
                },
                reason=f"Honeypot channel creation requested by {ctx.author.display_name} ({ctx.author.id}).",
            )

            embed = discord.Embed(
                title="Shhhhh - this is a security honeypot",
                description="A honeypot is a security mechanism designed to lure cybercriminals into interacting with decoy targets. By doing so, cybersecurity experts can observe and analyze the attackers' methods, allowing them to develop effective countermeasures.\n\nSimilarly, this channel serves as a honeypot. It is intentionally placed in a conspicuous location with clear instructions not to engage in conversation here. Unsuspecting automated bots and low-quality spammers, such as those promoting nitro scams or explicit content, will likely post messages in this channel, unaware of its true purpose.",
                color=0xff4545,
            ).add_field(
                name="What do I do?",
                value="- **Do not speak in this channel**\n- **Do not send images in this channel**\n- **Do not send files in this channel**",
                inline=False,
            ).add_field(
                name="What will happen?",
                value="An action will be taken against you as decided by the server owner, which could be anything from a timeout, to an immediate ban.",
                inline=False,
            ).set_footer(text=ctx.guild.name, icon_url=ctx.guild.icon.url).set_image(url="attachment://do_not_post_here.png")

            await honeypot_channel.send(
                embed=embed,
                files=[discord.File(os.path.join(os.path.dirname(__file__), "do_not_post_here.png"))],
            )
            await self.config.guild(ctx.guild).honeypot_channel.set(honeypot_channel.id)
            embed = discord.Embed(
                title="Honeypot created",
                description=(
                    f"The honeypot has been created - {honeypot_channel.mention} ({honeypot_channel.id}).\n"
                    "Make sure to activate it after configuring a logging channel and punishment action\n- `honeypot activate`"
                ),
                color=0x2bbd8e
            )
            await ctx.send(embed=embed)

    @commands.admin_or_permissions()
    @honeypot.command()
    async def activate(self, ctx: commands.Context) -> None:
        """Enable the honeypot functionality."""
        async with ctx.typing():
            await self.config.guild(ctx.guild).enabled.set(True)
            embed = discord.Embed(
                title="Honeypot enabled",
                description="Honeypot functionality has been enabled.",
                color=0x2bbd8e
            )
            await ctx.send(embed=embed)

    @commands.admin_or_permissions()
    @honeypot.command()
    async def disable(self, ctx: commands.Context) -> None:
        """Disable the honeypot functionality."""
        async with ctx.typing():
            await self.config.guild(ctx.guild).enabled.set(False)
            embed = discord.Embed(
                title="Honeypot disabled",
                description="Honeypot functionality has been disabled.",
                color=0xff4545
            )
            await ctx.send(embed=embed)

    @commands.admin_or_permissions()
    @honeypot.command()
    async def remove(self, ctx: commands.Context) -> None:
        """Disable the honeypot and delete the honeypot channel."""
        async with ctx.typing():
            honeypot_channel_id = await self.config.guild(ctx.guild).honeypot_channel()
            honeypot_channel = ctx.guild.get_channel(honeypot_channel_id) if honeypot_channel_id else None

            if honeypot_channel:
                await honeypot_channel.delete(reason=f"Honeypot channel removal requested by {ctx.author.display_name} ({ctx.author.id}).")
                await self.config.guild(ctx.guild).honeypot_channel.set(None)
                embed = discord.Embed(
                    title="Honeypot channel removed",
                    description="Honeypot channel has been deleted and configuration cleared.",
                    color=0xff4545
                )
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(
                    title="No honeypot channel",
                    description="No honeypot channel to delete.",
                    color=0xff4545
                )
                await ctx.send(embed=embed)

            await self.config.guild(ctx.guild).enabled.set(False)

    @commands.admin_or_permissions()
    @honeypot.command()
    async def action(self, ctx: commands.Context, action: str) -> None:
        """Set the action to take when a user is detected in the honeypot channel."""
        async with ctx.typing():
            if action not in ["mute", "kick", "ban", "timeout"]:
                embed = discord.Embed(
                    title="Invalid action",
                    description="Invalid action. Please choose from: mute, kick, ban, timeout.",
                    color=0xff4545
                )
                await ctx.send(embed=embed)
                return
            await self.config.guild(ctx.guild).action.set(action)
            embed = discord.Embed(
                title="Action set",
                description=f"Action has been set to {action}.",
                color=0x2bbd8e
            )
            await ctx.send(embed=embed)

    @commands.admin_or_permissions()
    @honeypot.command()
    async def logs(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Set the channel where logs will be sent."""
        async with ctx.typing():
            await self.config.guild(ctx.guild).logs_channel.set(channel.id)
            embed = discord.Embed(
                title="Logs set",
                description=f"Logs channel has been set to {channel.mention}.",
                color=0x2bbd8e
            )
            await ctx.send(embed=embed)

    @commands.admin_or_permissions()
    @honeypot.command()
    async def settings(self, ctx: commands.Context) -> None:
        """View the current honeypot settings."""
        async with ctx.typing():
            config = await self.config.guild(ctx.guild).all()
            embed = discord.Embed(title="Current honeypot settings", color=0xfffffe)
            embed.add_field(name="Enabled", value=config["enabled"], inline=False)
            embed.add_field(name="Action", value=config["action"] or "Not set", inline=False)
            embed.add_field(name="Logs channel", value=f"<#{config['logs_channel']}>" if config["logs_channel"] else "Not set", inline=False)
            embed.add_field(name="Ping role", value=f"<@&{config['ping_role']}>" if config["ping_role"] else "Not set", inline=False)
            embed.add_field(name="Honeypot channel", value=f"<#{config['honeypot_channel']}>" if config["honeypot_channel"] else "Not set", inline=False)
            embed.add_field(name="Mute role", value=f"<@&{config['mute_role']}>" if config["mute_role"] else "Not set", inline=False)
            embed.add_field(name="Days to delete on ban", value=config["ban_delete_message_days"], inline=False)
            await ctx.send(embed=embed)

    @honeypot.command()
    async def stats(self, ctx: commands.Context) -> None:
        """View the current honeypot statistics."""
        async with ctx.typing():
            config = await self.config.guild(ctx.guild).all()
            embed = discord.Embed(title="Honeypot statistics", color=0xfffffe)
            embed.add_field(name="Server detections", value=f"Nitro: {config['scam_stats'].get('nitro', 0)}\nSteam: {config['scam_stats'].get('steam', 0)}\nCSAM: {config['scam_stats'].get('csam', 0)}\nOther: {config['scam_stats'].get('other', 0)}", inline=False)
            embed.add_field(name="Global detections", value=f"Nitro: {self.global_scam_stats.get('nitro', 0)}\nSteam: {self.global_scam_stats.get('steam', 0)}\nCSAM: {self.global_scam_stats.get('csam', 0)}\nOther: {self.global_scam_stats.get('other', 0)}", inline=False)
            await ctx.send(embed=embed)
