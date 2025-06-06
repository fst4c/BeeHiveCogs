import csv
import os
import discord  #type: ignore
import asyncio  #type: ignore
import aiohttp #type: ignore
import tempfile
import datetime
from reportlab.lib.pagesizes import letter  # type: ignore
from reportlab.pdfgen import canvas  # type: ignore
from reportlab.lib import colors  # type: ignore
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # type: ignore
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle  # type: ignore
from redbot.core import commands, Config  # type: ignore
from discord.ui import Button, View  # type: ignore

class ReviewButton(discord.ui.Button):
    def __init__(self, label, review_id, style=discord.ButtonStyle.primary):
        super().__init__(label=label, style=style)
        self.review_id = review_id

    async def callback(self, interaction):
        cog = self.view.cog
        try:
            await cog.rate_review(interaction, self.review_id, int(self.label[0]))
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

class ReviewsCog(commands.Cog):
    """A cog for managing product or service reviews."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        default_guild = {
            "reviews": {},
            "review_channel": None,
            "next_id": 1
        }
        self.config.register_guild(**default_guild)

    async def rate_review(self, interaction, review_id, rating):
        async with self.config.guild(interaction.guild).reviews() as reviews:
            review = reviews.get(str(review_id))
            if review:
                review['rating'] = rating
                embed = discord.Embed(description=f"Thank you for rating the experience {rating} stars!", color=discord.Color.from_str("#2bbd8e"))
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                embed = discord.Embed(description="Review not found.", color=discord.Color(0xff4545))
                await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.guild_only()
    @commands.group(invoke_without_command=True)
    async def review(self, ctx):
        """Review commands."""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="Select what you'd like to do",
                description="Would you like to submit a review or get help with the review commands?",
                color=discord.Color.from_str("#fffffe")
            )
            view = View(timeout=180)  # Set a timeout for the view

            submit_button = Button(label=f"Add a review for {ctx.guild.name}", style=discord.ButtonStyle.green)
            help_button = Button(label="Show available commands", style=discord.ButtonStyle.grey)

            async def submit_button_callback(interaction):
                if interaction.user != ctx.author:
                    await interaction.response.send_message("You are not allowed to interact with this button.", ephemeral=True)
                    return
                await interaction.response.defer()
                await self.review_submit.callback(self, ctx)

            async def help_button_callback(interaction):
                if interaction.user != ctx.author:
                    await interaction.response.send_message("You are not allowed to interact with this button.", ephemeral=True)
                    return
                await interaction.response.defer()
                await ctx.send_help(str(ctx.command))

            submit_button.callback = submit_button_callback
            help_button.callback = help_button_callback

            view.add_item(submit_button)
            view.add_item(help_button)

            message = await ctx.send(embed=embed, view=view)
            await view.wait()  # Wait for the interaction to be completed

            if view.is_finished():
                await message.edit(view=None)  # Remove the buttons after the interaction is done

    @review.command(name="submit")
    async def review_submit(self, ctx):
        """Submit a review for approval."""
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        embed = discord.Embed(title=f"Leave a new review for {ctx.guild.name}", description=f"Thanks for wanting to submit feedback about your experience here! This should only take a couple seconds...\n\nReply in chat with your review message or type **`cancel`** to abandon the process.", color=discord.Color.from_str("#fffffe"))
        await ctx.send(embed=embed)
        try:
            msg = await self.bot.wait_for('message', check=check, timeout=120.0)
            if msg.content.lower() == 'cancel':
                embed = discord.Embed(description=":x: **Review process has been canceled**", color=discord.Color(0xff4545))
                await ctx.send(embed=embed)
                return
        except asyncio.TimeoutError:
            embed = discord.Embed(description=":x: **Timed out, you took too long to reply**", color=discord.Color(0xff4545))
            await ctx.send(embed=embed)
            return

        content = msg.content

        embed = discord.Embed(title="Please provide your email", description="Reply in chat with your email address or type **`cancel`** to abandon the process.", color=discord.Color.from_str("#fffffe"))
        await ctx.send(embed=embed)
        try:
            email_msg = await self.bot.wait_for('message', check=check, timeout=120.0)
            if email_msg.content.lower() == 'cancel':
                embed = discord.Embed(description=":x: **Review process has been canceled**", color=discord.Color(0xff4545))
                await ctx.send(embed=embed)
                return
        except asyncio.TimeoutError:
            embed = discord.Embed(description=":x: **Timed out, you took too long to reply**", color=discord.Color(0xff4545))
            await ctx.send(embed=embed)
            return

        email = email_msg.content

        review_id = await self.config.guild(ctx.guild).next_id()
        async with self.config.guild(ctx.guild).reviews() as reviews:
            reviews[str(review_id)] = {"author": ctx.author.id, "content": content, "status": "pending", "rating": None}

        await self.config.guild(ctx.guild).next_id.set(review_id + 1)

        view = View(timeout=None)
        colors = [discord.ButtonStyle.danger, discord.ButtonStyle.gray, discord.ButtonStyle.gray, discord.ButtonStyle.gray, discord.ButtonStyle.success]
        for i in range(1, 6):
            button = ReviewButton(label=f"{i} Star", review_id=review_id, style=colors[i-1])
            view.add_item(button)
        view.cog = self  # Assign the cog reference to the view for callback access

        embed = discord.Embed(description="Please rate your experience from 1 to 5 stars, where...\n\n- **1 star** indicates **poor** customer service, product quality, or overall experience\nand\n- **5 stars** indicates an **excellent** experience, **high** product quality, or **extremely helpful** customer service.", color=discord.Color.from_str("#fffffe"))
        message = await ctx.send(embed=embed, view=view)
        await view.wait()  # Wait for the interaction to be completed

        if not view.children:  # If the view has no children, the interaction was completed
            embed = discord.Embed(description="Thank you for submitting your review!", color=discord.Color.from_str("#2bbd8e"))
            await message.edit(embed=embed, view=None)

            # Check if the bot has a testimonialto API key set
            api_key = await self.bot.get_shared_api_tokens("testimonialto")
            if api_key:
                # Prepare the data to be sent
                data = {
                    "testimonial": content,
                    "rating": view.selected_rating,  # Assuming the rating is stored in view.selected_rating
                    "name": str(ctx.author),
                    "title": "Member of " + ctx.guild.name,
                    "email": email,
                    "avatarURL": ctx.author.display_avatar.url,
                    "attachedImageURL": ctx.guild.icon.url if ctx.guild.icon else "",
                    "confirm": True,
                    "isLiked": True
                }

                # Send the data to the testimonialto API
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        'https://api.testimonial.to/v1/submit/text',
                        headers={
                            'Authorization': f'Bearer {api_key}',
                            'Content-Type': 'application/json'
                        },
                        json=data
                    ) as response:
                        if response.status == 200:
                            print("Review submitted to testimonialto API successfully.")
                        else:
                            print(f"Failed to submit review to testimonialto API. Status code: {response.status}")
        else:
            embed = discord.Embed(description="Review rating was not received. Please try submitting again.", color=discord.Color(0xff4545))
            await message.edit(embed=embed, view=None)

    @review.command(name="approve")
    @commands.has_permissions(manage_guild=True)
    async def review_approve(self, ctx, review_id: int):
        """Approve a review."""
        async with self.config.guild(ctx.guild).reviews() as reviews:
            review = reviews.get(str(review_id))
            if review and review["status"] == "pending":
                review["status"] = "approved"
                embed = discord.Embed(description="The review has been approved.", color=discord.Color.from_str("#2bbd8e"))
                await ctx.send(embed=embed)
                review_channel_id = await self.config.guild(ctx.guild).review_channel()
                if review_channel_id:
                    review_channel = self.bot.get_channel(review_channel_id)
                    if review_channel:
                        star_rating = "⭐" * review['rating'] if review['rating'] else "No rating"
                        embed = discord.Embed(
                            title="New Review",
                            description=f"**Customer:** <@{review['author']}>\n**Rating:** {star_rating}\n\n**Testimonial:**\n{review['content']}",
                            color=discord.Color.from_str("#fffffe")
                        )
                        user = ctx.guild.get_member(review['author'])
                        if user:
                            embed.set_author(name=str(user), icon_url=user.display_avatar.url)
                        else:
                            embed.set_author(name="User not found")
                        embed.set_footer(text=f"User ID: {review['author']}")
                        embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else discord.Embed.Empty)
                        embed.timestamp = datetime.datetime.utcnow()
                        await review_channel.send(embed=embed)
                    else:
                        embed = discord.Embed(description=":x: **Review channel not found.**", color=discord.Color(0xff4545))
                        await ctx.send(embed=embed)
                else:
                    embed = discord.Embed(description=":x: **Review channel not set.**", color=discord.Color(0xff4545))
                    await ctx.send(embed=embed)
            else:
                embed = discord.Embed(description=":x: **This review has already been handled or does not exist.**", color=discord.Color(0xff4545))
                await ctx.send(embed=embed)

    @review.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def review_remove(self, ctx, review_id: int):
        """Remove a review."""
        async with self.config.guild(ctx.guild).reviews() as reviews:
            if str(review_id) in reviews:
                del reviews[str(review_id)]
                embed = discord.Embed(description="The review has been removed.", color=discord.Color.from_str("#2bbd8e"))
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(description="Review not found.", color=discord.Color(0xff4545))
                await ctx.send(embed=embed)

    @review.command(name="export")
    @commands.has_permissions(manage_guild=True)
    async def review_export(self, ctx, file_format: str):
        """Export reviews to a CSV or PDF file."""
        if file_format.lower() not in ["csv", "pdf"]:
            await ctx.send("Please specify the file format as either 'csv' or 'pdf'.")
            return

        reviews = await self.config.guild(ctx.guild).reviews()
        file_name = f"reviews_{ctx.guild.id}.{file_format.lower()}"
        file_path = os.path.join(tempfile.gettempdir(), file_name)

        try:
            if file_format.lower() == "csv":
                if "../" in file_name or "..\\" in file_name:
                    raise Exception("Invalid file path")
                with open(file_path, "w", newline='', encoding='utf-8') as file:
                    writer = csv.writer(file)
                    writer.writerow(["ID", "Author ID", "Content", "Status", "Rating"])
                    for review_id, review in reviews.items():
                        writer.writerow([review_id, review["author"], review["content"], review["status"], review.get("rating", "Not rated")])
                await ctx.send(file=discord.File(file_path))
            elif file_format.lower() == "pdf":
                doc = SimpleDocTemplate(file_path, pagesize=letter)
                styles = getSampleStyleSheet()
                # Ensure the font name is a standard font available in ReportLab, such as 'Helvetica'
                styles.add(ParagraphStyle(name='Normal-Bold', fontName='Helvetica-Bold', fontSize=12, leading=14))
                flowables = []

                flowables.append(Paragraph("Guild Reviews", styles['Normal-Bold']))
                flowables.append(Spacer(1, 12))

                data = [["ID", "Author ID", "Content", "Status", "Rating"]]
                for review_id, review in reviews.items():
                    data.append([review_id, review["author"], review["content"], review["status"], review.get("rating", "Not rated")])

                t = Table(data)
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    # Use the standard font name here as well
                    ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.blue),
                ]))
                flowables.append(t)

                doc.build(flowables)
                await ctx.send(file=discord.File(file_path))
        except PermissionError:
            await ctx.send("I do not have permission to write to the file system.")
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

    @review.command(name="setchannel")
    @commands.has_permissions(manage_guild=True)
    async def review_setchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel where approved reviews will be posted."""
        await self.config.guild(ctx.guild).review_channel.set(channel.id)
        embed = discord.Embed(description=f"Review channel has been set to {channel.mention}.", color=discord.Color.from_str("#2bbd8e"))
        await ctx.send(embed=embed)

    @review.command(name="list")
    @commands.has_permissions(manage_guild=True)
    async def review_list(self, ctx):
        """List all reviews."""
        reviews = await self.config.guild(ctx.guild).reviews()
        if not reviews:
            embed = discord.Embed(description="There are no reviews to list.", color=discord.Color(0xff4545))
            await ctx.send(embed=embed)
            return

        for review_id, review in reviews.items():
            status = "Approved" if review["status"] == "approved" else "Pending"
            embed = discord.Embed(title=f"Review ID: {review_id}", color=discord.Color.from_str("#fffffe"))
            embed.add_field(name="Status", value=status, inline=False)
            content_preview = review['content'][:100] + "..." if len(review['content']) > 100 else review['content']
            embed.add_field(name="Content", value=content_preview, inline=False)
            rating = review.get('rating', 'Not rated')
            embed.add_field(name="Rating", value=rating, inline=False)
            await ctx.send(embed=embed)


