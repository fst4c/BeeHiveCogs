import discord
from redbot.core import commands, Config, checks, bank
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate
import time
import aiohttp
import asyncio
import re
from collections import defaultdict

CUSTOMER_ROLE_ID = 1364590138847400008  # The role to grant/revoke based on customer_id

# Default prices for each command (USD, as string for formatting)
DEFAULT_PRICES = {
    "ask": "$0.10/ask",
    "answer": "$0.15/answer",
    "explain": "$0.20/explanation",
}

# Stripe price IDs to subscribe new customers to
STRIPE_PRICE_IDS = [
    "price_1RH6qnRM8UTRBxZH0ynsEnQU",
    "price_1RH7u6RM8UTRBxZHtWar1C2Z",
    "price_1RH7unRM8UTRBxZHU80WXA3e",
]

INVITE_CREDIT_AMOUNT = 100  # $1.00 in Stripe's "cents" (100 = $1.00)
INVITE_CREDIT_PER = 10      # Every 10 invites = $1.00

class HomeworkAI(commands.Cog):
    """
    Use AI to get your homework done. It doesn't get any lazier than this, really.
    """

    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=77777777777, force_registration=True)
        default_user = {
            "customer_id": None,
            "applied": False,
            "phone_number": None,
            "phone_verified": False,
            "denied": False,  # Track if the user was denied
            "invited_users": [],  # List of user IDs this user has invited
            "invite_credits_granted": 0,  # Number of $1 credits already granted
        }
        default_guild = {
            "applications_channel": None,
            "pricing_channel": None,
            "prices": DEFAULT_PRICES.copy(),
            "pricing_message_id": None,
            "stats_channel": None,  # Channel for stats reporting
            "stats": {
                "ask": 0,
                "answer": 0,
                "explain": 0,
                "upvotes": 0,
                "downvotes": 0,
            },
            "stats_message_id": None,
        }
        self.config.register_user(**default_user)
        self.config.register_guild(**default_guild)
        self.billing_portal_url = "https://billing.stripe.com/p/login/6oE4h4dqVe3zbtefYY"
        self.bot.loop.create_task(self._maybe_update_all_pricing_channels())
        self.bot.loop.create_task(self._periodic_stats_update())  # Start periodic stats update

        # Invite tracking cache: {guild_id: {user_id: inviter_id}}
        self._invite_cache = defaultdict(dict)
        self._invite_code_cache = defaultdict(dict)  # {guild_id: {invite_code: uses}}

        self.bot.loop.create_task(self._initialize_invite_tracking())

        # --- Custom Status Cycling ---
        self._status_cycle_task = self.bot.loop.create_task(self._cycle_status())
        self._status_cycle_index = 0

    async def _cycle_status(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                # Gather pricing info for status
                pricing_status = None
                for guild in self.bot.guilds:
                    prices = await self.config.guild(guild).prices()
                    if prices:
                        pricing_status = " | ".join(f"{cmd}: {price}" for cmd, price in prices.items())
                        break
                if not pricing_status:
                    pricing_status = "ask $0.10 | answer $0.15 | explain $0.20"

                # Gather stats info for status
                stats_status = None
                for guild in self.bot.guilds:
                    stats = await self.config.guild(guild).stats()
                    if stats:
                        stats_status = f"Ask: {stats.get('ask',0)} | Ans: {stats.get('answer',0)} | Exp: {stats.get('explain',0)}"
                        break
                if not stats_status:
                    stats_status = "Ask: 0 | Ans: 0 | Exp: 0"

                # Commands info for status
                commands_status = "Try /ask, /answer, /explain"

                # Cycle through the three statuses using ActivityType.custom
                status_list = [
                    (discord.ActivityType.custom, f"💲 {pricing_status}"),
                    (discord.ActivityType.custom, f"📊 {stats_status}"),
                    (discord.ActivityType.custom, f"🤖 {commands_status}"),
                ]
                activity_type, status_text = status_list[self._status_cycle_index % len(status_list)]
                # discord.ActivityType.custom requires the 'name' kwarg to be the status text
                await self.bot.change_presence(activity=discord.Activity(type=activity_type, name=status_text))
                self._status_cycle_index += 1
            except Exception:
                pass
            await asyncio.sleep(30)  # Change status every 30 seconds

    async def _initialize_invite_tracking(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self._cache_guild_invites(guild)

    async def _cache_guild_invites(self, guild):
        try:
            invites = await guild.invites()
            self._invite_code_cache[guild.id] = {invite.code: invite.uses for invite in invites}
        except Exception:
            self._invite_code_cache[guild.id] = {}

    async def cog_load(self):
        # Called when the cog is loaded/reloaded
        await self._maybe_update_all_pricing_channels()
        await self._maybe_update_all_stats_channels()
        self.bot.loop.create_task(self._periodic_stats_update())  # Ensure periodic stats update on reload
        self.bot.loop.create_task(self._initialize_invite_tracking())
        # Restart status cycling on reload
        if hasattr(self, "_status_cycle_task"):
            self._status_cycle_task.cancel()
        self._status_cycle_task = self.bot.loop.create_task(self._cycle_status())
        self._status_cycle_index = 0

    async def _maybe_update_all_pricing_channels(self):
        # Wait for bot to be ready
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                await self._update_pricing_channel(guild)
            except Exception:
                pass

    async def _maybe_update_all_stats_channels(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                await self._update_stats_channel(guild)
            except Exception:
                pass

    async def _periodic_stats_update(self):
        await self.bot.wait_until_ready()
        while True:
            for guild in self.bot.guilds:
                try:
                    await self._update_stats_channel(guild)
                except Exception:
                    pass
            await asyncio.sleep(120)  # Update every 120 seconds

    async def _update_pricing_channel(self, guild):
        # Get pricing channel and prices for this guild
        channel_id = await self.config.guild(guild).pricing_channel()
        prices = await self.config.guild(guild).prices()
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        # Compose the pricing message
        embed = discord.Embed(
            title="HomeworkAI pricing",
            description="Here are the current prices for each HomeworkAI feature. HomeworkAI is charged by usage, meaning you only pay for how much you use.",
            color=discord.Color.blurple()
        )
        for cmd, price in prices.items():
            if cmd == "ask":
                label = "Ask (best for General Questions)"
            elif cmd == "answer":
                label = "Answer (best for Multiple Choice and Comparison)"
            elif cmd == "explain":
                label = "Explain (best for Step-by-Step work)"
            else:
                label = cmd.capitalize()
            embed.add_field(name=label, value=price, inline=False)
        embed.set_footer(text="Prices are per command use and may change with notice.\nInvite friends! For every 10 users you invite, you get $1 promotional credit.")

        # Try to edit the previous pricing message if it exists, else send a new one
        msg_id = await self.config.guild(guild).pricing_message_id()
        msg = None
        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
            except Exception:
                msg = None
        if msg:
            try:
                await msg.edit(embed=embed)
            except Exception:
                # If can't edit, send new
                msg = await channel.send(embed=embed)
                await self.config.guild(guild).pricing_message_id.set(msg.id)
        else:
            msg = await channel.send(embed=embed)
            await self.config.guild(guild).pricing_message_id.set(msg.id)

    async def _update_stats_channel(self, guild):
        channel_id = await self.config.guild(guild).stats_channel()
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        stats = await self.config.guild(guild).stats()
        msg_id = await self.config.guild(guild).stats_message_id()
        embed = discord.Embed(
            title="HomeworkAI Usage & Ratings",
            color=discord.Color.blurple(),
            description="Statistics for HomeworkAI usage and answer ratings in this server."
        )
        embed.add_field(name="Ask Uses", value=str(stats.get("ask", 0)), inline=True)
        embed.add_field(name="Answer Uses", value=str(stats.get("answer", 0)), inline=True)
        embed.add_field(name="Explain Uses", value=str(stats.get("explain", 0)), inline=True)
        embed.add_field(name="👍 Upvotes", value=str(stats.get("upvotes", 0)), inline=True)
        embed.add_field(name="👎 Downvotes", value=str(stats.get("downvotes", 0)), inline=True)
        embed.set_footer(text="Stats update live as users interact with HomeworkAI.\nInvite friends! For every 10 users you invite, you get $1 promotional credit.")

        msg = None
        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
            except Exception:
                msg = None
        if msg:
            try:
                await msg.edit(embed=embed)
            except Exception:
                msg = await channel.send(embed=embed)
                await self.config.guild(guild).stats_message_id.set(msg.id)
        else:
            msg = await channel.send(embed=embed)
            await self.config.guild(guild).stats_message_id.set(msg.id)

    async def get_openai_key(self):
        tokens = await self.bot.get_shared_api_tokens("openai")
        return tokens.get("api_key")

    async def get_stripe_key(self):
        tokens = await self.bot.get_shared_api_tokens("stripe")
        return tokens.get("api_key")

    async def get_twilio_keys(self):
        tokens = await self.bot.get_shared_api_tokens("twilio")
        return (
            tokens.get("account_sid"),
            tokens.get("auth_token"),
        )

    async def cog_check(self, ctx):
        return True

    # --- Invite Tracking and Credit Granting ---

    async def _grant_invite_credits(self, inviter_id: int):
        """
        Check if the inviter is eligible for new invite credits and grant them if so.
        """
        inviter_conf = self.config.user_from_id(inviter_id)
        invited_users = await inviter_conf.invited_users()
        credits_granted = await inviter_conf.invite_credits_granted()
        # Only count users who have a customer_id (i.e., completed onboarding)
        count = 0
        for uid in invited_users:
            customer_id = await self.config.user_from_id(uid).customer_id()
            if customer_id:
                count += 1
        eligible_credits = count // INVITE_CREDIT_PER
        to_grant = eligible_credits - credits_granted
        if to_grant > 0:
            # Grant $1.00 Stripe credit per eligible batch using /v1/billing/credit_grants
            stripe_key = await self.get_stripe_key()
            inviter_customer_id = await inviter_conf.customer_id()
            if stripe_key and inviter_customer_id:
                for _ in range(to_grant):
                    try:
                        async with aiohttp.ClientSession() as session:
                            headers = {
                                "Authorization": f"Bearer {stripe_key}",
                                "Content-Type": "application/x-www-form-urlencoded"
                            }
                            data = {
                                "name": "Invite reward",
                                "customer": inviter_customer_id,
                                "amount[monetary][currency]": "usd",
                                "amount[monetary][value]": str(INVITE_CREDIT_AMOUNT),
                                "amount[type]": "monetary",
                                "applicability_config[scope][price_type]": "metered",
                                "category": "promotional"
                            }
                            async with session.post(
                                "https://api.stripe.com/v1/billing/credit_grants",
                                data=data,
                                headers=headers,
                                timeout=aiohttp.ClientTimeout(total=30)
                            ) as resp:
                                # Optionally, you could log or handle errors here
                                pass
                    except Exception:
                        pass
                await inviter_conf.invite_credits_granted.set(credits_granted + to_grant)
                # Optionally, notify the user
                try:
                    user = self.bot.get_user(inviter_id)
                    if user:
                        await user.send(
                            f"🎉 You've earned ${to_grant}.00 in promotional credit for inviting new users to HomeworkAI! Thank you for spreading the word, keep it up and you'll keep earning credit."
                        )
                except Exception:
                    pass

    async def _find_inviter(self, member: discord.Member):
        """
        Try to find the inviter for a new member using invite code tracking.
        Returns inviter_id or None.
        """
        guild = member.guild
        try:
            before_invites = self._invite_code_cache[guild.id]
            after_invites = {}
            invites = await guild.invites()
            for invite in invites:
                after_invites[invite.code] = invite.uses
            self._invite_code_cache[guild.id] = after_invites
            used_code = None
            for code, uses in after_invites.items():
                if code in before_invites and uses > before_invites[code]:
                    used_code = code
                    break
                elif code not in before_invites and uses > 0:
                    used_code = code
                    break
            if used_code:
                for invite in invites:
                    if invite.code == used_code and invite.inviter:
                        return invite.inviter.id
        except Exception:
            pass
        return None

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """
        Track invites and record who invited whom.
        """
        inviter_id = await self._find_inviter(member)
        if inviter_id:
            # Save the invited user to the inviter's invited_users list
            inviter_conf = self.config.user_from_id(inviter_id)
            invited_users = await inviter_conf.invited_users()
            if member.id not in invited_users:
                invited_users.append(member.id)
                await inviter_conf.invited_users.set(invited_users)
            # Optionally, notify the inviter
            try:
                user = self.bot.get_user(inviter_id)
                if user:
                    await user.send(
                        f"🎉 You invited {member.mention} to HomeworkAI! If they complete onboarding, you'll get credit toward a $1.00 Stripe reward."
                    )
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """
        When a member's roles or status change, check if they have just become a customer (i.e., completed onboarding).
        If so, check if they were invited and grant invite credits if eligible.
        """
        # Only act if the user just got the customer role
        before_roles = set(before.roles)
        after_roles = set(after.roles)
        customer_role = after.guild.get_role(CUSTOMER_ROLE_ID)
        if customer_role and customer_role not in before_roles and customer_role in after_roles:
            # See if this user was invited by someone
            for inviter_id, inviteds in self._invite_cache.get(after.guild.id, {}).items():
                if after.id in inviteds:
                    await self._grant_invite_credits(inviter_id)
                    break

    # --- ADMIN/CONFIG/MANAGEMENT COMMAND GROUP ---
    @commands.group()
    @commands.guild_only()
    async def homeworkai(self, ctx):
        """HomeworkAI configuration commands."""

    @commands.group(name="homeworkaiset", invoke_without_command=True)
    @commands.guild_only()
    async def homeworkaiset(self, ctx):
        """HomeworkAI admin/configuration/management commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @homeworkaiset.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def setapplications(self, ctx, channel: discord.TextChannel):
        """Set the channel where HomeworkAI applications are sent."""
        await self.config.guild(ctx.guild).applications_channel.set(channel.id)
        embed = discord.Embed(
            title="Applications Channel Set",
            description=f"Applications channel set to {channel.mention}.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @homeworkaiset.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def setpricing(self, ctx, channel: discord.TextChannel):
        """Set the channel where HomeworkAI pricing is displayed and update the pricing message."""
        await self.config.guild(ctx.guild).pricing_channel.set(channel.id)
        await self._update_pricing_channel(ctx.guild)
        embed = discord.Embed(
            title="Pricing Channel Set",
            description=f"Pricing channel set to {channel.mention}. The pricing message has been updated.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @homeworkaiset.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def setstats(self, ctx, channel: discord.TextChannel):
        """Set the channel where HomeworkAI usage and rating statistics are displayed and update the stats message."""
        await self.config.guild(ctx.guild).stats_channel.set(channel.id)
        await self._update_stats_channel(ctx.guild)
        embed = discord.Embed(
            title="Stats Channel Set",
            description=f"Stats channel set to {channel.mention}. The stats message has been updated.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @homeworkaiset.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def setprice(self, ctx, command: str, price: str):
        """
        Set the price for a HomeworkAI command (ask, answer, explain).
        Example: [p]homeworkaiset setprice ask $0.12
        """
        command = command.lower()
        if command not in DEFAULT_PRICES:
            await ctx.send(f"Invalid command. Valid options: {', '.join(DEFAULT_PRICES.keys())}")
            return
        prices = await self.config.guild(ctx.guild).prices()
        prices[command] = price
        await self.config.guild(ctx.guild).prices.set(prices)
        await self._update_pricing_channel(ctx.guild)
        embed = discord.Embed(
            title="Price Updated",
            description=f"Price for `{command}` set to `{price}`. Pricing message updated.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @homeworkaiset.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def resetprices(self, ctx):
        """Reset all HomeworkAI command prices to their defaults and update the pricing message."""
        await self.config.guild(ctx.guild).prices.set(DEFAULT_PRICES.copy())
        await self._update_pricing_channel(ctx.guild)
        embed = discord.Embed(
            title="Prices Reset",
            description="All HomeworkAI command prices have been reset to their defaults. Pricing message updated.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @homeworkaiset.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def resetstats(self, ctx):
        """Reset all HomeworkAI usage and rating statistics for this server."""
        await self.config.guild(ctx.guild).stats.set({
            "ask": 0,
            "answer": 0,
            "explain": 0,
            "upvotes": 0,
            "downvotes": 0,
        })
        await self._update_stats_channel(ctx.guild)
        embed = discord.Embed(
            title="Stats Reset",
            description="All HomeworkAI usage and rating statistics have been reset for this server.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @homeworkaiset.command()
    @commands.is_owner()
    async def setcid(self, ctx, user: discord.User, customer_id: str):
        """
        Set a user's customer ID (admin/owner only).
        """
        prev_id = await self.config.user(user).customer_id()
        await self.config.user(user).customer_id.set(customer_id)
        # If setting a customer_id, clear denied flag
        if customer_id:
            await self.config.user(user).denied.set(False)

        # Role management: Add or remove the customer role in all mutual guilds
        for guild in self.bot.guilds:
            member = guild.get_member(user.id)
            if not member:
                continue
            role = guild.get_role(CUSTOMER_ROLE_ID)
            if not role:
                continue
            try:
                if customer_id:
                    if role not in member.roles:
                        await member.add_roles(role, reason="Granted HomeworkAI customer role (setcustomerid)")
                else:
                    if role in member.roles:
                        await member.remove_roles(role, reason="Removed HomeworkAI customer role (setcustomerid)")
            except Exception:
                pass

        if not prev_id and customer_id:
            try:
                embed = discord.Embed(
                    title="Welcome to HomeworkAI!",
                    description=(
                        "You now have access to HomeworkAI.\n\n"
                        "**How to use:**\n"
                        "- Use `/ask` to get answers to general questions (text or image supported).\n"
                        "- Use `/answer` for multiple choice or comparison questions (text or image supported).\n"
                        "- Use `/explain` to get step-by-step explanations for your homework problems.\n\n"
                        "All answers are sent to you in DMs for privacy.\n\n"
                        f"To manage your billing or connect your payment method, visit our [billing portal]({self.billing_portal_url})"
                    ),
                    color=discord.Color.green()
                )
                await user.send(embed=embed)
            except Exception:
                pass
        embed = discord.Embed(
            title="Customer ID Set",
            description=f"Customer ID for {user.mention} set to `{customer_id}`.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @homeworkaiset.command(name="removecustomerid")
    @commands.is_owner()
    async def delcid(self, ctx, user: discord.User):
        """
        Remove a user's customer ID and revoke the customer role (admin/owner only).
        """
        prev_id = await self.config.user(user).customer_id()
        await self.config.user(user).customer_id.set(None)

        # Remove the customer role in all mutual guilds
        for guild in self.bot.guilds:
            member = guild.get_member(user.id)
            if not member:
                continue
            role = guild.get_role(CUSTOMER_ROLE_ID)
            if not role:
                continue
            try:
                if role in member.roles:
                    await member.remove_roles(role, reason="Removed HomeworkAI customer role (removecustomerid)")
            except Exception:
                pass

        embed = discord.Embed(
            title="Customer ID Removed",
            description=f"Customer ID for {user.mention} has been removed and the customer role revoked.",
            color=discord.Color.orange()
        )
        await ctx.send(embed=embed)

    @homeworkaiset.command(name="resetcogdata")
    @commands.is_owner()
    async def resetmodule(self, ctx):
        """
        **OWNER ONLY**: Reset all HomeworkAI cog data (users and guilds).
        This will erase all stored customer IDs, applications, and configuration.
        """
        confirm_message = await ctx.send(
            embed=discord.Embed(
                title="Reset HomeworkAI Data",
                description="⚠️ **Are you sure you want to reset all HomeworkAI cog data?**\n"
                            "This will erase all stored customer IDs, applications, and configuration for all users and guilds.\n\n"
                            "Type `CONFIRM RESET` within 30 seconds to proceed.",
                color=discord.Color.red()
            )
        )

        def check(m):
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=30)
        except asyncio.TimeoutError:
            await ctx.send("Reset cancelled: confirmation timed out.")
            return

        if msg.content.strip() != "CONFIRM RESET":
            await ctx.send("Reset cancelled: incorrect confirmation phrase.")
            return

        try:
            await self.config.clear_all()
            await ctx.send(
                embed=discord.Embed(
                    title="HomeworkAI Data Reset",
                    description="All HomeworkAI cog data has been erased.",
                    color=discord.Color.green()
                )
            )
        except Exception as e:
            await ctx.send(
                embed=discord.Embed(
                    title="Reset Failed",
                    description=f"An error occurred while resetting data: {e}",
                    color=discord.Color.red()
                )
            )

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command: discord.app_commands.Command):
        # This is a placeholder in case you want to handle app command completions
        pass

    # --- Application Approval/Deny Button Views ---

    class ApplicationActionView(discord.ui.View):
        def __init__(self, cog, user: discord.User, answers: dict, *, timeout=600):
            super().__init__(timeout=timeout)
            self.cog = cog
            self.user = user
            self.answers = answers
            self.message = None

        @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="homeworkai_approve")
        async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
            # ... (no change, see above)
            pass

        @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="homeworkai_deny")
        async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
            # ... (no change, see above)
            pass

    @discord.app_commands.command(name="onboard", description="Apply to use HomeworkAI.")
    async def onboard(self, interaction: discord.Interaction):
        # ... (no change, see above)
        pass
