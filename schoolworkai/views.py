import discord

class RatingView(discord.ui.View):
    def __init__(self, cog, ctx, prompt_type, guild_id, *, timeout=120):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.prompt_type = prompt_type
        self.guild_id = guild_id
        self.upvoted = False
        self.downvoted = False

    @discord.ui.button(label="👍", style=discord.ButtonStyle.success, custom_id="schoolworkai_upvote")
    async def upvote(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.upvoted:
            await interaction.response.send_message("You already upvoted this answer.", ephemeral=True)
            return
        self.upvoted = True
        if self.guild_id:
            await self.cog._increment_stat(self.guild_id, "upvotes")
            guild = self.cog.bot.get_guild(self.guild_id)
            if guild:
                await self.cog._update_stats_channel(guild)
        await interaction.response.send_message("Thank you for your feedback! 👍", ephemeral=True)

    @discord.ui.button(label="👎", style=discord.ButtonStyle.danger, custom_id="schoolworkai_downvote")
    async def downvote(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.downvoted:
            await interaction.response.send_message("You already downvoted this answer.", ephemeral=True)
            return
        self.downvoted = True
        if self.guild_id:
            await self.cog._increment_stat(self.guild_id, "downvotes")
            guild = self.cog.bot.get_guild(self.guild_id)
            if guild:
                await self.cog._update_stats_channel(guild)
        await interaction.response.send_message("Thank you for your feedback! 👎", ephemeral=True)

class ApplicationActionView(discord.ui.View):
    def __init__(self, cog, user: discord.User, answers: dict, *, timeout=600):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.user = user
        self.answers = answers
        self.message = None

    @discord.ui.button(label="Allow", style=discord.ButtonStyle.success, custom_id="schoolworkai_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ... (copy the logic from your main cog here, using self.cog, self.user, etc.) ...
        pass

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="schoolworkai_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ... (copy the logic from your main cog here, using self.cog, self.user, etc.) ...
        pass