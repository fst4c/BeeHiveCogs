import contextlib
import datetime
import logging
from typing import List, Literal, Optional, Union

import discord
import humanize
from discord.utils import utcnow
from redbot.core import Config, commands, modlog
from redbot.core.bot import Red
from redbot.core.commands.converter import TimedeltaConverter

from .exceptions import TimeoutException

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]

log = logging.getLogger("red.beehive-cogs.timeout")

# Fix: define timeout as None at module level to avoid NameError in cog_unload
timeout = None

class Timeout(commands.Cog):
    """
    Manage Timeouts.
    """

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=190, force_registration=True)
        default_guild = {"dm": True, "showmod": False, "role_enabled": False}
        self.config.register_guild(**default_guild)

    __author__ = ["adminelevation"]
    __version__ = "1.0.0"

    def format_help_for_context(self, ctx: commands.Context) -> str:
        """
        Thanks Sinbad!
        """
        pre_processed = super().format_help_for_context(ctx)
        return f"{pre_processed}\n\nAuthors: {', '.join(self.__author__)}\nCog Version: {self.__version__}"

    async def red_delete_data_for_user(
        self, *, requester: RequestType, user_id: int
    ) -> None:
        # TODO: Replace this with the proper end user data removal handling.
        await super().red_delete_data_for_user(requester=requester, user_id=user_id)

    async def pre_load(self):
        with contextlib.suppress(RuntimeError):
            await modlog.register_casetype(
                name="timeout",
                default_setting=True,
                image=":mute:",
                case_str="Timeout",
            )
            await modlog.register_casetype(
                name="untimeout",
                default_setting=True,
                image=":sound:",
                case_str="Untimeout",
            )

    async def timeout_user(
        self,
        ctx: commands.Context,
        member: discord.Member,
        time: Optional[datetime.timedelta],
        reason: Optional[str] = None,
    ) -> None:
        # Fix: Check if member is already timed out if time is not None
        if time and member.is_timed_out():
            raise TimeoutException("User is already timed out.")
        await member.timeout(time, reason=reason)
        await modlog.create_case(
            bot=ctx.bot,
            guild=ctx.guild,
            created_at=utcnow(),
            action_type="timeout" if time else "untimeout",
            user=member,
            moderator=ctx.author,
            reason=reason,
            until=(utcnow() + time) if time else None,
            channel=ctx.channel,
        )
        if await self.config.guild(member.guild).dm():
            with contextlib.suppress(discord.HTTPException):
                embed = discord.Embed(
                    title="Server timeout" if time else "Server untimeout",
                    description=(
                        f"**Reason:** {reason}"
                        if reason
                        else "**Reason:** No reason given."
                    ),
                    timestamp=utcnow(),
                    colour=await ctx.embed_colour(),
                )

                if time:
                    timestamp_val = utcnow() + time
                    timestamp_int = int(timestamp_val.timestamp())
                    embed.add_field(
                        name="Until", value=f"<t:{timestamp_int}:f>", inline=True
                    )
                    embed.add_field(
                        name="Duration", value=humanize.naturaldelta(time), inline=True
                    )
                embed.add_field(name="Server", value=str(ctx.guild), inline=False)
                if await self.config.guild(ctx.guild).showmod():
                    embed.add_field(name="Moderator", value=str(ctx.author), inline=False)
                await member.send(embed=embed)

    async def timeout_role(
        self,
        ctx: commands.Context,
        role: discord.Role,
        time: datetime.timedelta,
        reason: Optional[str] = None,
    ) -> List[discord.Member]:
        failed = []
        members = list(role.members)
        for member in members:
            try:
                if (
                    member.is_timed_out()
                    or not await is_allowed_by_hierarchy(ctx.bot, ctx.author, member)
                    or ctx.channel.permissions_for(member).administrator
                ):
                    raise TimeoutException
                await self.timeout_user(ctx, member, time, reason)
            except (discord.HTTPException, TimeoutException):
                failed.append(member)
        return failed

    @commands.command(aliases=["tt"])
    @commands.guild_only()
    @commands.cooldown(1, 1, commands.BucketType.user)
    @commands.bot_has_permissions(moderate_members=True)
    @commands.mod_or_permissions(moderate_members=True)
    async def timeout(
        self,
        ctx: commands.Context,
        member_or_role: Union[discord.Member, discord.Role],
        time: TimedeltaConverter(
            minimum=datetime.timedelta(minutes=1),
            maximum=datetime.timedelta(days=28),
            default_unit="minutes",
            allowed_units=["minutes", "seconds", "hours", "days", "weeks"],
        ) = None,
        *,
        reason: Optional[str] = None,
    ):
        """
        Timeout users.

        `<member_or_role>` is the username/rolename, ID or mention. If provided a role,
        everyone with that role will be timedout.
        `[time]` is the time to mute for. Time is any valid time length such as `45 minutes`
        or `3 days`. If nothing is provided the timeout will be 60 seconds default.
        `[reason]` is the reason for the timeout. Defaults to `None` if nothing is provided.

        Examples:
        `[p]timeout @member 5m talks too much`
        `[p]timeout @member 10m`

        """
        if not time:
            time = datetime.timedelta(seconds=60)
        timestamp_val = utcnow() + time
        timestamp = int(timestamp_val.timestamp())
        if isinstance(member_or_role, discord.Member):
            if member_or_role.is_timed_out():
                embed = discord.Embed(
                    title="Timeout Failed",
                    description="This user is already timed out.",
                    colour=discord.Colour.red(),
                    timestamp=utcnow(),
                )
                return await ctx.send(embed=embed)
            if not await is_allowed_by_hierarchy(ctx.bot, ctx.author, member_or_role):
                embed = discord.Embed(
                    title="Timeout Failed",
                    description="You cannot timeout this user due to hierarchy.",
                    colour=discord.Colour.red(),
                    timestamp=utcnow(),
                )
                return await ctx.send(embed=embed)
            if ctx.channel.permissions_for(member_or_role).administrator:
                embed = discord.Embed(
                    title="Timeout Failed",
                    description="You can't timeout an administrator.",
                    colour=discord.Colour.red(),
                    timestamp=utcnow(),
                )
                return await ctx.send(embed=embed)
            try:
                await self.timeout_user(ctx, member_or_role, time, reason)
            except TimeoutException:
                embed = discord.Embed(
                    title="Timeout Failed",
                    description="This user is already timed out.",
                    colour=discord.Colour.red(),
                    timestamp=utcnow(),
                )
                return await ctx.send(embed=embed)
            embed = discord.Embed(
                title="User timed out",
                description=f"{member_or_role.mention} has been timed out.",
                colour=discord.Colour.orange(),
                timestamp=utcnow(),
            )
            embed.add_field(name="Until", value=f"<t:{timestamp}:f>", inline=True)
            embed.add_field(name="Duration", value=humanize.naturaldelta(time), inline=True)
            if reason:
                embed.add_field(name="Reason", value=reason, inline=False)
            await ctx.send(embed=embed)
            return
        if isinstance(member_or_role, discord.Role):
            enabled = await self.config.guild(ctx.guild).role_enabled()
            if not enabled:
                embed = discord.Embed(
                    title="Timeout Failed",
                    description="Role (un)timeouts are not enabled.",
                    colour=discord.Colour.red(),
                    timestamp=utcnow(),
                )
                return await ctx.send(embed=embed)
            embed = discord.Embed(
                title="Role Timeout",
                description=f"Timing out {len(member_or_role.members)} members till <t:{timestamp}:f>.",
                colour=discord.Colour.orange(),
                timestamp=utcnow(),
            )
            await ctx.send(embed=embed)
            failed = await self.timeout_role(ctx, member_or_role, time, reason)
            if failed:
                embed = discord.Embed(
                    title="Timeout Failed",
                    description=f"Failed to timeout {len(failed)} members.",
                    colour=discord.Colour.red(),
                    timestamp=utcnow(),
                )
                await ctx.send(embed=embed)

    @commands.command(aliases=["utt"])
    @commands.guild_only()
    @commands.cooldown(1, 1, commands.BucketType.user)
    @commands.bot_has_permissions(moderate_members=True)
    @commands.mod_or_permissions(moderate_members=True)
    async def untimeout(
        self,
        ctx: commands.Context,
        member_or_role: Union[discord.Member, discord.Role],
        *,
        reason: Optional[str] = None,
    ):
        """
        Untimeout users.

        `<member_or_role>` is the username/rolename, ID or mention. If
        provided a role, everyone with that role will be untimed.
        `[reason]` is the reason for the untimeout. Defaults to `None`
        if nothing is provided.

        """
        if isinstance(member_or_role, discord.Member):
            if not member_or_role.is_timed_out():
                embed = discord.Embed(
                    title="User wasn't timed out",
                    description="Are you sure you ran this command on the right user?",
                    colour=0xff4545,
                    timestamp=utcnow(),
                )
                return await ctx.send(embed=embed)
            await self.timeout_user(ctx, member_or_role, None, reason)
            embed = discord.Embed(
                title="Timeout lifted",
                description=f"Removed timeout from {member_or_role.mention}, they should be able to speak now.",
                colour=0x2bbd8e,
                timestamp=utcnow(),
            )
            if reason:
                embed.add_field(name="Reason", value=reason, inline=False)
            await ctx.send(embed=embed)
            return
        if isinstance(member_or_role, discord.Role):
            enabled = await self.config.guild(ctx.guild).role_enabled()
            if not enabled:
                embed = discord.Embed(
                    title="Untimeout Failed",
                    description="Role (un)timeouts are not enabled.",
                    colour=discord.Colour.red(),
                    timestamp=utcnow(),
                )
                return await ctx.send(embed=embed)
            embed = discord.Embed(
                title="Role Untimeout",
                description=f"Removing timeout from {len(member_or_role.members)} members.",
                colour=discord.Colour.green(),
                timestamp=utcnow(),
            )
            await ctx.send(embed=embed)
            members = list(member_or_role.members)
            untimed_count = 0
            for member in members:
                if member.is_timed_out():
                    await self.timeout_user(ctx, member, None, reason)
                    untimed_count += 1
            embed = discord.Embed(
                title="Role Untimeout Complete",
                description=f"Removed timeout from {untimed_count} members.",
                colour=discord.Colour.green(),
                timestamp=utcnow(),
            )
            await ctx.send(embed=embed)

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def timeoutset(self, ctx: commands.Context):
        """Manage timeout settings."""

    @timeoutset.command(name="showmoderator", aliases=["showmod"])
    async def timeoutset_showmoderator(self, ctx: commands.Context):
        """Change whether to show moderator on DM's or not."""
        current = await self.config.guild(ctx.guild).showmod()
        await self.config.guild(ctx.guild).showmod.set(not current)
        w = "Will not" if current else "Will"
        embed = discord.Embed(
            title="Timeout Setting Changed",
            description=f"I {w} show the moderator in timeout DM's.",
            colour=discord.Colour.blue(),
            timestamp=utcnow(),
        )
        await ctx.send(embed=embed)

    @timeoutset.command(name="dm")
    async def timeoutset_dm(self, ctx: commands.Context):
        """Change whether to DM the user when they are timed out."""
        current = await self.config.guild(ctx.guild).dm()
        await self.config.guild(ctx.guild).dm.set(not current)
        w = "Will not" if current else "Will"
        embed = discord.Embed(
            title="Timeout Setting Changed",
            description=f"I {w} DM the user when they are timed out.",
            colour=discord.Colour.blue(),
            timestamp=utcnow(),
        )
        await ctx.send(embed=embed)

    @timeoutset.command(name="role")
    async def timeoutset_role(self, ctx: commands.Context):
        """Change whether to timeout role or not."""
        current = await self.config.guild(ctx.guild).role_enabled()
        await self.config.guild(ctx.guild).role_enabled.set(not current)
        w = "Will not" if current else "Will"
        embed = discord.Embed(
            title="Timeout Setting Changed",
            description=f"I {w} timeout role.",
            colour=discord.Colour.blue(),
            timestamp=utcnow(),
        )
        await ctx.send(embed=embed)

    async def cog_unload(self) -> None:
        global timeout
        # Fix: Only try to remove/add command if timeout is not None
        if timeout is not None:
            try:
                self.bot.remove_command("timeout")
            except Exception as e:
                log.info(e)
            self.bot.add_command(timeout)



async def is_allowed_by_hierarchy(
    bot: Red, user: discord.Member, member: discord.Member
) -> bool:
    # Fix: Check if user and member are in the same guild
    if user.guild != member.guild:
        return False
    return (
        user.guild.owner_id == user.id
        or user.top_role > member.top_role
        or await bot.is_owner(user)
    )


async def setup(bot: Red) -> None:
    global timeout
    timeout = bot.remove_command("timeout")
    cog = Timeout(bot)
    await cog.pre_load()
    await bot.add_cog(cog)