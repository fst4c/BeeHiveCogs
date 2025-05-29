from .staffmonitor import StaffMonitor

async def setup(bot):
    await bot.add_cog(StaffMonitor(bot))
