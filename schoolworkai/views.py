import discord
import aiohttp

# These should be imported or defined elsewhere in your codebase
# from .constants import STRIPE_PRICE_IDS, CUSTOMER_ROLE_ID

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
        try:
            price_ids = STRIPE_PRICE_IDS
        except NameError:
            price_ids = []  # Fallback if not defined; should be imported from constants
        for price_id in price_ids:
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
        try:
            customer_role_id = CUSTOMER_ROLE_ID
        except NameError:
            customer_role_id = None  # Fallback if not defined; should be imported from constants
        for guild in self.cog.bot.guilds:
            member = guild.get_member(self.user.id)
            if not member:
                continue
            if not customer_role_id:
                continue
            role = guild.get_role(customer_role_id)
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