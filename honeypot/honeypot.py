import discord
from redbot.core import commands
import typing
import os
from datetime import timedelta

class Honeypot(commands.Cog, name="Honeypot"):
    """Create a channel at the top of the server to attract self bots/scammers and notify/mute/kick/ban them immediately!"""

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot
        self.config = {
            "enabled": False,
            "action": None,
            "logs_channel": None,
            "ping_role": None,
            "honeypot_channel": None,
            "mute_role": None,
            "ban_delete_message_days": 3,
        }

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return

        config = self.config
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

        action = config["action"]
        embed = discord.Embed(
            title="Honeypot detected a suspicious user",
            description=f">>> {message.content}",
            color=discord.Color.red(),
            timestamp=message.created_at,
        ).set_author(
            name=f"{message.author.display_name} ({message.author.id})",
            icon_url=message.author.display_avatar.url,
        ).set_thumbnail(url=message.author.display_avatar.url)

        failed = None
        if action:
            try:
                if action == "mute":
                    mute_role_id = config.get("mute_role")
                    mute_role = message.guild.get_role(mute_role_id) if mute_role_id else None
                    if mute_role:
                        await message.author.add_roles(mute_role, reason="Self bot/scammer detected.")
                    else:
                        failed = "**Failed:** The mute role is not set or doesn't exist anymore."
                elif action == "kick":
                    await message.author.kick(reason="Self bot/scammer detected.")
                elif action == "ban":
                    await message.author.ban(reason="Self bot/scammer detected.", delete_message_days=config["ban_delete_message_days"])
                elif action == "timeout":
                    timeout_duration = timedelta(days=7)  # 7 day timeout
                    await message.author.timeout_for(timeout_duration, reason="Self bot/scammer detected.")
            except discord.HTTPException as e:
                failed = f"**Failed:** An error occurred while trying to take action against the member:\n{e}"
            else:
                # Log the action (this is a placeholder for actual logging)
                print(f"Action {action} taken against {message.author}")

            action_result = {
                "mute": "The member has been muted.",
                "kick": "The member has been kicked.",
                "ban": "The member has been banned.",
                "timeout": "The member has been timed out for 7 days."
            }.get(action, "No action taken.")

            embed.add_field(name="Action:", value=failed or action_result, inline=False)

        embed.set_footer(text=message.guild.name, icon_url=message.guild.icon.url)
        ping_role_id = config.get("ping_role")
        ping_role = message.guild.get_role(ping_role_id) if ping_role_id else None
        await logs_channel.send(content=ping_role.mention if ping_role else None, embed=embed)

    @commands.guild_only()
    @commands.admin_or_permissions()
    @commands.group()
    async def sethoneypot(self, ctx: commands.Context) -> None:
        """Set the honeypot settings. Only administrators can use this command for security reasons."""
        pass

    @commands.admin_or_permissions()
    @sethoneypot.command(aliases=["makechannel"])
    async def createchannel(self, ctx: commands.Context) -> None:
        """Create the honeypot channel."""
        honeypot_channel_id = self.config.get("honeypot_channel")
        honeypot_channel = ctx.guild.get_channel(honeypot_channel_id) if honeypot_channel_id else None

        if honeypot_channel:
            await ctx.send(f"The honeypot channel already exists: {honeypot_channel.mention} ({honeypot_channel.id}).")
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
            title="This channel is a security honeypot",
            description="A honeypot is a piece of security tooling used by cybersecurity experts to attract cybercriminals to fake targets. In the same way, this channel is a honeypot. Placed in an obvious place, the instruction is made clear not to speak in this channel. Automated and low quality bots will send messages in this channel, not knowing it is a honeypot, and they'll be automatically dealt with as a result.",
            color=0xff4545,
        ).add_field(
            name="What not to do?",
            value="- Do not speak in this channel\n- Do not send images in this channel\n- Do not send files in this channel\n-Invite others to this channel",
            inline=False,
        ).add_field(
            name="What will happen?",
            value="An action will be taken against you as decided by the server owner, which could be anything from a timeout, to an immediate ban.",
            inline=False,
        ).set_footer(text=ctx.guild.name, icon_url=ctx.guild.icon.url).set_image(url="attachment://do_not_post_here.png")

        await honeypot_channel.send(
            content="## ⚠️ WARNING ⚠️",
            embed=embed,
            files=[discord.File(os.path.join(os.path.dirname(__file__), "do_not_post_here.png"))],
        )
        self.config["honeypot_channel"] = honeypot_channel.id
        await ctx.send(
            f"The honeypot channel has been set to {honeypot_channel.mention} ({honeypot_channel.id}). You can now start attracting self bots/scammers!\n"
            "Please make sure to enable the cog and set the logs channel, the action to take, the role to ping (and the mute role) if you haven't already."
        )

    @commands.admin_or_permissions()
    @sethoneypot.command()
    async def enable(self, ctx: commands.Context) -> None:
        """Enable the honeypot functionality."""
        self.config["enabled"] = True
        await ctx.send("Honeypot functionality has been enabled.")

    @commands.admin_or_permissions()
    @sethoneypot.command()
    async def disable(self, ctx: commands.Context) -> None:
        """Disable the honeypot functionality."""
        self.config["enabled"] = False
        await ctx.send("Honeypot functionality has been disabled.")

    @commands.admin_or_permissions()
    @sethoneypot.command()
    async def setaction(self, ctx: commands.Context, action: str) -> None:
        """Set the action to take when a user is detected in the honeypot channel."""
        if action not in ["mute", "kick", "ban", "timeout"]:
            await ctx.send("Invalid action. Please choose from: mute, kick, ban, timeout.")
            return
        self.config["action"] = action
        await ctx.send(f"Action has been set to {action}.")

    @commands.admin_or_permissions()
    @sethoneypot.command()
    async def setlogchannel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Set the channel where logs will be sent."""
        self.config["logs_channel"] = channel.id
        await ctx.send(f"Logs channel has been set to {channel.mention}.")
