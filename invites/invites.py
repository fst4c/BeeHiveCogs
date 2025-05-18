import discord  # type: ignore
from redbot.core import commands, Config, checks  # type: ignore
import matplotlib.pyplot as plt  # type: ignore
import io
import asyncio
from datetime import datetime

class Invites(commands.Cog):
    """
    A comprehensive invite tracking cog for Red-DiscordBot.
    Tracks invites, provides leaderboards, rewards, announcements, and server growth charts.
    """

    DISBOARD_BOT_ID = 302050872383242240

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        default_guild = {
            "invites": {},  # {user_id: count}
            "rewards": {},  # {invite_count: role_id}
            "announcement_channel": None,
            "member_growth": [],  # [(iso_date, member_count)]
        }
        self.config.register_guild(**default_guild)
        self._cache = {}  # {guild_id: [Invite objects]}

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        # Remove user invite data for GDPR compliance
        for guild_id in await self.config.all_guilds():
            async with self.config.guild_from_id(guild_id).invites() as invites:
                invites.pop(str(user_id), None)

    @commands.Cog.listener()
    async def on_ready(self):
        # Cache invites for all guilds
        for guild in self.bot.guilds:
            try:
                self._cache[guild.id] = await guild.invites()
            except Exception:
                self._cache[guild.id] = []

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        try:
            self._cache[guild.id] = await guild.invites()
        except Exception:
            self._cache[guild.id] = []

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        # Update cache when new invite is created
        guild = invite.guild
        try:
            self._cache[guild.id] = await guild.invites()
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        # Update cache when invite is deleted
        guild = invite.guild
        try:
            self._cache[guild.id] = await guild.invites()
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        try:
            before = self._cache.get(guild.id, [])
            after = await guild.invites()
            self._cache[guild.id] = after

            used_invite = None
            for old in before:
                new = next((i for i in after if i.code == old.code), None)
                if new and new.uses > old.uses:
                    used_invite = new
                    break

            inviter = used_invite.inviter if used_invite else None

            # Ignore Disboard bot invites
            if inviter and inviter.id == self.DISBOARD_BOT_ID:
                return

            if inviter:
                await self._increment_invite(guild, inviter)
                await self._announce_invite(guild, member, inviter)
                await self._check_and_award_rewards(guild, inviter)

            # Track member growth
            async with self.config.guild(guild).member_growth() as growth:
                growth.append((member.joined_at.isoformat(), guild.member_count))

        except Exception as e:
            print(f"[Invites] Error processing member join in {guild.id}: {e}")

    async def _increment_invite(self, guild, inviter):
        async with self.config.guild(guild).invites() as invites:
            invites.setdefault(str(inviter.id), 0)
            invites[str(inviter.id)] += 1

    async def _announce_invite(self, guild, member, inviter):
        channel_id = await self.config.guild(guild).announcement_channel()
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not channel:
            return
        embed = discord.Embed(
            title="New Member Joined!",
            description=f"{member.mention} joined using {inviter.mention}'s invite.",
            color=0x2bbd8e
        )
        await channel.send(embed=embed)

    async def _check_and_award_rewards(self, guild, inviter):
        invites = await self.config.guild(guild).invites()
        rewards = await self.config.guild(guild).rewards()
        count = invites.get(str(inviter.id), 0)
        for threshold, role_id in rewards.items():
            try:
                if count == int(threshold):
                    role = guild.get_role(role_id)
                    if role and role not in inviter.roles:
                        await inviter.add_roles(role, reason="Invite reward")
                        await self._announce_reward(guild, inviter, role, count)
            except Exception:
                continue

    async def _announce_reward(self, guild, inviter, role, count):
        channel_id = await self.config.guild(guild).announcement_channel()
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not channel:
            return
        embed = discord.Embed(
            title="Invite Reward Earned!",
            description=f"{inviter.mention} has been awarded the {role.mention} role for reaching {count} invites!",
            color=discord.Color.gold()
        )
        await channel.send(embed=embed)

    @commands.group(name="invites", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin()
    async def invites_group(self, ctx):
        """Invite tracker settings and stats."""
        await ctx.send_help()

    @invites_group.command(name="announcechannel")
    async def set_announce_channel(self, ctx, channel: discord.TextChannel = None):
        """Set or clear the invite announcement channel."""
        if channel is None:
            await self.config.guild(ctx.guild).announcement_channel.clear()
            await ctx.send("Announcement channel cleared.")
        else:
            await self.config.guild(ctx.guild).announcement_channel.set(channel.id)
            await ctx.send(f"Announcement channel set to {channel.mention}.")

    @invites_group.command(name="addreward")
    async def add_reward(self, ctx, invite_count: int, role: discord.Role):
        """Add a reward for a specific number of invites."""
        async with self.config.guild(ctx.guild).rewards() as rewards:
            rewards[str(invite_count)] = role.id
        await ctx.send(f"Reward set: {role.mention} for {invite_count} invites.")

    @invites_group.command(name="removereward")
    async def remove_reward(self, ctx, invite_count: int):
        """Remove a reward for a specific number of invites."""
        async with self.config.guild(ctx.guild).rewards() as rewards:
            if str(invite_count) in rewards:
                del rewards[str(invite_count)]
                await ctx.send(f"Reward for {invite_count} invites removed.")
            else:
                await ctx.send("No such reward found.")

    @invites_group.command(name="rewards")
    async def list_rewards(self, ctx):
        """List all invite rewards."""
        rewards = await self.config.guild(ctx.guild).rewards()
        if not rewards:
            await ctx.send("No invite rewards set.")
            return
        lines = []
        for count, role_id in sorted(rewards.items(), key=lambda x: int(x[0])):
            role = ctx.guild.get_role(role_id)
            if role:
                lines.append(f"{count} invites: {role.mention}")
        await ctx.send("\n".join(lines) if lines else "No valid rewards found.")

    @invites_group.command(name="leaderboard")
    async def leaderboard(self, ctx):
        """Show the top inviters in this server."""
        invites = await self.config.guild(ctx.guild).invites()
        if not invites:
            await ctx.send("No invite data yet.")
            return
        sorted_invites = sorted(invites.items(), key=lambda x: x[1], reverse=True)
        desc = ""
        # Check for vanity and widget invites
        vanity_count = 0
        widget_count = 0

        # Try to get vanity and widget invite counts
        try:
            vanity_url = ctx.guild.vanity_url_code
            if vanity_url:
                # Discord API does not provide vanity invite uses directly, but we can get it from the widget
                # or from the guild.vanity_invite property if available
                if hasattr(ctx.guild, "vanity_invite") and ctx.guild.vanity_invite:
                    vanity_count = ctx.guild.vanity_invite.uses
                else:
                    # fallback: try to get from invites list
                    for inv in await ctx.guild.invites():
                        if inv.code == vanity_url:
                            vanity_count = inv.uses
                            break
        except Exception:
            pass

        try:
            if ctx.guild.widget_enabled:
                widget = await ctx.guild.widget()
                if widget and hasattr(widget, "approximate_member_count"):
                    # Widget invites don't have a use count, but we can try to estimate
                    # However, Discord does not provide widget invite use counts directly
                    # So we will just show WEB if widget is enabled
                    widget_count = -1  # -1 means enabled, but unknown count
        except Exception:
            pass

        for idx, (user_id, count) in enumerate(sorted_invites[:10], 1):
            member = ctx.guild.get_member(int(user_id))
            name = member.mention if member else f"<@{user_id}>"
            desc += f"**{idx}.** {name} — `{count}` invites\n"

        # Add vanity invite if present
        if vanity_count:
            desc += f"**`VANITY`** — `{vanity_count}` invites\n"
        elif ctx.guild.vanity_url_code:
            # If vanity exists but count is 0 or unknown, still show
            desc += f"**`VANITY`** — `0` invites\n"

        # Add widget invite if present
        if ctx.guild.widget_enabled:
            # Widget invites don't have a use count, so just show WEB
            desc += f"**`WEB`** — `?` invites\n"

        embed = discord.Embed(
            title="Invite Leaderboard",
            description=desc or "No data.",
            color=discord.Color.blurple()
        )
        await ctx.send(embed=embed)

    @invites_group.command(name="chart")
    async def chart(self, ctx):
        """Show a chart of server member growth."""
        growth = await self.config.guild(ctx.guild).member_growth()
        if not growth or len(growth) < 2:
            await ctx.send("Not enough data to plot member growth.")
            return

        # Summarize by day
        daily = {}
        for iso, count in growth:
            day = iso.split("T")[0]
            daily[day] = count
        days = sorted(daily.keys())
        counts = [daily[day] for day in days]

        plt.figure(figsize=(8, 4))
        plt.plot([datetime.strptime(d, "%Y-%m-%d") for d in days], counts, marker="o")
        plt.title("Server Member Growth")
        plt.xlabel("Date")
        plt.ylabel("Member Count")
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        plt.close()
        buf.seek(0)
        await ctx.send(file=discord.File(buf, filename="growth.png"))

    @invites_group.command(name="stats")
    async def stats(self, ctx, member: discord.Member = None):
        """Show invite stats for a member or the server."""
        invites = await self.config.guild(ctx.guild).invites()
        if member:
            count = invites.get(str(member.id), 0)
            await ctx.send(f"{member.mention} has invited `{count}` member(s).")
        else:
            total = sum(invites.values())
            await ctx.send(f"Total invites tracked: `{total}`.")

    @invites_group.command(name="profile")
    async def invites_profile(self, ctx, member: discord.Member = None):
        """
        Show a profile of a user's invite activity: number of invite links, invited members, and how many remain.
        """
        member = member or ctx.author
        # Get all invites for this guild
        try:
            invites = await ctx.guild.invites()
        except Exception:
            invites = []
        # Count how many invites this user has created
        user_invites = [i for i in invites if i.inviter and i.inviter.id == member.id]
        num_links = len(user_invites)
        total_uses = sum(i.uses for i in user_invites)
        # Get how many members this user has invited (from config)
        invites_data = await self.config.guild(ctx.guild).invites()
        invited_count = invites_data.get(str(member.id), 0)
        # Try to estimate how many of those invited are still in the server
        # This is a best effort: we don't track exactly who joined via whom, but we can estimate
        # by comparing the number of uses of their invites to the number of tracked invites
        # We'll use the tracked invites as the "invited" count, and estimate "still in server" as:
        #   - If tracked invites > 0, then for each member in the server, check if they joined via this inviter
        #   - But since we don't store that, we'll estimate: still_in = min(invited_count, total_uses)
        #   - left = invited_count - still_in (if total_uses < invited_count, assume all still in)
        still_in = min(invited_count, total_uses)
        left = invited_count - still_in if invited_count > total_uses else 0

        embed = discord.Embed(
            title=f"Invite Profile: {member.display_name}",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=member.display_avatar.url if hasattr(member, "display_avatar") else member.avatar_url)
        embed.add_field(name="Invite Links Created", value=str(num_links), inline=True)
        embed.add_field(name="Members Invited", value=str(invited_count), inline=True)
        embed.add_field(name="Members Still in Server", value=str(still_in), inline=True)
        embed.add_field(name="Members Left", value=str(left), inline=True)
        await ctx.send(embed=embed)

    @invites_group.command(name="raw")
    async def raw_invites(self, ctx):
        """Show raw invite codes and their uses (from Discord API)."""
        invites = await ctx.guild.invites()
        if not invites:
            await ctx.send("No invites found.")
            return
        lines = []
        for inv in invites:
            inviter = inv.inviter.mention if inv.inviter else "Unknown"
            lines.append(f"`{inv.code}` by {inviter}: {inv.uses} uses")
        await ctx.send("\n".join(lines[:20]) if lines else "No invites found.")

