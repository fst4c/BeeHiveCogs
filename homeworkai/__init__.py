from .homeworkai import HomeworkAI

async def setup(bot):
    await bot.add_cog(HomeworkAI(bot))
