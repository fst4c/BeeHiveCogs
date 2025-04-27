from .joinmonitor import JoinMonitor

async def setup(bot):
    await bot.add_cog(JoinMonitor(bot))


