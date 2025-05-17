from redbot.core.bot import Red

from .linksafety import LinkSafety


async def setup(bot: Red):
    cog = LinkSafety(bot)
    await bot.add_cog(cog)


__red_end_user_data_statement__ = "This cog does not store any end user data."
