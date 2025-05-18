from .triage import Triage

async def setup(bot):
    await bot.add_cog(Triage(bot))
