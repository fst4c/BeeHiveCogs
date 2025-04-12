import discord
from redbot.core import commands, Config
import aiohttp
from datetime import datetime

class TwilioLookup(commands.Cog):
    """Cog to lookup phone numbers using the Twilio API and charge users via Stripe."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_user = {"customer_id": None}
        self.config.register_user(**default_user)
        self.twilio_error_codes = {
            60600: "Unprovisioned or out of coverage",
            21610: "The message has been blocked by the user.",
            21614: "The 'To' phone number is not a valid mobile number.",
            # Add more error codes and their descriptions as needed
        }

    async def _track_stripe_event(self, ctx, customer_id):
        stripe_tokens = await self.bot.get_shared_api_tokens("stripe")
        stripe_key = stripe_tokens.get("api_key") if stripe_tokens else None

        if stripe_key:
            stripe_url = "https://api.stripe.com/v1/billing/meter_events"
            stripe_headers = {
                "Authorization": f"Bearer {stripe_key}",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            stripe_payload = {
                "event_name": "phone-number-lookup",
                "timestamp": int(datetime.now().timestamp()),
                "payload[stripe_customer_id]": customer_id
            }
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.post(stripe_url, headers=stripe_headers, data=stripe_payload) as stripe_response:
                        if stripe_response.status != 200:
                            error_message = await stripe_response.text()
                            await ctx.send(f"Failed to track event with Stripe. Status code: {stripe_response.status}, Error: {error_message}", delete_after=10)
                except aiohttp.ClientError as e:
                    await ctx.send(f"Failed to connect to Stripe API: {str(e)}", delete_after=10)

    @commands.command(name="lookup")
    async def lookup_phone_number(self, ctx: commands.Context, phone_number: str):
        """
        Lookup any phone number in the world, billed at $0.50/lookup
        
        Formatting matters.

        :x: `+1(302)6002611`
        :white_check_mark: `+13026002611`
        :x: `+1 302 600-2611`
        
        """
        twilio_tokens = await self.bot.get_shared_api_tokens("twilio")
        twilio_account_sid = twilio_tokens.get("account_sid")
        twilio_auth_token = twilio_tokens.get("auth_token")

        if not twilio_account_sid or not twilio_auth_token:
            await ctx.send("Twilio API credentials are not set.", delete_after=10)
            return

        user_data = await self.config.user(ctx.author).all()
        customer_id = user_data.get("customer_id")

        if not customer_id:
            await ctx.send("There's no customer ID attached to your Discord profile. Talk to staff about getting onboarded.", delete_after=10)
            return

        twilio_url = f"https://lookups.twilio.com/v1/PhoneNumbers/{phone_number}?Type=carrier&Type=caller-name"
        auth = aiohttp.BasicAuth(twilio_account_sid, twilio_auth_token)

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(twilio_url, auth=auth) as response:
                    if response.status == 200:
                        data = await response.json()
                        carrier_info = data.get("carrier", {})
                        caller_name_info = data.get("caller_name") or {}
                        formatted_number = data.get("national_format", phone_number)

                        embed = discord.Embed(title="Phone number lookup", color=0xfffffe)
                        embed.add_field(name="Phone number", value=formatted_number, inline=False)
                        embed.add_field(name="Caller name", value=(caller_name_info.get("caller_name") or "Unknown").title(), inline=True)
                        embed.add_field(name="Caller type", value=(caller_name_info.get("caller_type") or "Unknown").title(), inline=True)
                        embed.add_field(name="Carrier name", value=carrier_info.get("name", "Unknown"), inline=True)
                        carrier_type = carrier_info.get("type", "Unknown")
                        embed.add_field(name="Carrier type", value=carrier_type.upper() if carrier_type else "Unknown", inline=True)
                        embed.add_field(name="Mobile country code", value=carrier_info.get("mobile_country_code", "Unknown"), inline=True)
                        embed.add_field(name="Mobile network code", value=carrier_info.get("mobile_network_code", "Unknown"), inline=True)
                        if carrier_info.get("error_code") is not None:
                            error_code = carrier_info.get("error_code")
                            error_description = self.twilio_error_codes.get(error_code, "Unknown error")
                            embed.add_field(name="Carrier error code", value=f"`{error_code}` - {error_description}", inline=True)
                        if caller_name_info.get("error_code") is not None:
                            error_code = caller_name_info.get("error_code")
                            error_description = self.twilio_error_codes.get(error_code, "Unknown error")
                            embed.add_field(name="Caller error code", value=f"`{error_code}` - {error_description}", inline=True)

                        await ctx.send(embed=embed)

                        # Track the event with Stripe
                        await self._track_stripe_event(ctx, customer_id)
                    else:
                        error_description = self.twilio_error_codes.get(response.status, "Unknown error")
                        await ctx.send(f"Failed to lookup phone number. Status code: {response.status} - {error_description}", delete_after=10)
            except aiohttp.ClientError as e:
                await ctx.send(f"Failed to connect to Twilio API: {str(e)}", delete_after=10)

    @commands.group(name="lookupset", invoke_without_command=True)
    @commands.is_owner()
    async def lookupset(self, ctx: commands.Context):
        """Manage customer-related settings."""
        await ctx.send_help(ctx.command)

    @lookupset.command(name="id")
    async def set_customer_id(self, ctx: commands.Context, user: discord.User, customer_id: str):
        """Set a customer's ID for a user."""
        await self.config.user(user).customer_id.set(customer_id)
        await ctx.send(f"Customer ID for {user.name} has been set.", delete_after=10)
        await ctx.message.delete()
