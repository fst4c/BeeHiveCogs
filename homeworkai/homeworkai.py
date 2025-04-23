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
            await interaction.response.defer(ephemeral=True, thinking=True)
            # Only allow admins to approve
            if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.administrator:
                await interaction.followup.send("You do not have permission to approve applications.", ephemeral=True)
                return

            # Create Stripe customer
            stripe_key = await self.cog.get_stripe_key()
            if not stripe_key:
                await interaction.followup.send("Stripe API key is not configured. Please contact an administrator.", ephemeral=True)
                return

            # Check if already has customer_id
            prev_customer_id = await self.cog.config.user(self.user).customer_id()
            if prev_customer_id:
                await interaction.followup.send("This user already has a customer ID.", ephemeral=True)
                return

            # Create Stripe customer
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {
                        "Authorization": f"Bearer {stripe_key}",
                        "Content-Type": "application/x-www-form-urlencoded"
                    }
                    data = {
                        "email": self.answers.get("billing_email"),
                        "name": f"{self.answers.get('first_name', '')} {self.answers.get('last_name', '')}".strip(),
                        "metadata[first_name]": self.answers.get("first_name", ""),
                        "metadata[last_name]": self.answers.get("last_name", ""),
                        "metadata[discord_id]": str(self.user.id),
                        "metadata[phone_number]": self.answers.get("phone_number", ""),
                        # Optionally, add new onboarding fields to Stripe metadata
                        "metadata[grade]": self.answers.get("grade", ""),
                        "metadata[intended_use]": self.answers.get("intended_use", ""),
                    }
                    async with session.post(
                        "https://api.stripe.com/v1/customers",
                        data=data,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as resp:
                        if resp.status not in (200, 201):
                            text = await resp.text()
                            await interaction.followup.send(
                                f"Failed to create Stripe customer: {resp.status}\n{text}",
                                ephemeral=True
                            )
                            return
                        try:
                            result = await resp.json()
                        except Exception as e:
                            await interaction.followup.send(
                                f"Could not decode Stripe response: {e}",
                                ephemeral=True
                            )
                            return
                        customer_id = result.get("id")
                        if not customer_id:
                            await interaction.followup.send(
                                "Stripe did not return a customer ID. Please check the logs.",
                                ephemeral=True
                            )
                            return
            except Exception as e:
                await interaction.followup.send(
                    f"An error occurred while creating the Stripe customer: {e}",
                    ephemeral=True
                )
                return

            # --- Automatically subscribe the customer to the required price IDs ---
            subscription_errors = []
            for price_id in STRIPE_PRICE_IDS:
                try:
                    async with aiohttp.ClientSession() as session:
                        headers = {
                            "Authorization": f"Bearer {stripe_key}",
                            "Content-Type": "application/x-www-form-urlencoded"
                        }
                        data = {
                            "customer": customer_id,
                            "items[0][price]": price_id,
                        }
                        async with session.post(
                            "https://api.stripe.com/v1/subscriptions",
                            data=data,
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=30)
                        ) as resp:
                            if resp.status not in (200, 201):
                                text = await resp.text()
                                subscription_errors.append(f"Failed to subscribe to {price_id}: {resp.status} {text}")
                except Exception as e:
                    subscription_errors.append(f"Exception subscribing to {price_id}: {e}")

            # Save customer_id and mark as not pending
            await self.cog.config.user(self.user).customer_id.set(customer_id)
            await self.cog.config.user(self.user).applied.set(False)
            # Optionally, mark phone_verified again
            await self.cog.config.user(self.user).phone_verified.set(True)
            # Clear denied flag on approval
            await self.cog.config.user(self.user).denied.set(False)

            # Give customer role in all mutual guilds
            for guild in self.cog.bot.guilds:
                member = guild.get_member(self.user.id)
                if not member:
                    continue
                role = guild.get_role(CUSTOMER_ROLE_ID)
                if not role:
                    continue
                try:
                    if role not in member.roles:
                        await member.add_roles(role, reason="Granted HomeworkAI customer role (application approved)")
                except Exception:
                    pass

            # DM the user
            try:
                embed = discord.Embed(
                    title="Welcome to HomeworkAI!",
                    description=(
                        "Your application has been **approved**! 🎉\n\n"
                        "You now have access to HomeworkAI.\n\n"
                        "**How to use:**\n"
                        "- Use the `ask` command in any server where HomeworkAI is enabled.\n"
                        "- You can ask questions by text or by attaching an image.\n\n"
                        f"**You still need to add a payment method to prevent service interruptions**.\n- [Click here to sign in and add one.]({self.cog.billing_portal_url})"
                    ),
                    color=0x2bbd8e
                )
                if subscription_errors:
                    embed.add_field(
                        name="Subscription Issues",
                        value="Some subscriptions could not be created automatically:\n" + "\n".join(subscription_errors),
                        inline=False
                    )
                await self.user.send(embed=embed)
            except Exception:
                pass

            # Edit the application message to show approved
            if self.message:
                try:
                    embed = self.message.embeds[0]
                    embed.color = discord.Color.green()
                    embed.title = "HomeworkAI application (Approved)"
                    if subscription_errors:
                        embed.add_field(
                            name="Subscription Issues",
                            value="Some subscriptions could not be created automatically:\n" + "\n".join(subscription_errors),
                            inline=False
                        )
                    await self.message.edit(embed=embed, view=None)
                except Exception:
                    pass

            msg_text = f"Application for {self.user.mention} has been **approved** and a Stripe customer was created."
            if subscription_errors:
                msg_text += "\n\nSome subscriptions could not be created automatically:\n" + "\n".join(subscription_errors)
            await interaction.followup.send(msg_text, ephemeral=True)

            # --- Invite Credit Granting: Check if this user was invited and grant credits if eligible ---
            # Find all users who have this user in their invited_users list
            for user_id in (u.id for u in self.cog.bot.users):
                inviter_conf = self.cog.config.user_from_id(user_id)
                invited_users = await inviter_conf.invited_users()
                if self.user.id in invited_users:
                    await self.cog._grant_invite_credits(user_id)

        @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="homeworkai_deny")
        async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Only allow admins to deny
            if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("You do not have permission to deny applications.", ephemeral=True)
                return

            # Ask for a reason via modal
            class DenyReasonModal(discord.ui.Modal, title="Deny Application"):
                reason = discord.ui.TextInput(
                    label="Reason for denial",
                    style=discord.TextStyle.paragraph,
                    required=True,
                    max_length=300,
                    placeholder="Please provide a reason for denying this application."
                )

                async def on_submit(self, modal_interaction: discord.Interaction):
                    # Save as denied, mark as not pending
                    await self.cog.config.user(self.user).applied.set(False)
                    # Optionally, clear phone_verified
                    await self.cog.config.user(self.user).phone_verified.set(False)
                    # Mark as denied so user can reapply
                    await self.cog.config.user(self.user).denied.set(True)
                    # DM the user
                    try:
                        embed = discord.Embed(
                            title="HomeworkAI Application Denied",
                            description=f"Your application was denied for the following reason:\n\n> {self.reason.value}\n\nYou may submit a new application if you wish.",
                            color=discord.Color.red()
                        )
                        await self.user.send(embed=embed)
                    except Exception:
                        pass
                    # Edit the application message to show denied
                    if self.message:
                        try:
                            embed = self.message.embeds[0]
                            embed.color = discord.Color.red()
                            embed.title = "HomeworkAI Application (Denied)"
                            embed.add_field(name="Denial Reason", value=self.reason.value, inline=False)
                            await self.message.edit(embed=embed, view=None)
                        except Exception:
                            pass
                    await modal_interaction.response.send_message(f"Application for {self.user.mention} has been **denied**.", ephemeral=True)

            modal = DenyReasonModal()
            modal.cog = self.cog
            modal.user = self.user
            modal.message = self.message
            await interaction.response.send_modal(modal)

    @discord.app_commands.command(name="onboard", description="Apply to use HomeworkAI.")
    async def onboard(self, interaction: discord.Interaction):
        """
        Slash command: Apply to use HomeworkAI.
        Collects info via DM prompt-by-prompt, including phone number verification via Twilio.
        """
        user = interaction.user
        # Allow reapplication if denied, block only if currently applied or already approved
        applied = await self.config.user(user).applied()
        customer_id = await self.config.user(user).customer_id()
        denied = await self.config.user(user).denied()
        if applied:
            embed = discord.Embed(
                title="Already Applied",
                description="You have already applied to use HomeworkAI. Please wait for approval.",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if customer_id:
            embed = discord.Embed(
                title="Already Approved",
                description="You have already been approved for HomeworkAI. If you need help, please contact support.",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        # If denied, allow reapplication (no block)

        # Try to DM the user
        try:
            await interaction.response.send_message(
                "Please check your DMs to complete your HomeworkAI application.",
                ephemeral=True
            )
            dm_channel = await user.create_dm()
        except Exception:
            try:
                await interaction.followup.send(
                    "I couldn't DM you. Please enable DMs from server members and try again.",
                    ephemeral=True
                )
            except Exception:
                pass
            return

        def check(m):
            return m.author.id == user.id and isinstance(m.channel, discord.DMChannel)

        # Expanded questions for onboarding
        questions = [
            ("first_name", "What is your **legal first name**?"),
            ("last_name", "What is your **legal last name**?"),
            ("billing_email", "What is your **billing email address**? (This will be used for billing and notifications)"),
            ("grade", "What **grade** are you in? (e.g., 9th, 10th, college freshman, etc.)"),
            ("intended_use", "What do you **intend to use HomeworkAI for**? (e.g., math homework, essay help, general study, etc.)"),
        ]
        answers = {}

        try:
            await dm_channel.send(
                embed=discord.Embed(
                    title="HomeworkAI onboarding",
                    description=(
                        ":wave: **Hi there!**\n\nLet's get you set up to use HomeworkAI.\n"
                        "Please answer the following questions. You can type `cancel` to stop the sign-up."
                    ),
                    color=discord.Color.blurple()
                )
            )
            for key, prompt in questions:
                while True:
                    await dm_channel.send(prompt)
                    try:
                        msg = await self.bot.wait_for("message", check=check, timeout=120)
                    except asyncio.TimeoutError:
                        await dm_channel.send("You took too long to respond. Application cancelled.")
                        return
                    if msg.content.lower().strip() == "cancel":
                        await dm_channel.send("Application cancelled.")
                        return
                    if key == "billing_email":
                        # Basic email validation
                        if "@" not in msg.content or "." not in msg.content:
                            await dm_channel.send("That doesn't look like a valid email. Please try again or type `cancel`.")
                            continue
                    if key in ("first_name", "last_name"):
                        if not msg.content.strip() or len(msg.content.strip()) > 50:
                            await dm_channel.send("Please provide a valid name (max 50 characters). Try again or type `cancel`.")
                            continue
                    if key == "billing_email" and len(msg.content.strip()) > 100:
                        await dm_channel.send("Email is too long (max 100 characters). Try again or type `cancel`.")
                        continue
                    if key == "grade":
                        if not msg.content.strip() or len(msg.content.strip()) > 50:
                            await dm_channel.send("Please provide a valid grade (max 50 characters). Try again or type `cancel`.")
                            continue
                    if key == "intended_use":
                        if not msg.content.strip() or len(msg.content.strip()) > 200:
                            await dm_channel.send("Please provide a brief description (max 200 characters). Try again or type `cancel`.")
                            continue
                    answers[key] = msg.content.strip()
                    break

            # Phone number collection and verification
            # Get Twilio credentials
            account_sid, auth_token = await self.get_twilio_keys()
            if not (account_sid and auth_token):
                await dm_channel.send(
                    embed=discord.Embed(
                        title="Twilio Not Configured",
                        description="Phone verification is not available at this time. Please contact an administrator.",
                        color=discord.Color.red()
                    )
                )
                return

            # Ask for phone number
            
            phone_pattern = re.compile(r"^\+?[1-9]\d{1,14}$")  # E.164 format

            while True:
                await dm_channel.send(
                    "Please enter your **mobile phone number** in international format (e.g., `+12345678901`). This will be used for verification and important notifications. Landlines and VOIP numbers are not accepted."
                )
                try:
                    msg = await self.bot.wait_for("message", check=check, timeout=120)
                except asyncio.TimeoutError:
                    await dm_channel.send("You took too long to respond. Application cancelled.")
                    return
                if msg.content.lower().strip() == "cancel":
                    await dm_channel.send("Application cancelled.")
                    return
                phone_number = msg.content.strip()
                if not phone_pattern.match(phone_number):
                    await dm_channel.send("That doesn't look like a valid phone number in international format. Please try again or type `cancel`.")
                    continue

                # Send verification code via Twilio Verify API
                try:
                    tokens = await self.bot.get_shared_api_tokens("twilio")
                    verify_sid = tokens.get("verify_sid")
                    if not verify_sid:
                        await dm_channel.send(
                            embed=discord.Embed(
                                title="Twilio Not Configured",
                                description="Phone verification is not available at this time. Please contact an administrator.",
                                color=discord.Color.red()
                            )
                        )
                        return
                    url = f"https://verify.twilio.com/v2/Services/{verify_sid}/Verifications"
                    data = {
                        "To": phone_number,
                        "Channel": "sms"
                    }
                    async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(account_sid, auth_token)) as session:
                        async with session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                            if resp.status not in (200, 201):
                                text = await resp.text()
                                await dm_channel.send(
                                    embed=discord.Embed(
                                        title="Twilio Error",
                                        description=f"Could not send verification code. ({resp.status})\n{text}",
                                        color=discord.Color.red()
                                    )
                                )
                                continue
                except Exception as e:
                    await dm_channel.send(
                        embed=discord.Embed(
                            title="Twilio Error",
                            description=f"An error occurred while sending the verification code: {e}",
                            color=discord.Color.red()
                        )
                    )
                    continue

                await dm_channel.send(
                    "A verification code has been sent to your phone. Please enter the code you received, type `resend` to get a new code, or type `cancel` to stop."
                )
                attempts = 0
                max_attempts = 3
                while attempts < max_attempts:
                    try:
                        code_msg = await self.bot.wait_for("message", check=check, timeout=120)
                    except asyncio.TimeoutError:
                        await dm_channel.send("You took too long to respond. Application cancelled.")
                        return
                    code_content = code_msg.content.lower().strip()
                    if code_content == "cancel":
                        await dm_channel.send("Application cancelled.")
                        return
                    if code_content == "resend":
                        # Resend the code
                        try:
                            tokens = await self.bot.get_shared_api_tokens("twilio")
                            verify_sid = tokens.get("verify_sid")
                            url = f"https://verify.twilio.com/v2/Services/{verify_sid}/Verifications"
                            data = {
                                "To": phone_number,
                                "Channel": "sms"
                            }
                            async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(account_sid, auth_token)) as session:
                                async with session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                                    if resp.status in (200, 201):
                                        await dm_channel.send("A new verification code has been sent to your phone. Please enter the new code, type `resend` to get another code, or type `cancel` to stop.")
                                    else:
                                        text = await resp.text()
                                        await dm_channel.send(
                                            embed=discord.Embed(
                                                title="Twilio Error",
                                                description=f"Could not resend verification code. ({resp.status})\n{text}",
                                                color=discord.Color.red()
                                            )
                                        )
                        except Exception as e:
                            await dm_channel.send(
                                embed=discord.Embed(
                                    title="Twilio Error",
                                    description=f"An error occurred while resending the verification code: {e}",
                                    color=discord.Color.red()
                                )
                            )
                        continue  # Don't count as an attempt
                    code = code_msg.content.strip()
                    # Verify code with Twilio
                    try:
                        tokens = await self.bot.get_shared_api_tokens("twilio")
                        verify_sid = tokens.get("verify_sid")
                        url = f"https://verify.twilio.com/v2/Services/{verify_sid}/VerificationCheck"
                        data = {
                            "To": phone_number,
                            "Code": code
                        }
                        async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(account_sid, auth_token)) as session:
                            async with session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                                # Defensive: check for JSON decode error
                                try:
                                    result = await resp.json()
                                except Exception as e:
                                    await dm_channel.send(
                                        embed=discord.Embed(
                                            title="Twilio Error",
                                            description=f"Could not decode Twilio response: {e}",
                                            color=discord.Color.red()
                                        )
                                    )
                                    attempts += 1
                                    if attempts >= max_attempts:
                                        await dm_channel.send("Too many failed attempts. Application cancelled.")
                                        return
                                    continue
                                if resp.status in (200, 201) and result.get("status") == "approved":
                                    await dm_channel.send(
                                        embed=discord.Embed(
                                            title="Phone verified",
                                            description="Your verification was successful. Thanks for helping us fight fraud.",
                                            color=0x2bbd8e
                                        )
                                    )
                                    answers["phone_number"] = phone_number
                                    break
                                else:
                                    await dm_channel.send(
                                        embed=discord.Embed(
                                            title="Verification Failed",
                                            description="The code you entered is incorrect. Please try again, type `resend` to get a new code, or `cancel` to stop.",
                                            color=discord.Color.red()
                                        )
                                    )
                                    attempts += 1
                                    if attempts >= max_attempts:
                                        await dm_channel.send("Too many failed attempts. Application cancelled.")
                                        return
                                    continue
                        break
                    except Exception as e:
                        await dm_channel.send(
                            embed=discord.Embed(
                                title="Twilio Error",
                                description=f"An error occurred while verifying the code: {e}",
                                color=discord.Color.red()
                            )
                        )
                        attempts += 1
                        if attempts >= max_attempts:
                            await dm_channel.send("Too many failed attempts. Application cancelled.")
                            return
                        continue
                else:
                    continue  # If not broken out of, ask for phone again
                break  # Phone verified, break out of phone loop

            # Send application to applications channel
            guild = interaction.guild
            channel_id = await self.config.guild(guild).applications_channel()
            channel = guild.get_channel(channel_id) if (guild and channel_id) else None
            if not channel:
                await dm_channel.send(
                    embed=discord.Embed(
                        title="Applications Channel Not Set",
                        description="Applications channel is not set. Please contact an admin.",
                        color=discord.Color.red()
                    )
                )
                return

            embed = discord.Embed(
                title="New HomeworkAI sign-up",
                color=0xfffffe,
            )
            embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=False)
            embed.add_field(name="First name", value=answers["first_name"], inline=True)
            embed.add_field(name="Last name", value=answers["last_name"], inline=True)
            embed.add_field(name="Billing email", value=answers["billing_email"], inline=False)
            embed.add_field(name="Phone number", value=answers["phone_number"], inline=False)
            embed.add_field(name="Grade", value=answers.get("grade", "N/A"), inline=True)
            embed.add_field(name="Intended use", value=answers.get("intended_use", "N/A"), inline=True)

            # Send with action buttons
            view = self.ApplicationActionView(self, user, answers)
            msg = await channel.send(embed=embed, view=view)
            view.message = msg

            await self.config.user(user).applied.set(True)
            await self.config.user(user).phone_number.set(answers["phone_number"])
            await self.config.user(user).phone_verified.set(True)
            # Clear denied flag on new application
            await self.config.user(user).denied.set(False)
            await dm_channel.send(
                embed=discord.Embed(
                    title="Thanks! You're in the queue!",
                    description="We need to review your signup request to make sure everything looks good here.\n\nThis can take up to 8 hours, and you'll be notified of any updates via your DM's.",
                    color=0x2bbd8e
                )
            )
        except Exception as e:
            try:
                await dm_channel.send(
                    embed=discord.Embed(
                        title="Application Error",
                        description=f"An error occurred: {e}",
                        color=discord.Color.red()
                    )
                )
            except Exception:
                pass

    # --- HomeworkAI Question Commands ---

    class RatingView(discord.ui.View):
        def __init__(self, cog, ctx, prompt_type, guild_id, *, timeout=120):
            super().__init__(timeout=timeout)
            self.cog = cog
            self.ctx = ctx
            self.prompt_type = prompt_type
            self.guild_id = guild_id
            self.upvoted = False
            self.downvoted = False

        @discord.ui.button(label="👍", style=discord.ButtonStyle.success, custom_id="homeworkai_upvote")
        async def upvote(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.upvoted:
                await interaction.response.send_message("You already upvoted this answer.", ephemeral=True)
                return
            self.upvoted = True
            await self.cog._increment_stat(self.guild_id, "upvotes")
            await self.cog._update_stats_channel(self.ctx.guild)
            await interaction.response.send_message("Thank you for your feedback! 👍", ephemeral=True)

        @discord.ui.button(label="👎", style=discord.ButtonStyle.danger, custom_id="homeworkai_downvote")
        async def downvote(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.downvoted:
                await interaction.response.send_message("You already downvoted this answer.", ephemeral=True)
                return
            self.downvoted = True
            await self.cog._increment_stat(self.guild_id, "downvotes")
            await self.cog._update_stats_channel(self.ctx.guild)
            await interaction.response.send_message("Thank you for your feedback! 👎", ephemeral=True)

    async def _increment_stat(self, guild_id, stat):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        stats = await self.config.guild(guild).stats()
        stats[stat] = stats.get(stat, 0) + 1
        await self.config.guild(guild).stats.set(stats)

    async def _increment_usage(self, guild, prompt_type):
        stats = await self.config.guild(guild).stats()
        stats[prompt_type] = stats.get(prompt_type, 0) + 1
        await self.config.guild(guild).stats.set(stats)
        await self._update_stats_channel(guild)

    async def _send_homeworkai_response(
        self,
        ctx: commands.Context,
        question: str,
        image_url: str,
        prompt_type: str
    ):
        """
        Helper for ask/answer/explain commands.
        prompt_type: "ask", "answer", or "explain"
        """
        customer_id = await self.config.user(ctx.author).customer_id()
        if not customer_id:
            embed = discord.Embed(
                title="Billing Profile Required",
                description="You need to set up a billing profile to use HomeworkAI. Please contact service support for assistance.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        # Give the customer role if not already present
        if ctx.guild:
            try:
                member = ctx.guild.get_member(ctx.author.id)
                if member:
                    role = ctx.guild.get_role(CUSTOMER_ROLE_ID)
                    if role and role not in member.roles:
                        await member.add_roles(role, reason="Granted HomeworkAI customer role (has customer_id)")
            except Exception:
                pass

        openai_key = await self.get_openai_key()
        if not openai_key:
            embed = discord.Embed(
                title="OpenAI API Key Not Configured",
                description="OpenAI API key is not configured. Please contact an administrator.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        if not question and not image_url:
            embed = discord.Embed(
                title="No Question or Image Provided",
                description="Please provide a question or attach an image.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        # Prompt engineering for each command type
        if prompt_type == "ask":
            system_prompt = (
                "You are HomeworkAI, an expert homework assistant. "
                "Answer the user's question as clearly and concisely as possible. Format math formulas using markdown, and final results inside a ```codeblock``` using standard expression indicators instead of spelling out math expressions."
                "If the user attaches an image, analyze it and provide a helpful, accurate answer."
            )
        elif prompt_type == "answer":
            system_prompt = (
                "You are HomeworkAI, an expert at answering multiple choice and comparison questions. "
                "If the user provides a list of options or a multiple choice question, "
                "explain your reasoning concisely and accurately, and select the best answer. "
                "If the user attaches an image, analyze it for relevant information."
            )
        elif prompt_type == "explain":
            system_prompt = (
                "You are HomeworkAI, an expert tutor. "
                "Provide a detailed, step-by-step explanation or tutorial for the user's question. "
                "If the user attaches an image, use it to help explain the answer in depth."
            )
        else:
            system_prompt = "You are HomeworkAI, an expert homework assistant."

        async with ctx.typing():
            try:
                headers = {
                    "Authorization": f"Bearer {openai_key}",
                    "Content-Type": "application/json"
                }
                if image_url:
                    user_content = [
                        {"type": "text", "text": question or "Please analyze this image."},
                        {"type": "image_url", "image_url": {"url": image_url}}
                    ]
                else:
                    user_content = question

                if image_url:
                    payload = {
                        "model": "gpt-4.1",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content}
                        ],
                        "max_tokens": 1024
                    }
                else:
                    payload = {
                        "model": "gpt-4.1",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": question}
                        ],
                        "max_tokens": 1024
                    }
                endpoint = "https://api.openai.com/v1/chat/completions"

                async with aiohttp.ClientSession() as session:
                    async with session.post(endpoint, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            embed = discord.Embed(
                                title="OpenAI API Error",
                                description=f"Status: {resp.status}\n{text}",
                                color=discord.Color.red()
                            )
                            await ctx.send(embed=embed)
                            return
                        data = await resp.json()
                        answer = None
                        try:
                            answer = data["choices"][0]["message"]["content"]
                        except Exception:
                            embed = discord.Embed(
                                title="Unexpected OpenAI Response",
                                description="OpenAI API returned an unexpected response.",
                                color=discord.Color.red()
                            )
                            await ctx.send(embed=embed)
                            return

                        # Compose the DM embed
                        # Truncate answer if needed
                        max_answer_len = 1900
                        if len(answer) > max_answer_len:
                            answer = answer[:max_answer_len] + "\n\n*Response truncated.*"

                        # Compose the question section
                        if question:
                            question_section = question
                        elif image_url:
                            question_section = "*(No text question provided, image attached)*"
                        else:
                            question_section = "N/A"

                        # Title and field names per command
                        if prompt_type == "ask":
                            embed_title = "Your HomeworkAI Answer"
                            field_name = "You asked"
                        elif prompt_type == "answer":
                            embed_title = "HomeworkAI Multiple Choice/Comparison Answer"
                            field_name = "Your question"
                        elif prompt_type == "explain":
                            embed_title = "HomeworkAI Step-by-Step Explanation"
                            field_name = "Your question"
                        else:
                            embed_title = "Your HomeworkAI Answer"
                            field_name = "You asked"

                        embed = discord.Embed(
                            title=embed_title,
                            color=discord.Color.blurple()
                        )
                        # Truncate question_section to 1024 for embed field
                        embed.add_field(
                            name=field_name,
                            value=question_section if len(question_section) < 1024 else question_section[:1020] + "...",
                            inline=False
                        )
                        if image_url:
                            embed.set_image(url=image_url)
                        # Truncate answer to 1024 for embed field (Discord API limit)
                        answer_field_value = answer if len(answer) <= 1024 else answer[:1020] + "..."
                        embed.add_field(
                            name="HomeworkAI says...",
                            value=answer_field_value,
                            inline=False
                        )

                        # Try to DM the user
                        try:
                            view = self.RatingView(self, ctx, prompt_type, ctx.guild.id if ctx.guild else None)
                            await ctx.author.send(embed=embed, view=view)
                            await ctx.send(
                                embed=discord.Embed(
                                    title="Finished thinking!",
                                    description="Check your DM's for the answer",
                                    color=0x2bbd8e
                                )
                            )
                        except discord.Forbidden:
                            await ctx.send(
                                embed=discord.Embed(
                                    title="Unable to DM",
                                    description="I couldn't send you a DM. Please enable DMs from server members and try again.",
                                    color=discord.Color.red()
                                )
                            )
                        except Exception as e:
                            await ctx.send(
                                embed=discord.Embed(
                                    title="DM Error",
                                    description=f"An error occurred while sending your answer in DMs: {e}",
                                    color=discord.Color.red()
                                )
                            )

            except Exception as e:
                embed = discord.Embed(
                    title="OpenAI Error",
                    description=f"An error occurred while contacting OpenAI: {e}",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)

    @commands.hybrid_command(name="ask", with_app_command=True)
    async def ask(self, ctx: commands.Context, *, question: str = None):
        """
        Ask HomeworkAI an open-ended question (text or attach an image).
        The answer will be sent to you in DMs.
        """
        # Check if the user has a customer_id set
        customer_id = await self.config.user(ctx.author).customer_id()
        if not customer_id:
            embed = discord.Embed(
                title="Access Required",
                description=(
                    "You don't have access to HomeworkAI yet.\n\n"
                    "To apply for access, please use the `/onboard` command.\n"
                    "Once approved, you'll be able to use HomeworkAI features."
                ),
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        image_url = None
        # For slash commands, attachments are in ctx.interaction.data if present
        attachments = []
        if hasattr(ctx, "interaction") and ctx.interaction is not None:
            # Try to get attachments from the interaction (for slash)
            data = getattr(ctx.interaction, "data", {})
            resolved = data.get("resolved", {}) if data else {}
            attachments = list(resolved.get("attachments", {}).values()) if resolved else []
        if not attachments and ctx.message and ctx.message.attachments:
            attachments = ctx.message.attachments
        for att in attachments:
            # Discord.py's Attachment object or dict from interaction
            content_type = getattr(att, "content_type", None) or att.get("content_type") if isinstance(att, dict) else None
            url = getattr(att, "url", None) or att.get("url") if isinstance(att, dict) else None
            if content_type and content_type.startswith("image/"):
                image_url = url
                break

        # --- Stripe Meter Event Logging ---
        try:
            stripe_key = await self.get_stripe_key()
            if stripe_key and customer_id:
                # Use current UTC timestamp as int
                timestamp = int(time.time())
                meter_url = "https://api.stripe.com/v1/billing/meter_events"
                data = {
                    "event_name": "ask",
                    "timestamp": timestamp,
                    "payload[stripe_customer_id]": customer_id,
                }
                auth = aiohttp.BasicAuth(stripe_key)
                async with aiohttp.ClientSession(auth=auth) as session:
                    async with session.post(meter_url, data=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        # Optionally, you could log or handle errors here
                        pass
        except Exception as e:
            # Optionally log the error, but don't block the command
            pass

        if ctx.guild:
            await self._increment_usage(ctx.guild, "ask")

        await self._send_homeworkai_response(ctx, question, image_url, prompt_type="ask")

    @commands.hybrid_command(name="answer", with_app_command=True)
    async def answer(self, ctx: commands.Context, *, question: str = None):
        """
        Ask HomeworkAI to answer a multiple choice or comparison question.
        The answer will be sent to you in DMs.
        """
        # Check if the user has a customer_id set
        customer_id = await self.config.user(ctx.author).customer_id()
        if not customer_id:
            embed = discord.Embed(
                title="Access Required",
                description=(
                    "You don't have access to HomeworkAI yet.\n\n"
                    "To apply for access, please use the `/onboard` command.\n"
                    "Once approved, you'll be able to use HomeworkAI features."
                ),
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        image_url = None
        # For slash commands, attachments are in ctx.interaction.data if present
        attachments = []
        if hasattr(ctx, "interaction") and ctx.interaction is not None:
            data = getattr(ctx.interaction, "data", {})
            resolved = data.get("resolved", {}) if data else {}
            attachments = list(resolved.get("attachments", {}).values()) if resolved else []
        if not attachments and ctx.message and ctx.message.attachments:
            attachments = ctx.message.attachments
        for att in attachments:
            content_type = getattr(att, "content_type", None) or att.get("content_type") if isinstance(att, dict) else None
            url = getattr(att, "url", None) or att.get("url") if isinstance(att, dict) else None
            if content_type and content_type.startswith("image/"):
                image_url = url
                break

        # --- Stripe Meter Event Logging ---
        try:
            stripe_key = await self.get_stripe_key()
            if stripe_key and customer_id:
                timestamp = int(time.time())
                meter_url = "https://api.stripe.com/v1/billing/meter_events"
                data = {
                    "event_name": "answer",
                    "timestamp": timestamp,
                    "payload[stripe_customer_id]": customer_id,
                }
                auth = aiohttp.BasicAuth(stripe_key)
                async with aiohttp.ClientSession(auth=auth) as session:
                    async with session.post(meter_url, data=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        pass
        except Exception as e:
            pass

        if ctx.guild:
            await self._increment_usage(ctx.guild, "answer")

        await self._send_homeworkai_response(ctx, question, image_url, prompt_type="answer")

    @commands.hybrid_command(name="explain", with_app_command=True)
    async def explain(self, ctx: commands.Context, *, question: str = None):
        """
        Ask HomeworkAI for a detailed, step-by-step explanation or tutorial.
        The answer will be sent to you in DMs.
        """
        # Check if the user has a customer_id set
        customer_id = await self.config.user(ctx.author).customer_id()
        if not customer_id:
            embed = discord.Embed(
                title="Access Required",
                description=(
                    "You don't have access to HomeworkAI yet.\n\n"
                    "To apply for access, please use the `/onboard` command.\n"
                    "Once approved, you'll be able to use HomeworkAI features."
                ),
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        image_url = None
        # For slash commands, attachments are in ctx.interaction.data if present
        attachments = []
        if hasattr(ctx, "interaction") and ctx.interaction is not None:
            data = getattr(ctx.interaction, "data", {})
            resolved = data.get("resolved", {}) if data else {}
            attachments = list(resolved.get("attachments", {}).values()) if resolved else []
        if not attachments and ctx.message and ctx.message.attachments:
            attachments = ctx.message.attachments
        for att in attachments:
            content_type = getattr(att, "content_type", None) or att.get("content_type") if isinstance(att, dict) else None
            url = getattr(att, "url", None) or att.get("url") if isinstance(att, dict) else None
            if content_type and content_type.startswith("image/"):
                image_url = url
                break

        # --- Stripe Meter Event Logging ---
        try:
            stripe_key = await self.get_stripe_key()
            if stripe_key and customer_id:
                timestamp = int(time.time())
                meter_url = "https://api.stripe.com/v1/billing/meter_events"
                data = {
                    "event_name": "explain",
                    "timestamp": timestamp,
                    "payload[stripe_customer_id]": customer_id,
                }
                auth = aiohttp.BasicAuth(stripe_key)
                async with aiohttp.ClientSession(auth=auth) as session:
                    async with session.post(meter_url, data=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        pass
        except Exception as e:
            pass

        if ctx.guild:
            await self._increment_usage(ctx.guild, "explain")

        await self._send_homeworkai_response(ctx, question, image_url, prompt_type="explain")

    @commands.hybrid_command(name="billing", with_app_command=True)
    async def billing(self, ctx: commands.Context):
        """
        View payments, dues, invoices, and update your payment method on-file
        """
        await ctx.defer(ephemeral=True)
        customer_id = await self.config.user(ctx.author).customer_id()
        if not customer_id:
            # Remove the customer role if present
            if ctx.guild:
                try:
                    member = ctx.guild.get_member(ctx.author.id)
                    if member:
                        role = ctx.guild.get_role(CUSTOMER_ROLE_ID)
                        if role and role in member.roles:
                            await member.remove_roles(role, reason="Removed HomeworkAI customer role (no billing profile)")
                except Exception:
                    pass

            embed = discord.Embed(
                title="No Billing Profile",
                description="You do not have a billing profile set up. Please contact support.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, ephemeral=True)
            return

        # Give the customer role if not already present
        if ctx.guild:
            try:
                member = ctx.guild.get_member(ctx.author.id)
                if member:
                    role = ctx.guild.get_role(CUSTOMER_ROLE_ID)
                    if role and role not in member.roles:
                        await member.add_roles(role, reason="Granted HomeworkAI customer role (billing command)")
            except Exception:
                pass

        stripe_key = await self.get_stripe_key()
        if not stripe_key:
            embed = discord.Embed(
                title="Stripe API Key Not Configured",
                description="Stripe API key is not configured. Please contact an administrator.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, ephemeral=True)
            return

        portal_url = None
        try:
            # Build the return_url as the URL for the channel the command was used in
            # If in a guild, use the guild channel URL, else fallback to a default
            if ctx.guild and ctx.channel:
                return_url = f"https://discord.com/channels/{ctx.guild.id}/{ctx.channel.id}"
            elif ctx.channel:
                # For DMs, guild is None, so use @me for the guild id
                return_url = f"https://discord.com/channels/@me/{ctx.channel.id}"
            else:
                return_url = "https://beehive.systems"

            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {stripe_key}",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
                data = {
                    "customer": customer_id,
                    "return_url": return_url
                }
                async with session.post(
                    "https://api.stripe.com/v1/billing_portal/sessions",
                    data=data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        embed = discord.Embed(
                            title="Stripe API Error",
                            description=f"Status: {resp.status}\n{text}",
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=embed, ephemeral=True)
                        return
                    try:
                        result = await resp.json()
                    except Exception as e:
                        embed = discord.Embed(
                            title="Stripe API Error",
                            description=f"Could not decode Stripe response: {e}",
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=embed, ephemeral=True)
                        return
                    portal_url = result.get("url")
        except Exception as e:
            embed = discord.Embed(
                title="Stripe Error",
                description=f"An error occurred while contacting Stripe: {e}",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, ephemeral=True)
            return

        if portal_url:
            embed = discord.Embed(
                title="Here's your sign-in link",
                description=f"[You can manage your billing by clicking here]({portal_url})\n\n**Do not share this link**",
                color=discord.Color.blurple()
            )
            await ctx.send(embed=embed, ephemeral=True)
        else:
            embed = discord.Embed(
                title="Billing Portal Error",
                description="Could not generate a billing portal link. Please contact support.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, ephemeral=True)
