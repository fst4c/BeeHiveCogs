from .dynamicslowmode import DynamicSlowmode

async def setup(bot):
    await bot.add_cog(DynamicSlowmode(bot))
