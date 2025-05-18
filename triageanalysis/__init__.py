from .triageanalysis import TriageAnalysis
import triage.client
from triage.__version__ import __version__

Client = triage.client.Client

async def setup(bot):
    await bot.add_cog(TriageAnalysis(bot))
