import discord
from redbot.core import commands, Config, checks, bank
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate

import aiohttp
import asyncio

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
            "phone_number": None,
            "phone_verified": False,
        }
        default_guild = {
            "applications_channel": None,
        }
        self.config.register_user(**default_user)
        self.config.register_guild(**default_guild)
        self.billing_portal_url = "https://www.beehive.systems/billing"  # Example link

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

    @commands.group()
    @commands.guild_only()
    async def homeworkai(self, ctx):
        """HomeworkAI configuration commands."""

    @homeworkai.command()
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

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command: discord.app_commands.Command):
        # This is a placeholder in case you want to handle app command completions
        pass

    @discord.app_commands.command(name="onboard", description="Apply to use HomeworkAI.")
    async def onboard(self, interaction: discord.Interaction):
        """
        Slash command: Apply to use HomeworkAI.
        Collects info via DM prompt-by-prompt, including phone number verification via Twilio.
        """
        user = interaction.user
        if await self.config.user(user).applied():
            embed = discord.Embed(
                title="Already Applied",
                description="You have already applied to use HomeworkAI. Please wait for approval.",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

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

        questions = [
            ("first_name", "What is your **first name**?"),
            ("last_name", "What is your **last name**?"),
            ("billing_email", "What is your **billing email address**? (This will be used for billing and notifications)"),
        ]
        answers = {}

        try:
            await dm_channel.send(
                embed=discord.Embed(
                    title="HomeworkAI Application",
                    description=(
                        "Welcome! Let's get you set up to use HomeworkAI.\n"
                        "Please answer the following questions. You can type `cancel` at any time to stop the application."
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
            import re
            phone_pattern = re.compile(r"^\+?[1-9]\d{1,14}$")  # E.164 format

            while True:
                await dm_channel.send(
                    "Please enter your **mobile phone number** in international format (e.g., `+12345678901`). This will be used for verification and important notifications."
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
                    # Bug: get_shared_api_tokens is not awaitable, but in original code it's called as self.bot.get_shared_api_tokens("twilio").get("verify_sid")
                    # This will not work if get_shared_api_tokens is a coroutine (Red 3.5+). Fix: await it.
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
                    "A verification code has been sent to your phone. Please enter the code you received (or type `cancel` to stop)."
                )
                for attempt in range(3):
                    try:
                        code_msg = await self.bot.wait_for("message", check=check, timeout=120)
                    except asyncio.TimeoutError:
                        await dm_channel.send("You took too long to respond. Application cancelled.")
                        return
                    if code_msg.content.lower().strip() == "cancel":
                        await dm_channel.send("Application cancelled.")
                        return
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
                                result = await resp.json()
                                if resp.status in (200, 201) and result.get("status") == "approved":
                                    await dm_channel.send(
                                        embed=discord.Embed(
                                            title="Phone Verified",
                                            description="Your phone number has been verified successfully.",
                                            color=discord.Color.green()
                                        )
                                    )
                                    answers["phone_number"] = phone_number
                                    break
                                else:
                                    await dm_channel.send(
                                        embed=discord.Embed(
                                            title="Verification Failed",
                                            description="The code you entered is incorrect. Please try again.",
                                            color=discord.Color.red()
                                        )
                                    )
                                    if attempt == 2:
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
                        if attempt == 2:
                            await dm_channel.send("Too many failed attempts. Application cancelled.")
                            return
                        continue
                else:
                    continue  # If not broken out of, ask for phone again
                break  # Phone verified, break out of phone loop

            # Send application to applications channel
            guild = interaction.guild
            channel_id = await self.config.guild(guild).applications_channel()
            channel = guild.get_channel(channel_id) if channel_id else None
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
                title="New HomeworkAI Application",
                color=discord.Color.blurple(),
            )
            embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=False)
            embed.add_field(name="First Name", value=answers["first_name"], inline=True)
            embed.add_field(name="Last Name", value=answers["last_name"], inline=True)
            embed.add_field(name="Billing Email", value=answers["billing_email"], inline=False)
            embed.add_field(name="Phone Number", value=answers["phone_number"], inline=False)
            await channel.send(embed=embed)
            await self.config.user(user).applied.set(True)
            await self.config.user(user).phone_number.set(answers["phone_number"])
            await self.config.user(user).phone_verified.set(True)
            await dm_channel.send(
                embed=discord.Embed(
                    title="Application Submitted",
                    description="Your application has been submitted! We'll be in touch soon.",
                    color=discord.Color.green()
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

    @commands.command()
    async def ask(self, ctx: commands.Context, *, question: str = None):
        """
        Ask HomeworkAI a question (text or attach an image).
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

        openai_key = await self.get_openai_key()
        if not openai_key:
            embed = discord.Embed(
                title="OpenAI API Key Not Configured",
                description="OpenAI API key is not configured. Please contact an administrator.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        image_url = None
        if ctx.message and ctx.message.attachments:
            for att in ctx.message.attachments:
                # Bug: att.content_type may be None, so check for that
                if getattr(att, "content_type", None) and att.content_type.startswith("image/"):
                    image_url = att.url
                    break

        if not question and not image_url:
            embed = discord.Embed(
                title="No Question or Image Provided",
                description="Please provide a question or attach an image.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        await ctx.trigger_typing()
        try:
            headers = {
                "Authorization": f"Bearer {openai_key}",
                "Content-Type": "application/json"
            }
            if image_url:
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
                    # Use embed for the answer, truncate if needed
                    if len(answer) < 1900:
                        embed = discord.Embed(
                            title="HomeworkAI Answer",
                            description=answer,
                            color=discord.Color.blurple()
                        )
                        await ctx.send(embed=embed)
                    else:
                        embed = discord.Embed(
                            title="HomeworkAI Answer (truncated)",
                            description=answer[:1900] + "\n\n*Response truncated.*",
                            color=discord.Color.blurple()
                        )
                        await ctx.send(embed=embed)

        except Exception as e:
            embed = discord.Embed(
                title="OpenAI Error",
                description=f"An error occurred while contacting OpenAI: {e}",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

    @commands.command()
    @commands.is_owner()
    async def setcustomerid(self, ctx, user: discord.User, customer_id: str):
        """
        Set a user's customer ID (admin/owner only).
        """
        prev_id = await self.config.user(user).customer_id()
        await self.config.user(user).customer_id.set(customer_id)
        if not prev_id:
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
        embed = discord.Embed(
            title="Customer ID Set",
            description=f"Customer ID for {user.mention} set to `{customer_id}`.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="billing", with_app_command=True)
    async def billing(self, ctx: commands.Context):
        """
        Get a link to your Stripe billing portal.
        """
        await ctx.defer(ephemeral=True)
        customer_id = await self.config.user(ctx.author).customer_id()
        if not customer_id:
            embed = discord.Embed(
                title="No Billing Profile",
                description="You do not have a billing profile set up. Please contact support.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, ephemeral=True)
            return

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
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {stripe_key}",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
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
                        embed = discord.Embed(
                            title="Stripe API Error",
                            description=f"Status: {resp.status}\n{text}",
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=embed, ephemeral=True)
                        return
                    result = await resp.json()
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
                title="Billing Portal",
                description=f"[Manage your billing here]({portal_url})",
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
