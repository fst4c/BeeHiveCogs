
from .pollshaddy import PollShaddy

async def setup(bot):
    await bot.add_cog(PollShaddy(bot))


