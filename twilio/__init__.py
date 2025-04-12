from .twilio import TwilioLookup

async def setup(bot):
    cog = TwilioLookup(bot)
    await bot.add_cog(cog)
