from .schoolworkai import SchoolworkAI

async def setup(bot):
    await bot.add_cog(SchoolworkAI(bot))
