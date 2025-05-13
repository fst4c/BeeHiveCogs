from .antispam import AntiSpam

async def setup(bot):
    await bot.add_cog(AntiSpam(bot))

