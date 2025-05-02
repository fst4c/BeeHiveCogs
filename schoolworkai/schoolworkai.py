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

CUSTOMER_ROLE_ID = 1364590138847400008

# Default prices for each command (USD, as string for formatting)
DEFAULT_PRICES = {
    "ask": "$0.10",
    "answer": "$0.15",
    "explain": "$0.20",
    "outline": "$0.50",
}

# Stripe price IDs to subscribe new customers to
STRIPE_PRICE_IDS = [
    "price_1RH6qnRM8UTRBxZH0ynsEnQU",
    "price_1RH7u6RM8UTRBxZHtWar1C2Z",
    "price_1RH7unRM8UTRBxZHU80WXA3e",
    "price_1RIke9RM8UTRBxZHBonGF3Yv"
]

INVITE_CREDIT_AMOUNT = 100  # $1.00 in Stripe's "cents" (100 = $1.00)
INVITE_CREDIT_PER = 10      # Every 10 invites = $1.00

class SchoolworkAI(commands.Cog):
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
                "outline": 0,
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

        # --- Bot Status Cycling ---
        self._status_cycle = [
            discord.Activity(type=discord.ActivityType.watching, name="for /ask"),
            discord.Activity(type=discord.ActivityType.watching, name="for /answer"),
            discord.Activity(type=discord.ActivityType.watching, name="for /explain"),
            discord.Activity(type=discord.ActivityType.watching, name="for /outline"),
            discord.Activity(type=discord.ActivityType.streaming, name="homework answers", url="https://twitch.tv/schoolworkai"),
            discord.CustomActivity(name="Use /signup to get started"),
        ]
        self._status_index = 0
        self._status_task = self.bot.loop.create_task(self._cycle_status())

    async def _initialize_invite_tracking(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self._cache_guild_invites(guild)

    async def _cache_guild_invites(self, guild):
        try:
            invites = await guild.invites()
            self._invite_code_cache[guild.id] = {invite.code: invite.uses for invite in invites}
        except discord.Forbidden:
            self._invite_code_cache[guild.id] = {}
        except Exception as e:
            print(f"Error caching invites for guild {guild.id}: {e}")

    async def cog_load(self):
        await self._maybe_update_all_pricing_channels()
        await self._maybe_update_all_stats_channels()
        self.bot.loop.create_task(self._periodic_stats_update())
        self.bot.loop.create_task(self._initialize_invite_tracking())

    async def _maybe_update_all_pricing_channels(self):
        # Wait for bot to be ready
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                await self._update_pricing_channel(guild)
            except Exception as e:
                print(f"Error updating pricing channel for guild {guild.id}: {e}")

    async def _maybe_update_all_stats_channels(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                await self._update_stats_channel(guild)
            except Exception as e:
                print(f"Error updating stats channel for guild {guild.id}: {e}")

    async def _periodic_stats_update(self):
        await self.bot.wait_until_ready()
        while True:
            for guild in self.bot.guilds:
                try:
                    await self._update_stats_channel(guild)
                except Exception as e:
                    print(f"Error updating stats channel for guild {guild.id}: {e}")
            await asyncio.sleep(120)  # Update every 120 seconds

    async def _cycle_status(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                activity = self._status_cycle[self._status_index]
                await self.bot.change_presence(activity=activity)
                self._status_index = (self._status_index + 1) % len(self._status_cycle)
            except Exception as e:
                print(f"Error cycling status: {e}")
            await asyncio.sleep(30)  # Change status every 30 seconds

    async def _update_pricing_channel(self, guild):
        channel_id = await self.config.guild(guild).pricing_channel()
        prices = await self.config.guild(guild).prices()
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(
            title="What SchoolworkAI costs",
            description=(
                "SchoolworkAI usage and charges are metered in real-time. You're only charged for what you use."
            ),
            color=0x476b89
        )
        command_descriptions = {
            "ask": "Ask AI any question for any subject of school",
            "answer": "Ask AI to help you solve a multiple choice or prompt based question",
            "explain": "Ask AI to help explain something to you better",
            "outline": "Ask AI to generate an outline for a paper you need to write.",
        }
        command_mentions = {
            "ask": "</ask:1367827206226841685>",
            "answer": "</answer:1367827206226841683>",
            "explain": "</explain:1367827206226841689>",
            "outline": "</outline:1367827206226841684>",
        }
        for cmd, price in prices.items():
            label = cmd.capitalize()
            description = command_descriptions.get(cmd, "")
            mention = command_mentions.get(cmd, "")
            value = f"{description}\n**{price}** per {mention}"
            embed.add_field(name=label, value=value, inline=True)
        embed.set_footer(text=(
            "Prices are subject to change with prior notice.\n\n"
            "Earn free usage by inviting friends! For every 10 friends who join and onboard, "
            "you receive $1 in free usage credit.\n\n"
            "Not signed up yet? Use /signup to start benefiting from SchoolworkAI!"
        ))

        # Try to edit the previous pricing message if it exists, else send a new one
        msg_id = await self.config.guild(guild).pricing_message_id()
        msg = None
        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
            except discord.NotFound:
                msg = None
        if msg:
            try:
                await msg.edit(embed=embed)
            except discord.Forbidden:
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
        embed = discord.Embed(
            title="SchoolworkAI Usage & Ratings",
            color=0x476b89,
            description="Statistics for SchoolworkAI usage and answer ratings in this server."
        )
        command_stats = {
            "ask": "Asks solved",
            "answer": "Answers generated",
            "explain": "Explanations given",
            "outline": "Outlines generated",
            "upvotes": "👍 Upvotes",
            "downvotes": "👎 Downvotes",
        }
        for cmd, label in command_stats.items():
            value = str(stats.get(cmd, 0))
            embed.add_field(name=label, value=value, inline=True)
        embed.set_footer(text=(
            "Stats update live as users interact with SchoolworkAI.\n\n"
            "Invite friends! After onboarding, every 10 users you invite gets you $1 of free SchoolworkAI usage."
        ))

        # Try to edit the previous stats message if it exists, else send a new one
        msg_id = await self.config.guild(guild).stats_message_id()
        msg = None
        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
            except discord.NotFound:
                msg = None
        if msg:
            try:
                await msg.edit(embed=embed)
            except discord.Forbidden:
                # If can't edit, send new
                msg = await channel.send(embed=embed)
                await self.config.guild(guild).stats_message_id.set(msg.id)
        else:
            msg = await channel.send(embed=embed)
            await self.config.guild(guild).stats_message_id.set(msg.id)

    async def get_openai_key(self):
        tokens = await self.bot.get_shared_api_tokens("openai")
        return tokens.get("api_key") if tokens else None

    async def get_stripe_key(self):
        tokens = await self.bot.get_shared_api_tokens("stripe")
        return tokens.get("api_key") if tokens else None

    async def get_twilio_keys(self):
        tokens = await self.bot.get_shared_api_tokens("twilio")
        if not tokens:
            return (None, None)
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
                                if resp.status not in (200, 201):
                                    print(f"Error granting credit: {resp.status}")
                    except Exception as e:
                        print(f"Error during credit grant: {e}")
                await inviter_conf.invite_credits_granted.set(credits_granted + to_grant)
                # Optionally, notify the user
                try:
                    user = self.bot.get_user(inviter_id)
                    if user:
                        await user.send(
                            f"🎉 You've earned ${to_grant}.00 in promotional credit for inviting new users to SchoolworkAI! Thank you for spreading the word, keep it up and you'll keep earning credit."
                        )
                except Exception as e:
                    print(f"Error notifying user {inviter_id}: {e}")

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
        except discord.Forbidden:
            print(f"Permission error while accessing invites for guild {guild.id}")
        except Exception as e:
            print(f"Error finding inviter for member {member.id} in guild {guild.id}: {e}")
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
                    embed = discord.Embed(
                        title="You brought a friend!",
                        description=f"Thanks for inviting {member.mention} to SchoolworkAI!\n\nIf they sign up and complete onboarding, you'll get $1 of free SchoolworkAI usage as our way of saying thank you.\n\nDon't forget to remind them to **/signup**.",
                        color=0x476b89
                    )
                    await user.send(embed=embed)
            except Exception as e:
                print(f"Error notifying inviter {inviter_id}: {e}")

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
    async def schoolworkai(self, ctx):
        """SchoolworkAI configuration commands."""

    @commands.group(name="schoolworkaiset", invoke_without_command=True)
    @commands.guild_only()
    async def schoolworkaiset(self, ctx):
        """SchoolworkAI admin/configuration/management commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @schoolworkaiset.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def pendingchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel where SchoolworkAI applications are sent."""
        await self.config.guild(ctx.guild).applications_channel.set(channel.id)
        embed = discord.Embed(
            title="Pending signups channel set",
            description=f"Applications channel set to {channel.mention}.",
            color=0x2bbd8e  # Fixed: was negative, should be positive for green
        )
        await ctx.send(embed=embed)

    @schoolworkaiset.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def pricingchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel where SchoolworkAI pricing is displayed and update the pricing message."""
        await self.config.guild(ctx.guild).pricing_channel.set(channel.id)
        await self._update_pricing_channel(ctx.guild)
        embed = discord.Embed(
            title="Pricing channel set",
            description=f"Pricing channel set to {channel.mention}. The pricing message has been updated.",
            color=0x2bbd8e
        )
        await ctx.send(embed=embed)

    @schoolworkaiset.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def statchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel where SchoolworkAI usage and rating statistics are displayed and update the stats message."""
        await self.config.guild(ctx.guild).stats_channel.set(channel.id)
        await self._update_stats_channel(ctx.guild)
        embed = discord.Embed(
            title="Statistics channel set",
            description=f"Stats channel set to {channel.mention}. The stats message has been updated.",
            color=0x2bbd8e
        )
        await ctx.send(embed=embed)

    @schoolworkaiset.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def resetstats(self, ctx):
        """Reset all SchoolworkAI usage and rating statistics for this server."""
        await self.config.guild(ctx.guild).stats.set({
            "ask": 0,
            "answer": 0,
            "explain": 0,
            "outline": 0,
            "upvotes": 0,
            "downvotes": 0,
        })
        await self._update_stats_channel(ctx.guild)
        embed = discord.Embed(
            title="Statistics reset",
            description="All SchoolworkAI usage and rating statistics have been reset for this server.",
            color=0x2bbd8e
        )
        await ctx.send(embed=embed)

    @schoolworkaiset.command()
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
                        await member.add_roles(role, reason="Granted SchoolworkAI customer role (setcustomerid)")
                else:
                    if role in member.roles:
                        await member.remove_roles(role, reason="Removed SchoolworkAI customer role (setcustomerid)")
            except discord.Forbidden:
                pass

        if not prev_id and customer_id:
            try:
                embed = discord.Embed(
                    title="Welcome to SchoolworkAI!",
                    description=(
                        "You now have access to SchoolworkAI.\n\n"
                        "**How to use:**\n"
                        "- Use `/ask` to get answers to general questions (text or image supported).\n"
                        "- Use `/answer` for multiple choice or comparison questions (text or image supported).\n"
                        "- Use `/explain` to get step-by-step explanations for your homework problems.\n"
                        "- Use `/outline` to generate an outline for your paper.\n\n"
                        "All answers are sent to you in DMs for privacy.\n\n"
                        f"To manage your billing or connect your payment method, visit our [billing portal]({self.billing_portal_url})"
                    ),
                    color=0x476b89
                )
                await user.send(embed=embed)
            except discord.Forbidden:
                pass
        embed = discord.Embed(
            title="Customer ID Set",
            description=f"Customer ID for {user.mention} set to `{customer_id}`.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @schoolworkaiset.command()
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
                    await member.remove_roles(role, reason="Removed SchoolworkAI customer role (removecustomerid)")
            except discord.Forbidden:
                pass

        embed = discord.Embed(
            title="Customer ID Removed",
            description=f"Customer ID for {user.mention} has been removed and the customer role revoked.",
            color=discord.Color.orange()
        )
        await ctx.send(embed=embed)

    @schoolworkaiset.command(name="resetdata")
    @commands.is_owner()
    async def resetmodule(self, ctx):
        """
        **OWNER ONLY**: Reset all SchoolworkAI cog data (users and guilds).
        This will erase all stored customer IDs, applications, and configuration.
        """
        confirm_message = await ctx.send(
            embed=discord.Embed(
                title="Reset SchoolworkAI Data",
                description="⚠️ **Are you sure you want to reset all SchoolworkAI cog data?**\n"
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
                    title="SchoolworkAI Data Reset",
                    description="All SchoolworkAI cog data has been erased.",
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

        @discord.ui.button(label="Allow", style=discord.ButtonStyle.success, custom_id="schoolworkai_approve")
        async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.defer(ephemeral=True, thinking=True)
            # Only allow admins to approve
            if not (getattr(interaction.user, "guild_permissions", None) and (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator)):
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
                        await member.add_roles(role, reason="Granted SchoolworkAI customer role (application approved)")
                except discord.Forbidden:
                    pass

            # DM the user
            try:
                embed = discord.Embed(
                    title="Welcome to SchoolworkAI!",
                    description=(
                        "Your signup has been **approved**! 🎉\n\n"
                        "You now have access to SchoolworkAI.\n\n"
                        "**How to use:**\n"
                        "- Use the `ask` command in any server where SchoolworkAI is enabled.\n"
                        "- You can ask questions by text or by attaching an image.\n\n"
                        f"**You still need to add a payment method to prevent service interruptions**.\n- [Click here to sign in and add one.]({self.cog.billing_portal_url})"
                    ),
                    color=0x476b89
                )
                if subscription_errors:
                    embed.add_field(
                        name="Subscription Issues",
                        value="Some subscriptions could not be created automatically:\n" + "\n".join(subscription_errors),
                        inline=False
                    )
                await self.user.send(embed=embed)
            except discord.Forbidden:
                pass

            # Edit the application message to show approved
            if self.message:
                try:
                    embed = self.message.embeds[0]
                    embed.color = discord.Color.green()
                    embed.title = "SchoolworkAI application (Approved)"
                    if subscription_errors:
                        embed.add_field(
                            name="Subscription Issues",
                            value="Some subscriptions could not be created automatically:\n" + "\n".join(subscription_errors),
                            inline=False
                        )
                    await self.message.edit(embed=embed, view=None)
                except discord.Forbidden:
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

        @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="schoolworkai_deny")
        async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Only allow admins to deny
            if not (getattr(interaction.user, "guild_permissions", None) and (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator)):
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
                            title="SchoolworkAI Application Denied",
                            description=f"Your application was denied for the following reason:\n\n> {self.reason.value}\n\nYou may submit a new application if you wish.",
                            color=discord.Color.red()
                        )
                        await self.user.send(embed=embed)
                    except discord.Forbidden:
                        pass
                    # Edit the application message to show denied
                    if self.message:
                        try:
                            embed = self.message.embeds[0]
                            embed.color = discord.Color.red()
                            embed.title = "SchoolworkAI Application (Denied)"
                            embed.add_field(name="Denial Reason", value=self.reason.value, inline=False)
                            await self.message.edit(embed=embed, view=None)
                        except discord.Forbidden:
                            pass
                    await modal_interaction.response.send_message(f"Application for {self.user.mention} has been **denied**.", ephemeral=True)

            modal = DenyReasonModal()
            modal.cog = self.cog
            modal.user = self.user
            modal.message = self.message
            await interaction.response.send_modal(modal)

    @discord.app_commands.command(name="signup", description="Sign up for SchoolworkAI")
    async def signup(self, interaction: discord.Interaction):
        """
        Slash command: Apply to use SchoolworkAI.
        Collects info via DM prompt-by-prompt, including phone number verification via Twilio.
        """
        user = interaction.user
        # Allow reapplication if denied, block only if currently applied or already approved
        applied = await self.config.user(user).applied()
        customer_id = await self.config.user(user).customer_id()
        denied = await self.config.user(user).denied()
        if applied:
            embed = discord.Embed(
                title="Your signup is pending",
                description="Our team is still reviewing your signup, this can take up to 8 hours.\n\nIf you have waited longer than this without a response, please open a ticket for an expedited review.",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)
            return
        if customer_id:
            embed = discord.Embed(
                title="Can't get what you already have",
                description="You're already a SchoolworkAI user. Go ask it some questions or something.",
                color=0x476b89
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)
            return
        
        # Try to DM the user
        try:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Let's do this",
                    description="Signup started, check your messages to continue in private.",
                    color=0x476b89
                ),
                ephemeral=False
            )
            dm_channel = await user.create_dm()
        except discord.Forbidden:
            try:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="Your DM's are closed",
                        description="I couldn't DM you. Please enable DMs from server members and try again.",
                        color=0xff4545
                    ),
                    ephemeral=False
                )
            except discord.Forbidden:
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
            ("intended_use", "What do you **intend to use SchoolworkAI for**? (e.g., math homework, essay help, general study, etc.)"),
        ]
        answers = {}

        try:
            await dm_channel.send(
                embed=discord.Embed(
                    title="SchoolworkAI Onboarding",
                    description=(
                        ":wave: **Hi there!**\n\nLet's get you set up to use SchoolworkAI.\n"
                        "Please answer the following questions. You can type `cancel` to stop the sign-up."
                    ),
                    color=0x476b89
                )
            )
            for key, prompt in questions:
                while True:
                    await dm_channel.send(
                        embed=discord.Embed(
                            title="Question",
                            description=prompt,
                            color=0x476b89
                        )
                    )
                    try:
                        msg = await self.bot.wait_for("message", check=check, timeout=120)
                    except asyncio.TimeoutError:
                        await dm_channel.send(
                            embed=discord.Embed(
                                title="Timeout",
                                description="You took too long to respond. Application cancelled.",
                                color=discord.Color.red()
                            )
                        )
                        return
                    if msg.content.lower().strip() == "cancel":
                        await dm_channel.send(
                            embed=discord.Embed(
                                title="Cancelled",
                                description="Application cancelled.",
                                color=discord.Color.orange()
                            )
                        )
                        return
                    if key == "billing_email":
                        # Basic email validation
                        if "@" not in msg.content or "." not in msg.content:
                            await dm_channel.send(
                                embed=discord.Embed(
                                    title="Invalid Email",
                                    description="That doesn't look like a valid email. Please try again or type `cancel`.",
                                    color=discord.Color.red()
                                )
                            )
                            continue
                    if key in ("first_name", "last_name"):
                        if not msg.content.strip() or len(msg.content.strip()) > 50:
                            await dm_channel.send(
                                embed=discord.Embed(
                                    title="Invalid Name",
                                    description="Please provide a valid name (max 50 characters). Try again or type `cancel`.",
                                    color=discord.Color.red()
                                )
                            )
                            continue
                    if key == "billing_email" and len(msg.content.strip()) > 100:
                        await dm_channel.send(
                            embed=discord.Embed(
                                title="Email Too Long",
                                description="Email is too long (max 100 characters). Try again or type `cancel`.",
                                color=discord.Color.red()
                            )
                        )
                        continue
                    if key == "grade":
                        if not msg.content.strip() or len(msg.content.strip()) > 50:
                            await dm_channel.send(
                                embed=discord.Embed(
                                    title="Invalid Grade",
                                    description="Please provide a valid grade (max 50 characters). Try again or type `cancel`.",
                                    color=discord.Color.red()
                                )
                            )
                            continue
                    if key == "intended_use":
                        if not msg.content.strip() or len(msg.content.strip()) > 200:
                            await dm_channel.send(
                                embed=discord.Embed(
                                    title="Description Too Long",
                                    description="Please provide a brief description (max 200 characters). Try again or type `cancel`.",
                                    color=discord.Color.red()
                                )
                            )
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
                    embed=discord.Embed(
                        title="Phone Number",
                        description="Please enter your **mobile phone number** in international format (e.g., `+12345678901`). We'll use this solely for verification and billing; your information is **never** sold or shared.",
                        color=0x476b89
                    )
                )
                try:
                    msg = await self.bot.wait_for("message", check=check, timeout=120)
                except asyncio.TimeoutError:
                    await dm_channel.send(
                        embed=discord.Embed(
                            title="Timeout",
                            description="You took too long to respond. Application cancelled.",
                            color=discord.Color.red()
                        )
                    )
                    return
                if msg.content.lower().strip() == "cancel":
                    await dm_channel.send(
                        embed=discord.Embed(
                            title="Cancelled",
                            description="Application cancelled.",
                            color=discord.Color.orange()
                        )
                    )
                    return
                phone_number = msg.content.strip()
                if not phone_pattern.match(phone_number):
                    await dm_channel.send(
                        embed=discord.Embed(
                            title="Invalid Phone Number",
                            description="That doesn't look like a valid phone number in international format. Please try again or type `cancel`.",
                            color=discord.Color.red()
                        )
                    )
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
                    embed=discord.Embed(
                        title="Check your phone",
                        description="A verification code has been sent to you.\n\nPlease respond with the code to verify yourself.\n\n- Type **`resend`** to get a new code\n- Type **`cancel`** to cancel your signup.",
                        color=0x476b89
                    )
                )
                attempts = 0
                max_attempts = 3
                while attempts < max_attempts:
                    try:
                        code_msg = await self.bot.wait_for("message", check=check, timeout=120)
                    except asyncio.TimeoutError:
                        await dm_channel.send(
                            embed=discord.Embed(
                                title="Timeout",
                                description="You took too long to respond. Application cancelled.",
                                color=discord.Color.red()
                            )
                        )
                        return
                    code_content = code_msg.content.lower().strip()
                    if code_content == "cancel":
                        await dm_channel.send(
                            embed=discord.Embed(
                                title="Cancelled",
                                description="Application cancelled.",
                                color=discord.Color.orange()
                            )
                        )
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
                                        await dm_channel.send(
                                            embed=discord.Embed(
                                                title="Verification code sent",
                                                description="A verification code has been sent to your phone.\n\nPlease respond with the code\n\n- Type **`resend`** to get a new code\n- Type **`cancel`** to cancel your signup.",
                                                color=0x476b89
                                            )
                                        )
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
                                        await dm_channel.send(
                                            embed=discord.Embed(
                                                title="Too Many Attempts",
                                                description="Too many failed attempts. Application cancelled.",
                                                color=discord.Color.red()
                                            )
                                        )
                                        return
                                    continue
                                if resp.status in (200, 201) and result.get("status") == "approved":
                                    await dm_channel.send(
                                        embed=discord.Embed(
                                            title="Thanks, you're verified",
                                            description="Thanks for helping us fight fraud and fake users. We'll never sell or share this information - with advertisers, parents, or schools.",
                                            color=0x2bbd8e
                                        )
                                    )
                                    answers["phone_number"] = phone_number
                                    break
                                else:
                                    await dm_channel.send(
                                        embed=discord.Embed(
                                            title="That wasn't right",
                                            description="The code you entered doesn't match the code we sent you.\nPlease try again, type **`resend`** to get a new code, or **`cancel`** to stop.",
                                            color=0xff4545
                                        )
                                    )
                                    attempts += 1
                                    if attempts >= max_attempts:
                                        await dm_channel.send(
                                            embed=discord.Embed(
                                                title="Your application cannot be processed at this time",
                                                description="You failed verification too many times. We're unable to continue with your sign-up at this time.\n\nPlease try again later.",
                                                color=0xff4545
                                            )
                                        )
                                        return
                                    continue
                        break
                    except Exception as e:
                        await dm_channel.send(
                            embed=discord.Embed(
                                title="Telephony error",
                                description=f"An error occurred while verifying the code: {e}",
                                color=0xff4545
                            )
                        )
                        attempts += 1
                        if attempts >= max_attempts:
                            await dm_channel.send(
                                embed=discord.Embed(
                                    title="Your application cannot be processed at this time",
                                    description="You failed verification too many times. We're unable to continue with your sign-up at this time.\n\nPlease try again later.",
                                    color=0xff4545
                                )
                            )
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
                title="New sign-up pending",
                color=0x476b89,
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
                    color=0x476b89
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
            except discord.Forbidden:
                pass

    # --- SchoolworkAI Question Commands ---

    class RatingView(discord.ui.View):
        def __init__(self, cog, ctx, prompt_type, guild_id, *, timeout=120):
            super().__init__(timeout=timeout)
            self.cog = cog
            self.ctx = ctx
            self.prompt_type = prompt_type
            self.guild_id = guild_id
            self.upvoted = False
            self.downvoted = False

        @discord.ui.button(label="👍", style=discord.ButtonStyle.success, custom_id="schoolworkai_upvote")
        async def upvote(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.upvoted:
                await interaction.response.send_message("You already upvoted this answer.", ephemeral=True)
                return
            self.upvoted = True
            if self.guild_id:
                await self.cog._increment_stat(self.guild_id, "upvotes")
                guild = self.cog.bot.get_guild(self.guild_id)
                if guild:
                    await self.cog._update_stats_channel(guild)
            await interaction.response.send_message("Thank you for your feedback! 👍", ephemeral=True)

        @discord.ui.button(label="👎", style=discord.ButtonStyle.danger, custom_id="schoolworkai_downvote")
        async def downvote(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.downvoted:
                await interaction.response.send_message("You already downvoted this answer.", ephemeral=True)
                return
            self.downvoted = True
            if self.guild_id:
                await self.cog._increment_stat(self.guild_id, "downvotes")
                guild = self.cog.bot.get_guild(self.guild_id)
                if guild:
                    await self.cog._update_stats_channel(guild)
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

    async def _send_schoolworkai_response(
        self,
        ctx: commands.Context,
        question: str,
        image_url: str,
        prompt_type: str
    ):
        """
        Helper for ask/answer/explain/outline commands.
        prompt_type: "ask", "answer", "explain", or "outline"
        """
        customer_id = await self.config.user(ctx.author).customer_id()
        if not customer_id:
            embed = discord.Embed(
                title="Billing Profile Required",
                description="You need to set up a billing profile to use SchoolworkAI. Please contact service support for assistance.",
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
                        await member.add_roles(role, reason="Granted SchoolworkAI customer role (has customer_id)")
            except discord.Forbidden:
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
                "You are SchoolworkAI, an expert homework assistant. "
                "Answer the user's question as clearly and concisely as possible. Format math formulas using markdown, and final results inside a ```codeblock``` using standard expression indicators instead of spelling out math expressions."
                "If the user attaches an image, analyze it and provide a helpful, accurate answer."
            )
        elif prompt_type == "answer":
            system_prompt = (
                "You are SchoolworkAI, an expert at answering multiple choice and comparison questions. "
                "If the user provides a list of options or a multiple choice question, "
                "explain your reasoning concisely and accurately, and select the best answer. "
                "If the user attaches an image, analyze it for relevant information."
            )
        elif prompt_type == "explain":
            system_prompt = (
                "You are SchoolworkAI, an expert tutor. "
                "Provide a detailed, step-by-step explanation or tutorial for the user's question. "
                "If the user attaches an image, use it to help explain the answer in depth."
            )
        elif prompt_type == "outline":
            system_prompt = (
                "You are SchoolworkAI, an expert in creating outlines for academic papers. "
                "Generate a structured outline based on the user's topic and the specified number of paragraphs. "
                "Structure the outline assuming the first paragraph will be an introduction, and the last paragraph will be a conclusion."
                "Ensure the outline is logical and provides a clear framework for writing the paper. "
                "Format the outline using Discord-compatible markdown."
            )
        else:
            system_prompt = "You are SchoolworkAI, an expert homework assistant."

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

                payload = {
                    "model": "gpt-4.1",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
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
                        except KeyError:
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
                            embed_title = "Your SchoolworkAI response"
                            field_name = "You asked"
                        elif prompt_type == "answer":
                            embed_title = "SchoolworkAI chose an answer"
                            field_name = "Your question"
                        elif prompt_type == "explain":
                            embed_title = "SchoolworkAI generated an explanation"
                            field_name = "Your question"
                        elif prompt_type == "outline":
                            embed_title = "SchoolworkAI finished outlining your paper"
                            field_name = "Your request"
                        else:
                            embed_title = "Your SchoolworkAI Answer"
                            field_name = "You asked"

                        embed = discord.Embed(
                            title=embed_title,
                            color=0xfffffe
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
                            name="SchoolworkAI says...",
                            value=answer_field_value,
                            inline=False
                        )

                        # Try to DM the user
                        try:
                            view = self.RatingView(self, ctx, prompt_type, ctx.guild.id if ctx.guild else None)
                            await ctx.author.send(embed=embed, view=view)
                            await ctx.send(
                                embed=discord.Embed(
                                    title=":white_check_mark: Done",
                                    description="Check your messages",
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
        Ask SchoolworkAI an open-ended question (text or attach an image).
        The answer will be sent to you in DMs.
        """
        # Check if the user has a customer_id set
        customer_id = await self.config.user(ctx.author).customer_id()
        if not customer_id:
            embed = discord.Embed(
                title="You're not a SchoolworkAI user yet",
                description=(
                    "Get started with </signup:1367827206226841687> to sign up and start getting answers.\n\n"
                    "Power through homework faster with SchoolworkAI. Get answers, explanations, and more no matter where the question is."
                ),
                color=0xff4545
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
            content_type = getattr(att, "content_type", None) if not isinstance(att, dict) else att.get("content_type")
            url = getattr(att, "url", None) if not isinstance(att, dict) else att.get("url")
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
                # Stripe expects Bearer token, not BasicAuth for this endpoint
                headers = {
                    "Authorization": f"Bearer {stripe_key}",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(meter_url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status not in (200, 201):
                            # Log or handle errors here
                            pass
        except Exception as e:
            # Optionally log the error, but don't block the command
            pass

        if ctx.guild:
            await self._increment_usage(ctx.guild, "ask")

        await self._send_schoolworkai_response(ctx, question, image_url, prompt_type="ask")

    @commands.hybrid_command(name="answer", with_app_command=True)
    async def answer(self, ctx: commands.Context, *, question: str = None):
        """
        Ask SchoolworkAI to answer a multiple choice or comparison question.
        The answer will be sent to you in DMs.
        """
        # Check if the user has a customer_id set
        customer_id = await self.config.user(ctx.author).customer_id()
        if not customer_id:
            embed = discord.Embed(
                title="Access Required",
                description=(
                    "You don't have access to SchoolworkAI yet.\n\n"
                    "To apply for access, please use the `/onboard` command.\n"
                    "Once approved, you'll be able to use SchoolworkAI features."
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
            content_type = getattr(att, "content_type", None) if not isinstance(att, dict) else att.get("content_type")
            url = getattr(att, "url", None) if not isinstance(att, dict) else att.get("url")
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
                headers = {
                    "Authorization": f"Bearer {stripe_key}",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(meter_url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status not in (200, 201):
                            # Log or handle errors here
                            pass
        except Exception as e:
            pass

        if ctx.guild:
            await self._increment_usage(ctx.guild, "answer")

        await self._send_schoolworkai_response(ctx, question, image_url, prompt_type="answer")

    @commands.hybrid_command(name="explain", with_app_command=True)
    async def explain(self, ctx: commands.Context, *, question: str = None):
        """
        Ask SchoolworkAI for a detailed, step-by-step explanation or tutorial.
        The answer will be sent to you in DMs.
        """
        # Check if the user has a customer_id set
        customer_id = await self.config.user(ctx.author).customer_id()
        if not customer_id:
            embed = discord.Embed(
                title="Access Required",
                description=(
                    "You don't have access to SchoolworkAI yet.\n\n"
                    "To apply for access, please use the `/onboard` command.\n"
                    "Once approved, you'll be able to use SchoolworkAI features."
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
            content_type = getattr(att, "content_type", None) if not isinstance(att, dict) else att.get("content_type")
            url = getattr(att, "url", None) if not isinstance(att, dict) else att.get("url")
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
                headers = {
                    "Authorization": f"Bearer {stripe_key}",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(meter_url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status not in (200, 201):
                            # Log or handle errors here
                            pass
        except Exception as e:
            pass

        if ctx.guild:
            await self._increment_usage(ctx.guild, "explain")

        await self._send_schoolworkai_response(ctx, question, image_url, prompt_type="explain")

    @commands.hybrid_command(name="outline", with_app_command=True)
    async def outline(self, ctx: commands.Context, paragraphcount: int, *, topic: str):
        """
        Generate an outline for a paper you've been asked to write
        """
        # Check if the user has a customer_id set
        customer_id = await self.config.user(ctx.author).customer_id()
        if not customer_id:
            embed = discord.Embed(
                title="Access Required",
                description=(
                    "You don't have access to SchoolworkAI yet.\n\n"
                    "To apply for access, please use the `/onboard` command.\n"
                    "Once approved, you'll be able to use SchoolworkAI features."
                ),
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        # --- Stripe Meter Event Logging ---
        try:
            stripe_key = await self.get_stripe_key()
            if stripe_key and customer_id:
                timestamp = int(time.time())
                meter_url = "https://api.stripe.com/v1/billing/meter_events"
                data = {
                    "event_name": "outline",
                    "timestamp": timestamp,
                    "payload[stripe_customer_id]": customer_id,
                }
                headers = {
                    "Authorization": f"Bearer {stripe_key}",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(meter_url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status not in (200, 201):
                            # Log or handle errors here
                            pass
        except Exception as e:
            pass

        if ctx.guild:
            await self._increment_usage(ctx.guild, "outline")

        question = f"Generate an outline for a paper on the topic '{topic}' with {paragraphcount} paragraphs."
        await self._send_schoolworkai_response(ctx, question, image_url=None, prompt_type="outline")

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
                            await member.remove_roles(role, reason="Removed SchoolworkAI customer role (no billing profile)")
                except discord.Forbidden:
                    pass

            embed = discord.Embed(
                title="You don't have a billing profile yet.",
                description="Not a SchoolworkAI user yet? Use </signup:1367827206226841687> to get started.",
                color=0xff4545
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
                        await member.add_roles(role, reason="Granted SchoolworkAI customer role (billing command)")
            except discord.Forbidden:
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
                title="Something went wrong...",
                description="Could not generate a billing portal link. Please contact support.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, ephemeral=True)
