# commands.py
from .views import RatingView

async def ask_command(self, ctx, question):
    customer_id = await self.config.user(ctx.author).customer_id()
    if not customer_id:
        embed = self._not_customer_embed()
        await ctx.send(embed=embed)
        return

    image_url = self._extract_image_url(ctx)
    await self._log_stripe_meter_event("ask", customer_id)
    if getattr(ctx, "guild", None):
        await self._increment_usage(ctx.guild, "ask")
    await self._send_schoolworkai_response(ctx, question, image_url, prompt_type="ask")

async def answer_command(self, ctx, question):
    customer_id = await self.config.user(ctx.author).customer_id()
    if not customer_id:
        embed = self._not_customer_embed()
        await ctx.send(embed=embed)
        return

    image_url = self._extract_image_url(ctx)
    await self._log_stripe_meter_event("answer", customer_id)
    if getattr(ctx, "guild", None):
        await self._increment_usage(ctx.guild, "answer")
    await self._send_schoolworkai_response(ctx, question, image_url, prompt_type="answer")

async def explain_command(self, ctx, question):
    customer_id = await self.config.user(ctx.author).customer_id()
    if not customer_id:
        embed = self._not_customer_embed()
        await ctx.send(embed=embed)
        return

    image_url = self._extract_image_url(ctx)
    await self._log_stripe_meter_event("explain", customer_id)
    if getattr(ctx, "guild", None):
        await self._increment_usage(ctx.guild, "explain")
    await self._send_schoolworkai_response(ctx, question, image_url, prompt_type="explain")

async def outline_command(self, ctx, paragraphcount, topic):
    customer_id = await self.config.user(ctx.author).customer_id()
    if not customer_id:
        embed = self._not_customer_embed()
        await ctx.send(embed=embed)
        return

    await self._log_stripe_meter_event("outline", customer_id)
    if getattr(ctx, "guild", None):
        await self._increment_usage(ctx.guild, "outline")
    question = f"Generate an outline for a paper on the topic '{topic}' with {paragraphcount} paragraphs."
    await self._send_schoolworkai_response(ctx, question, image_url=None, prompt_type="outline")

# --- Helper methods for this module ---

def _not_customer_embed(self):
    import discord
    return discord.Embed(
        title="You're not a SchoolworkAI user yet",
        description="Get started with </signup:1365562353076146206> to sign up and start getting answers.",
        color=0xff4545
    )

def _extract_image_url(self, ctx):
    attachments = []
    # Check for interaction attachments
    if hasattr(ctx, "interaction") and ctx.interaction is not None:
        data = getattr(ctx.interaction, "data", {})
        resolved = data.get("resolved", {}) if data else {}
        attachments = list(resolved.get("attachments", {}).values()) if resolved else []
    # Check for message attachments
    if not attachments and hasattr(ctx, "message") and ctx.message and hasattr(ctx.message, "attachments") and ctx.message.attachments:
        attachments = ctx.message.attachments
    for att in attachments:
        if isinstance(att, dict):
            content_type = att.get("content_type")
            url = att.get("url")
        else:
            content_type = getattr(att, "content_type", None)
            url = getattr(att, "url", None)
        if content_type and str(content_type).startswith("image/"):
            return url
    return None

async def _log_stripe_meter_event(self, event_name, customer_id):
    import time, aiohttp
    try:
        stripe_key = await self.get_stripe_key()
        if stripe_key and customer_id:
            timestamp = int(time.time())
            meter_url = "https://api.stripe.com/v1/billing/meter_events"
            data = {
                "event_name": event_name,
                "timestamp": timestamp,
                "payload[stripe_customer_id]": customer_id,
            }
            headers = {
                "Authorization": f"Bearer {stripe_key}",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            async with aiohttp.ClientSession() as session:
                try:
                    await session.post(meter_url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=10))
                except Exception:
                    pass  # Log or handle post error if needed
    except Exception:
        pass