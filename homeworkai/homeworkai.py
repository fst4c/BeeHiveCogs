import discord
from redbot.core import commands, Config, checks, bank
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate

import aiohttp

class HomeworkAI(commands.Cog):
    """
    Ask AI-powered questions about your homework!
    """

    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=77777777777, force_registration=True)
        default_user = {
            "customer_id": None,
            "applied": False,
        }
        default_guild = {
            "applications_channel": None,
        }
        self.config.register_user(**default_user)
        self.config.register_guild(**default_guild)
        self.billing_portal_url = "https://www.beehive.systems/billing"  # Example link

    async def get_openai_key(self):
        # Fix: get_shared_api_tokens returns a dict, not a coroutine
        tokens = await self.bot.get_shared_api_tokens("openai")
        return tokens.get("api_key")

    async def get_stripe_key(self):
        tokens = await self.bot.get_shared_api_tokens("stripe")
        return tokens.get("api_key")

    async def cog_check(self, ctx):
        # Only allow in guilds or DMs
        return True

    @commands.group()
    @commands.guild_only()
    async def homeworkai(self, ctx):
        """HomeworkAI configuration commands."""

    @homeworkai.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def setapplications(self, ctx, channel: discord.TextChannel):
        """Set the channel where HomeworkAI applications are sent."""
        await self.config.guild(ctx.guild).applications_channel.set(channel.id)
        await ctx.send(f"Applications channel set to {channel.mention}.")

    @commands.command()
    async def onboard(self, ctx: commands.Context):
        """
        Apply to use HomeworkAI.
        """
        if await self.config.user(ctx.author).applied():
            await ctx.send("You have already applied to use HomeworkAI. Please wait for approval.")
            return

        # Fix: Modal class must be defined outside the function to avoid issues with self/cog
        # But for now, we can patch by passing cog as attribute
        class ApplicationModal(discord.ui.Modal, title="HomeworkAI Application"):
            first_name = discord.ui.TextInput(label="First Name", required=True, max_length=50)
            last_name = discord.ui.TextInput(label="Last Name", required=True, max_length=50)
            billing_email = discord.ui.TextInput(label="Billing Email", required=True, style=discord.TextStyle.short, max_length=100)

            async def on_submit(self, interaction: discord.Interaction):
                await self.process_application(interaction)

            async def process_application(self, interaction: discord.Interaction):
                # Fix: Use interaction.guild and interaction.user for slash commands
                guild = interaction.guild or ctx.guild
                user = interaction.user or ctx.author
                channel_id = await self.cog.config.guild(guild).applications_channel()
                channel = guild.get_channel(channel_id) if channel_id else None
                if not channel:
                    await interaction.response.send_message("Applications channel is not set. Please contact an admin.", ephemeral=True)
                    return
                embed = discord.Embed(
                    title="New HomeworkAI Application",
                    color=discord.Color.blurple(),
                    description=f"**User:** {user.mention} (`{user.id}`)\n"
                                f"**First Name:** {self.first_name.value}\n"
                                f"**Last Name:** {self.last_name.value}\n"
                                f"**Billing Email:** {self.billing_email.value}"
                )
                await channel.send(embed=embed)
                await self.cog.config.user(user).applied.set(True)
                await interaction.response.send_message("Your application has been submitted! We'll be in touch soon.", ephemeral=True)

        modal = ApplicationModal()
        modal.cog = self
        # Fix: Check for ctx.interaction and send modal properly, fallback to error if not available
        if hasattr(ctx, "interaction") and ctx.interaction:
            await ctx.interaction.response.send_modal(modal)
        elif hasattr(ctx, "send"):
            await ctx.send("Please use this command as a slash command for the best experience.")
        else:
            # Should not happen, but fallback
            pass

    @commands.command()
    async def ask(self, ctx: commands.Context, *, question: str = None):
        """
        Ask HomeworkAI a question (text or attach an image).
        """
        customer_id = await self.config.user(ctx.author).customer_id()
        if not customer_id:
            await ctx.send(
                "You need to set up a billing profile to use HomeworkAI. Please contact service support for assistance."
            )
            return

        openai_key = await self.get_openai_key()
        if not openai_key:
            await ctx.send("OpenAI API key is not configured. Please contact an administrator.")
            return

        # Check for image attachment
        image_url = None
        if ctx.message and ctx.message.attachments:
            for att in ctx.message.attachments:
                if att.content_type and att.content_type.startswith("image/"):
                    image_url = att.url
                    break

        if not question and not image_url:
            await ctx.send("Please provide a question or attach an image.")
            return

        await ctx.trigger_typing()
        try:
            headers = {
                "Authorization": f"Bearer {openai_key}",
                "Content-Type": "application/json"
            }
            if image_url:
                # Use OpenAI's vision endpoint (GPT-4V)
                payload = {
                    "model": "gpt-4-vision-preview",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": question or "Please analyze this image."},
                                {"type": "image_url", "image_url": {"url": image_url}}
                            ]
                        }
                    ],
                    "max_tokens": 512
                }
                endpoint = "https://api.openai.com/v1/chat/completions"
            else:
                # Use text endpoint
                payload = {
                    "model": "gpt-3.5-turbo",
                    "messages": [
                        {"role": "user", "content": question}
                    ],
                    "max_tokens": 512
                }
                endpoint = "https://api.openai.com/v1/chat/completions"

            async with aiohttp.ClientSession() as session:
                async with session.post(endpoint, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        await ctx.send(f"OpenAI API error: {resp.status}\n{text}")
                        return
                    data = await resp.json()
                    # Fix: Defensive check for response structure
                    answer = None
                    try:
                        answer = data["choices"][0]["message"]["content"]
                    except Exception:
                        await ctx.send("OpenAI API returned an unexpected response.")
                        return
                    await ctx.send(answer if len(answer) < 1900 else box(answer[:1900]))

        except Exception as e:
            await ctx.send(f"An error occurred while contacting OpenAI: {e}")

    @commands.command()
    @commands.is_owner()
    async def setcustomerid(self, ctx, user: discord.User, customer_id: str):
        """
        Set a user's customer ID (admin/owner only).
        """
        prev_id = await self.config.user(user).customer_id()
        await self.config.user(user).customer_id.set(customer_id)
        if not prev_id:
            # First time setting customer id, send welcome DM
            try:
                embed = discord.Embed(
                    title="Welcome to HomeworkAI!",
                    description=(
                        "You now have access to HomeworkAI.\n\n"
                        "**How to use:**\n"
                        "- Use the `ask` command in any server where HomeworkAI is enabled.\n"
                        "- You can ask questions by text or by attaching an image.\n\n"
                        f"To manage your billing or connect your payment method, visit: [Billing Portal]({self.billing_portal_url})"
                    ),
                    color=discord.Color.green()
                )
                await user.send(embed=embed)
            except Exception:
                pass
        await ctx.send(f"Customer ID for {user.mention} set to `{customer_id}`.")

    @commands.hybrid_command(name="billing", with_app_command=True)
    async def billing(self, ctx: commands.Context):
        """
        Get a link to your Stripe billing portal.
        """
        await ctx.defer(ephemeral=True)
        customer_id = await self.config.user(ctx.author).customer_id()
        if not customer_id:
            await ctx.send("You do not have a billing profile set up. Please contact support.", ephemeral=True)
            return

        stripe_key = await self.get_stripe_key()
        if not stripe_key:
            await ctx.send("Stripe API key is not configured. Please contact an administrator.", ephemeral=True)
            return

        # Stripe customer portal session creation
        portal_url = None
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {stripe_key}",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
                # Stripe expects form data, not JSON
                data = {
                    "customer": customer_id,
                    "return_url": "https://www.beehive.systems/billing"
                }
                async with session.post(
                    "https://api.stripe.com/v1/billing_portal/sessions",
                    data=data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        await ctx.send(f"Stripe API error: {resp.status}\n{text}", ephemeral=True)
                        return
                    result = await resp.json()
                    portal_url = result.get("url")
        except Exception as e:
            await ctx.send(f"An error occurred while contacting Stripe: {e}", ephemeral=True)
            return

        if portal_url:
            await ctx.send(f"Manage your billing here: {portal_url}", ephemeral=True)
        else:
            await ctx.send("Could not generate a billing portal link. Please contact support.", ephemeral=True)
