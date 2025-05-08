from redbot.core import commands, Config, checks
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta
import os
import tempfile
import asyncio
from collections import Counter, defaultdict
import random
import string

class ReportsPro(commands.Cog):
    """Cog to handle global user reports with improved functionality"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        default_guild = {
            "reports_channel": None,
            "reports": {},
            "mention_role": None,
            "auto_cleanup_days": 30,
            "auto_cleanup_enabled": False,
        }
        self.config.register_guild(**default_guild)
        # Register the context menu
        self.report_sender_context = app_commands.ContextMenu(
            name="Report sender", callback=self.report_sender_context_menu
        )
        try:
            self.bot.tree.add_command(self.report_sender_context)
        except Exception:
            # In case the tree is not ready yet, will be added in cog_load
            pass

    async def cog_load(self):
        # Ensure context menu is registered on cog load
        try:
            self.bot.tree.add_command(self.report_sender_context)
        except Exception:
            pass

    # --- Settings Commands ---

    @commands.guild_only()
    @commands.group(name="reportset", invoke_without_command=True)
    @checks.admin_or_permissions()
    async def reportset(self, ctx):
        """Group command for report settings."""
        await ctx.send_help(ctx.command)

    @reportset.command(name="channel")
    async def set_report_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel where reports will be sent."""
        await self.config.guild(ctx.guild).reports_channel.set(channel.id)
        embed = discord.Embed(
            title="Reports Channel Set",
            description=f"The reports will now be sent to {channel.mention}.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @reportset.command(name="mention")
    async def set_mention_role(self, ctx, role: discord.Role = None):
        """
        Set the role to be mentioned when new reports are sent.
        If no role is provided, the mention role will be cleared.
        """
        if role is None:
            await self.config.guild(ctx.guild).mention_role.set(None)
            embed = discord.Embed(
                title="Mention Role Cleared",
                description="The mention role for new reports has been cleared.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
        else:
            await self.config.guild(ctx.guild).mention_role.set(role.id)
            embed = discord.Embed(
                title="Mention Role Set",
                description=f"The role {role.mention} will be notified for new reports.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)

    @reportset.command(name="autocleanup")
    async def set_auto_cleanup(self, ctx, days: int = 30, enabled: bool = True):
        """
        Set automatic cleanup of old reports.
        Usage: [p]reportset autocleanup <days> <enabled>
        """
        await self.config.guild(ctx.guild).auto_cleanup_days.set(days)
        await self.config.guild(ctx.guild).auto_cleanup_enabled.set(enabled)
        embed = discord.Embed(
            title="Auto Cleanup Settings",
            description=f"Auto cleanup is now {'enabled' if enabled else 'disabled'} for reports older than {days} days.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @reportset.command(name="view")
    async def view_settings(self, ctx):
        """View the current settings for the guild."""
        reports_channel_id = await self.config.guild(ctx.guild).reports_channel()
        reports_channel = ctx.guild.get_channel(reports_channel_id) if reports_channel_id else None
        channel_mention = reports_channel.mention if reports_channel else "Not Set"

        mention_role_id = await self.config.guild(ctx.guild).mention_role()
        mention_role = ctx.guild.get_role(mention_role_id) if mention_role_id else None
        role_mention = mention_role.mention if mention_role else "Not Set"

        auto_cleanup_days = await self.config.guild(ctx.guild).auto_cleanup_days()
        auto_cleanup_enabled = await self.config.guild(ctx.guild).auto_cleanup_enabled()

        embed = discord.Embed(title="Current Reporting Settings", color=discord.Color.from_rgb(255, 255, 254))
        embed.add_field(name="Log Channel", value=channel_mention, inline=False)
        embed.add_field(name="Mention Role", value=role_mention, inline=False)
        embed.add_field(name="Auto Cleanup", value=f"{'Enabled' if auto_cleanup_enabled else 'Disabled'} ({auto_cleanup_days} days)", inline=False)

        try:
            await ctx.send(embed=embed)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Permission Error",
                description="I can't send messages in this channel. Please check my permissions.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

    # --- Reporting Context Menu Command ---

    async def report_sender_context_menu(self, interaction: discord.Interaction, message: discord.Message):
        """Context menu to report the sender of a message."""
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        member = message.author
        if member == interaction.user:
            embed = discord.Embed(
                title="Error",
                description="You cannot report yourself.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        reports_channel_id = await self.config.guild(guild).reports_channel()
        if not reports_channel_id:
            embed = discord.Embed(
                title="Error",
                description="The reports channel hasn't been set up yet. Please contact an admin.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        reports_channel = guild.get_channel(reports_channel_id)
        if not reports_channel:
            embed = discord.Embed(
                title="Error",
                description="I can't access the reports channel. Please contact an admin.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Create an embed with report types
        report_embed = discord.Embed(
            title=f"Report a User to the Moderators of {guild.name}",
            color=discord.Color.from_rgb(255, 69, 69),
            description=f"**You're reporting {member.mention} ({member.id})**\n\n"
                        f"Please choose a reason for the report from the dropdown below."
        )

        # Define report reasons with descriptions and emojis
        report_reasons = [
            ("Harassment", "Unwanted behavior that causes distress or discomfort."),
            ("Spam", "Repeated or irrelevant messages disrupting the chat."),
            ("Advertising", "Unwanted or non-consensual advertising or promotion."),
            ("Inappropriate Content", "Content that is offensive or not suitable for the community."),
            ("Impersonation", "Pretending to be someone else without permission."),
            ("Hate Speech", "Speech that attacks or discriminates against a group."),
            ("Terms of Service", "Actions that violate Discord's Terms of Service."),
            ("Community Guidelines", "Actions that violate Discord's Community Guidelines."),
            ("Other", "Any other reason not listed but reasonably applicable.")
        ]

        # --- FIXED: Modal/Dropdown logic for report submission ---
        # The main issue is that the Modal's on_submit is not properly chained to the dropdown's logic,
        # and the staticmethod finish_report_static is not being called with the correct context.
        # We'll refactor the dropdown and modal logic to ensure the report is submitted correctly.

        class ReportDropdown(discord.ui.Select):
            def __init__(self, config, interaction, member, reports_channel, capture_chat_history, message):
                self.config = config
                self.interaction = interaction
                self.member = member
                self.reports_channel = reports_channel
                self.capture_chat_history = capture_chat_history
                self.allowed_user_id = interaction.user.id  # Only allow the command invoker
                self.message = message
                options = [
                    discord.SelectOption(label=reason, description=description)
                    for reason, description in report_reasons
                ]
                super().__init__(placeholder="Choose a report reason...", min_values=1, max_values=1, options=options)

            async def callback(self, interaction: discord.Interaction):
                # Only allow the user who invoked the command to use the dropdown
                if interaction.user.id != self.allowed_user_id:
                    embed = discord.Embed(
                        title="Not for you!",
                        description="Only the user who started this report can select a reason.",
                        color=discord.Color.red()
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return

                selected_reason = self.values[0]
                selected_description = next(description for reason, description in report_reasons if reason == selected_reason)

                # If "Other", show a modal and handle submission
                if selected_reason == "Other":
                    # We need to pass all context to the modal so it can call finish_report after submission
                    parent_dropdown = self

                    class OtherModal(discord.ui.Modal, title="Additional Details"):
                        details = discord.ui.TextInput(
                            label="Please describe the reason for your report:",
                            style=discord.TextStyle.long,
                            required=True,
                            max_length=500,
                        )

                        def __init__(self):
                            super().__init__()

                        async def on_submit(self, modal_interaction: discord.Interaction):
                            # Call finish_report_static with the details from the modal
                            extra_details = self.details.value
                            # Compose the description for "Other"
                            full_description = selected_description + f"\n\nUser details: {extra_details}"
                            await ReportDropdown.finish_report_static(
                                modal_interaction,
                                parent_dropdown,
                                parent_dropdown.config,
                                parent_dropdown.interaction,
                                parent_dropdown.member,
                                parent_dropdown.reports_channel,
                                parent_dropdown.capture_chat_history,
                                parent_dropdown.message,
                                selected_reason,
                                full_description,
                                extra_details
                            )

                    modal = OtherModal()
                    await interaction.response.send_modal(modal)
                    return

                # If not "Other", finish the report directly
                await self.finish_report(interaction)

            async def finish_report(self, interaction):
                selected_reason = self.values[0]
                selected_description = next(description for reason, description in report_reasons if reason == selected_reason)
                await ReportDropdown.finish_report_static(
                    interaction,
                    self,
                    self.config,
                    self.interaction,
                    self.member,
                    self.reports_channel,
                    self.capture_chat_history,
                    self.message,
                    selected_reason,
                    selected_description,
                    ""
                )

            @staticmethod
            async def finish_report_static(
                interaction, self_ref, config, orig_interaction, member, reports_channel,
                capture_chat_history, message, selected_reason, selected_description, extra_details
            ):
                # Generate a unique report ID
                try:
                    reports = await config.guild(interaction.guild).reports()
                except Exception as e:
                    embed = discord.Embed(
                        title="Error",
                        description=f"Could not access reports config: {e}",
                        color=discord.Color.red()
                    )
                    try:
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                    except Exception:
                        pass
                    return

                report_id = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
                attempts = 0
                while report_id in reports and attempts < 10000:
                    report_id = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
                    attempts += 1
                if report_id in reports:
                    embed = discord.Embed(
                        title="Error",
                        description="Could not generate a unique report ID. Please try again.",
                        color=discord.Color.red()
                    )
                    try:
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                    except Exception:
                        pass
                    return

                # Store the report in the config
                try:
                    reports[report_id] = {
                        "reported_user": str(member.id),
                        "reporter": str(orig_interaction.user.id),
                        "reason": selected_reason,
                        "description": selected_description,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": "Open",
                        "details": extra_details,
                        "reported_message_id": str(message.id),
                        "reported_message_link": message.jump_url,
                        "reported_message_content": message.content,
                    }
                    await config.guild(interaction.guild).reports.set(reports)
                except Exception as e:
                    embed = discord.Embed(
                        title="Error",
                        description=f"Something went wrong while saving the report: {e}",
                        color=discord.Color.red()
                    )
                    try:
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                    except Exception:
                        pass
                    return

                # Capture chat history
                try:
                    chat_history = await capture_chat_history(interaction.guild, member)
                except Exception as e:
                    embed = discord.Embed(
                        title="Error",
                        description=f"Something went wrong while capturing chat history: {e}",
                        color=discord.Color.red()
                    )
                    try:
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                    except Exception:
                        pass
                    return

                # Count existing reports against the user by reason
                reason_counts = Counter(report['reason'] for report in reports.values() if report['reported_user'] == str(member.id))

                # Send the report to the reports channel
                if reports_channel:
                    report_message = discord.Embed(
                        title="A new report was filed",
                        color=0xff4545
                    )
                    report_message.add_field(name="Report ID", value=f"```{report_id}```", inline=False)
                    report_message.add_field(name="Offender", value=f"{member.mention}", inline=True)
                    report_message.add_field(name="Offender ID", value=f"`{member.id}`", inline=True)
                    report_message.add_field(name="Date", value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:D>", inline=True)
                    report_message.add_field(name="Reporter", value=orig_interaction.user.mention, inline=True)
                    report_message.add_field(name="Reporter ID", value=f"`{orig_interaction.user.id}`", inline=True)
                    report_message.add_field(name="Time", value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:R>", inline=True)
                    report_message.add_field(name="Reason", value=f"**{selected_reason}**\n*{selected_description}*", inline=False)
                    report_message.add_field(name="Reported Message", value=f"[Jump to message]({message.jump_url})", inline=False)
                    if message.content:
                        report_message.add_field(name="Message Content", value=message.content[:1024], inline=False)

                    # Add a summary of existing report counts by reason
                    if reason_counts:
                        summary = "\n".join(f"**{reason}** x**{count}**" for reason, count in reason_counts.items())
                        report_message.add_field(name="Previous reports", value=summary, inline=False)

                    mention_role_id = await config.guild(interaction.guild).mention_role()
                    mention_role = interaction.guild.get_role(mention_role_id) if mention_role_id else None
                    mention_text = mention_role.mention if mention_role else ""

                    try:
                        await reports_channel.send(content=mention_text, embed=report_message, allowed_mentions=discord.AllowedMentions(roles=True))
                        if chat_history:
                            await reports_channel.send(file=discord.File(chat_history, filename=f"{member.id}_chat_history.txt"))
                            os.remove(chat_history)  # Clean up the file after sending
                    except discord.Forbidden:
                        embed = discord.Embed(
                            title="Permission error",
                            description="I can't send messages in the reports channel. Please check my permissions.",
                            color=discord.Color.red()
                        )
                        try:
                            await interaction.response.send_message(embed=embed, ephemeral=True)
                        except Exception:
                            pass
                        return
                    except Exception as e:
                        embed = discord.Embed(
                            title="Error",
                            description=f"Something went wrong while sending the report: {e}",
                            color=discord.Color.red()
                        )
                        try:
                            await interaction.response.send_message(embed=embed, ephemeral=True)
                        except Exception:
                            pass
                        return
                else:
                    embed = discord.Embed(
                        title="Error",
                        description="I can't access the reports channel. Please contact an admin.",
                        color=discord.Color.red()
                    )
                    try:
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                    except Exception:
                        pass
                    return

                # Update the original ephemeral message with the thank you message and remove the view
                thank_you_embed = discord.Embed(
                    description="Your report has been submitted. Thank you for helping to keep this community safe. Our moderators will review your report as soon as possible.",
                    color=0x2bbd8e
                )
                # --- PATCH: Always complete the interaction to avoid "This interaction failed" ---
                try:
                    # Try to edit the original ephemeral message if possible
                    if hasattr(interaction, "response") and hasattr(interaction.response, "is_done") and interaction.response.is_done():
                        await interaction.followup.send(embed=thank_you_embed, ephemeral=True)
                    else:
                        try:
                            await interaction.response.edit_message(embed=thank_you_embed, view=None)
                        except Exception:
                            # If edit_message fails (e.g. not the original message), just send a followup
                            await interaction.followup.send(embed=thank_you_embed, ephemeral=True)
                except Exception:
                    # As a last resort, just acknowledge the interaction in some way
                    try:
                        if hasattr(interaction, "response") and not interaction.response.is_done():
                            await interaction.response.send_message(embed=thank_you_embed, ephemeral=True)
                    except Exception:
                        pass

                # Disable the dropdown after use to prevent "This interaction failed"
                if hasattr(self_ref, "disabled"):
                    self_ref.disabled = True
                if hasattr(self_ref, "view") and self_ref.view:
                    for item in self_ref.view.children:
                        item.disabled = True
                    try:
                        if hasattr(interaction, "message") and interaction.message:
                            await interaction.message.edit(view=self_ref.view)
                    except Exception:
                        pass

        # Create a view and add the dropdown
        class ReportView(discord.ui.View):
            def __init__(self, config, interaction, member, reports_channel, capture_chat_history, message):
                super().__init__(timeout=180)
                self.add_item(ReportDropdown(config, interaction, member, reports_channel, capture_chat_history, message))

        view = ReportView(self.config, interaction, member, reports_channel, self.capture_chat_history, message)

        try:
            await interaction.response.send_message(embed=report_embed, view=view, ephemeral=True)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Permission Error",
                description="I can't send messages in this channel. Please check my permissions.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            embed = discord.Embed(
                title="Error",
                description=f"Something went wrong while sending the report embed: {e}",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def capture_chat_history(self, guild, member, limit_per_channel=200):
        """Capture the chat history of a member across all channels."""
        chat_history = []
        for channel in guild.text_channels:
            try:
                async for message in channel.history(limit=limit_per_channel, oldest_first=False):
                    if message.author.id == member.id:
                        content = message.content
                        if message.attachments:
                            content += " [Attachments: " + ", ".join(a.url for a in message.attachments) + "]"
                        chat_history.append(f"[{message.created_at}] #{channel.name} {message.author}: {content}")
            except discord.Forbidden:
                continue
            except Exception as e:
                print(f"An error occurred while accessing channel {channel.name}: {e}")
                continue
        if chat_history:
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{member.id}_chat_history.txt", mode='w', encoding='utf-8') as temp_file:
                    temp_file.write("\n".join(chat_history))
                    return temp_file.name
            except Exception as e:
                print(f"An error occurred while writing chat history to file: {e}")
                return None
        return None

    # --- Reports Management Commands ---

    @commands.guild_only()
    @commands.group(name="reports", invoke_without_command=True)
    @checks.admin_or_permissions()
    async def reports(self, ctx):
        """Group command for managing reports."""
        await ctx.send_help(ctx.command)

    @reports.command(name="view")
    async def view_reports(self, ctx, member: discord.Member = None, status: str = None):
        """
        View all reports in the guild, reports for a specific user, or by status.
        Usage: [p]reports view [member] [status]
        """
        reports = await self.config.guild(ctx.guild).reports()
        if not reports:
            embed = discord.Embed(
                title="No Reports",
                description="There are no reports in this guild.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        # Filter reports for a specific member if provided
        filtered_reports = [
            (report_id, report_info) for report_id, report_info in reports.items()
            if (not member or (str(report_info['reported_user']) == str(member.id)))
            and (not status or report_info.get("status", "Open").lower() == status.lower())
        ]

        if not filtered_reports:
            embed = discord.Embed(
                title="No Reports",
                description="There are no reports for the specified user or status.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        # Sort by timestamp descending
        filtered_reports.sort(key=lambda x: x[1].get("timestamp", ""), reverse=True)

        # Create a list of embeds, one for each report
        embeds = []
        for report_id, report_info in filtered_reports:
            reported_user = ctx.guild.get_member(int(report_info['reported_user'])) if report_info.get('reported_user') else None
            reporter = ctx.guild.get_member(int(report_info['reporter'])) if report_info.get('reporter') else None

            embed = discord.Embed(title=f"Report {report_id}", color=discord.Color.from_rgb(255, 255, 254))
            embed.add_field(
                name="Reported User",
                value=reported_user.mention if reported_user else f"Unknown User ({report_info.get('reported_user', 'N/A')})",
                inline=False
            )
            embed.add_field(
                name="Reported By",
                value=reporter.mention if reporter else f"Unknown Reporter ({report_info.get('reporter', 'N/A')})",
                inline=False
            )
            embed.add_field(
                name="Reason",
                value=f"{report_info.get('reason', 'Unknown')}: {report_info.get('description', 'No description available')}",
                inline=False
            )
            # Defensive: handle missing or malformed timestamp
            try:
                ts = report_info.get('timestamp', '1970-01-01T00:00:00+00:00')
                dt = datetime.fromisoformat(ts)
                unix_ts = int(dt.timestamp())
                time_str = f"<t:{unix_ts}:R>"
            except Exception:
                time_str = "Unknown"
            embed.add_field(
                name="Timestamp",
                value=time_str,
                inline=False
            )
            embed.add_field(
                name="Status",
                value=report_info.get("status", "Open"),
                inline=False
            )
            if report_info.get("details"):
                embed.add_field(
                    name="Extra Details",
                    value=report_info["details"],
                    inline=False
                )
            if report_info.get("reported_message_link"):
                embed.add_field(
                    name="Reported Message",
                    value=f"[Jump to message]({report_info['reported_message_link']})",
                    inline=False
                )
            if report_info.get("reported_message_content"):
                embed.add_field(
                    name="Message Content",
                    value=report_info["reported_message_content"][:1024],
                    inline=False
                )
            embeds.append(embed)

        # Function to handle pagination
        async def send_paginated_embeds(ctx, embeds):
            current_page = 0
            message = await ctx.send(embed=embeds[current_page])

            # Add reaction controls
            if len(embeds) > 1:
                await message.add_reaction("⬅️")
                await message.add_reaction("➡️")
            await message.add_reaction("❌")  # Add close emoji

            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ["⬅️", "➡️", "❌"] and reaction.message.id == message.id

            while True:
                try:
                    reaction, user = await self.bot.wait_for('reaction_add', timeout=120.0, check=check)

                    if str(reaction.emoji) == "➡️" and current_page < len(embeds) - 1:
                        current_page += 1
                        await message.edit(embed=embeds[current_page])
                    elif str(reaction.emoji) == "⬅️" and current_page > 0:
                        current_page -= 1
                        await message.edit(embed=embeds[current_page])
                    elif str(reaction.emoji) == "❌":
                        await message.delete()
                        break

                    try:
                        await message.remove_reaction(reaction, user)
                    except Exception:
                        pass
                except asyncio.TimeoutError:
                    try:
                        await message.clear_reactions()
                    except Exception:
                        pass
                    break

        await send_paginated_embeds(ctx, embeds)

    @reports.command(name="clear")
    @checks.admin_or_permissions(manage_guild=True)
    async def clear_reports(self, ctx):
        """Clear all reports in the guild."""
        await self.config.guild(ctx.guild).reports.set({})
        try:
            embed = discord.Embed(
                title="Reports Cleared",
                description="All reports have been cleared.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Permission Error",
                description="I can't send messages in this channel. Please check my permissions.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

    @reports.command(name="cleanup")
    @checks.admin_or_permissions(manage_guild=True)
    async def cleanup_reports(self, ctx):
        """Manually clean up old reports."""
        reports = await self.config.guild(ctx.guild).reports()
        days = await self.config.guild(ctx.guild).auto_cleanup_days()
        updated_reports = {k: v for k, v in reports.items() if self.is_recent(v.get('timestamp'), days)}
        await self.config.guild(ctx.guild).reports.set(updated_reports)
        embed = discord.Embed(
            title="Reports Cleaned",
            description=f"Reports older than {days} days have been cleaned up.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @reports.command(name="stats")
    @checks.admin_or_permissions(manage_guild=True)
    async def report_stats(self, ctx):
        """View statistics about all reports."""
        reports = await self.config.guild(ctx.guild).reports()
        if not reports:
            embed = discord.Embed(
                title="No Reports",
                description="There are no reports in this guild.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        total_reports = len(reports)
        reason_counts = Counter(report.get('reason', 'Unknown') for report in reports.values())
        most_common_reason = reason_counts.most_common(1)[0] if reason_counts else ("None", 0)
        status_counts = Counter(report.get('status', 'Open') for report in reports.values())
        user_report_counts = Counter(report.get('reported_user', 'Unknown') for report in reports.values())

        embed = discord.Embed(
            title="Report Statistics",
            color=discord.Color.blue()
        )
        embed.add_field(name="Total Reports", value=total_reports, inline=False)
        embed.add_field(name="Most Common Reason", value=f"{most_common_reason[0]} ({most_common_reason[1]} times)", inline=False)
        embed.add_field(name="Reason Breakdown", value="\n".join(f"{reason}: {count}" for reason, count in reason_counts.items()), inline=False)
        embed.add_field(name="Status Breakdown", value="\n".join(f"{status}: {count}" for status, count in status_counts.items()), inline=False)
        embed.add_field(name="Top Reported Users", value="\n".join(f"<@{user}>: {count}" for user, count in user_report_counts.most_common(5)), inline=False)

        await ctx.send(embed=embed)

    def is_recent(self, timestamp, days=30):
        """Check if a report is recent (within N days)."""
        if not timestamp:
            return False
        try:
            report_time = datetime.fromisoformat(timestamp)
            # If timestamp is naive, assume UTC
            if report_time.tzinfo is None:
                report_time = report_time.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - report_time).days < days
        except Exception:
            return False

    @reports.command(name="handle")
    @checks.admin_or_permissions(manage_guild=True)
    async def handle_report(self, ctx, report_id: str):
        """Handle a report by its ID."""
        reports = await self.config.guild(ctx.guild).reports()
        report = reports.get(report_id)

        if not report:
            embed = discord.Embed(
                title="Report Not Found",
                description="We couldn't find a report with that ID.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        # Defensive: handle missing or malformed IDs
        try:
            reported_user = ctx.guild.get_member(int(report['reported_user'])) if report.get('reported_user') else None
        except Exception:
            reported_user = None
        try:
            reporter = ctx.guild.get_member(int(report['reporter'])) if report.get('reporter') else None
        except Exception:
            reporter = None

        # Create a view for handling the report
        class HandleReportView(discord.ui.View):
            def __init__(self, ctx, report_id, reporter, reported_user):
                super().__init__()
                self.ctx = ctx
                self.report_id = report_id
                self.reporter = reporter
                self.reported_user = reported_user
                self.answers = []

            async def ask_question(self, ctx, question, emoji_meanings):
                embed = discord.Embed(
                    title="Report Handling",
                    description=question,
                    color=discord.Color.blue()
                )
                emoji_list = "\n".join([f"{emoji}: {meaning}" for emoji, meaning in emoji_meanings.items()])
                embed.add_field(name="Options", value=emoji_list, inline=False)
                message = await ctx.send(embed=embed)
                for emoji in emoji_meanings.keys():
                    try:
                        await message.add_reaction(emoji)
                    except Exception:
                        pass
                return message

            async def handle_reaction(self, message, emoji_meanings):
                def check(reaction, user):
                    return user == self.ctx.author and str(reaction.emoji) in emoji_meanings and reaction.message.id == message.id

                try:
                    reaction, _ = await self.ctx.bot.wait_for('reaction_add', timeout=60.0, check=check)
                    return str(reaction.emoji)
                except asyncio.TimeoutError:
                    embed = discord.Embed(
                        title="Timeout",
                        description="You took too long to respond. Please try again.",
                        color=discord.Color.red()
                    )
                    await self.ctx.send(embed=embed)
                    return None

            async def handle_report(self):
                # Initial question to confirm investigation
                question = "Have you reviewed and investigated all facts of the matter?"
                emoji_meanings = {"✅": "Yes", "❌": "No"}
                message = await self.ask_question(self.ctx, question, emoji_meanings)
                emoji = await self.handle_reaction(message, emoji_meanings)
                if emoji is None or emoji_meanings[emoji] == "No":
                    embed = discord.Embed(
                        title="Investigation Required",
                        description="Please make sure to review all facts before proceeding.",
                        color=discord.Color.orange()
                    )
                    await self.ctx.send(embed=embed)
                    return
                self.answers.append(emoji_meanings[emoji])

                # Question to determine validity of the report
                question = "Do you believe the report, including its evidence and reason, is valid?"
                emoji_meanings = {"✅": "Valid", "❌": "Invalid"}
                message = await self.ask_question(self.ctx, question, emoji_meanings)
                emoji = await self.handle_reaction(message, emoji_meanings)
                if emoji is None or emoji_meanings[emoji] == "Invalid":
                    embed = discord.Embed(
                        title="Report Marked as Invalid",
                        description="This report has been reviewed and determined to be invalid. No further action will be taken at this time.",
                        color=discord.Color.orange()
                    )
                    await self.ctx.send(embed=embed)
                    self.answers.append("Invalid")
                    await self.finalize()
                    return
                self.answers.append(emoji_meanings[emoji])

                # Final question to decide action
                question = "What action should be taken against the reported user?"
                emoji_meanings = {"⚠️": "Warning", "⏲️": "Timeout", "🔨": "Ban", "❌": "No Action"}
                message = await self.ask_question(self.ctx, question, emoji_meanings)
                emoji = await self.handle_reaction(message, emoji_meanings)
                if emoji is None:
                    return
                self.answers.append(emoji_meanings[emoji])

                await self.finalize()

            async def finalize(self):
                action = self.answers[2] if len(self.answers) > 2 else "No action"
                # Update report status in config
                reports = await self.ctx.cog.config.guild(self.ctx.guild).reports()
                if self.report_id in reports:
                    reports[self.report_id]["status"] = "Closed"
                    reports[self.report_id]["action_taken"] = action
                    reports[self.report_id]["handled_by"] = str(self.ctx.author.id)
                    reports[self.report_id]["handled_at"] = datetime.now(timezone.utc).isoformat()
                    await self.ctx.cog.config.guild(self.ctx.guild).reports.set(reports)

                # Improved user communication for actions
                if action == "Warning" and self.reported_user:
                    try:
                        await self.reported_user.send(
                            "Hello, this is a notice from the moderation team. "
                            "You have received a warning following a review of a report submitted against you. "
                            "Please review the server rules and ensure your future conduct aligns with our guidelines. "
                            "If you have questions, you may contact the moderation team."
                        )
                    except discord.Forbidden:
                        embed = discord.Embed(
                            title="Warning Error",
                            description="I couldn't send a warning to the reported user.",
                            color=discord.Color.red()
                        )
                        await self.ctx.send(embed=embed)
                elif action == "Timeout" and self.reported_user:
                    try:
                        # discord.Member.timeout expects a timedelta or int (seconds) in discord.py 2.x
                        # But in Redbot, it may be .timeout_for or .edit(timeout=...)
                        # We'll use .timeout if available, else fallback
                        if hasattr(self.reported_user, "timeout"):
                            await self.reported_user.timeout(duration=86400)  # 24 hours
                        elif hasattr(self.reported_user, "edit"):
                            await self.reported_user.edit(timeout_until=datetime.now(timezone.utc) + timedelta(seconds=86400))
                        else:
                            raise Exception("Timeout not supported on this bot version.")
                        embed = discord.Embed(
                            title="User Timed Out",
                            description=f"{self.reported_user.mention} has been timed out for 24 hours.",
                            color=discord.Color.green()
                        )
                        await self.ctx.send(embed=embed)
                        try:
                            await self.reported_user.send(
                                "You have been temporarily restricted from participating in the server for 24 hours following a review of a report. "
                                "Please use this time to review the server rules. If you have questions, you may contact the moderation team."
                            )
                        except Exception:
                            pass
                    except discord.Forbidden:
                        embed = discord.Embed(
                            title="Timeout Error",
                            description="I couldn't timeout the reported user.",
                            color=discord.Color.red()
                        )
                        await self.ctx.send(embed=embed)
                    except Exception as e:
                        embed = discord.Embed(
                            title="Timeout Error",
                            description=f"An error occurred while timing out the user: {e}",
                            color=discord.Color.red()
                        )
                        await self.ctx.send(embed=embed)
                elif action == "Ban" and self.reported_user:
                    try:
                        await self.reported_user.ban(reason="Report handled and deemed valid.")
                        embed = discord.Embed(
                            title="User Banned",
                            description=f"{self.reported_user.mention} has been permanently removed from the server following a review of a report. This action was taken in accordance with our community guidelines.",
                            color=discord.Color.green()
                        )
                        await self.ctx.send(embed=embed)
                        try:
                            await self.reported_user.send(
                                "You have been permanently removed (banned) from the server following a review of a report. "
                                "This action was taken in accordance with our community guidelines."
                            )
                        except Exception:
                            pass
                    except discord.Forbidden:
                        embed = discord.Embed(
                            title="Ban Error",
                            description="I couldn't ban the reported user.",
                            color=discord.Color.red()
                        )
                        await self.ctx.send(embed=embed)
                    except Exception as e:
                        embed = discord.Embed(
                            title="Ban Error",
                            description=f"An error occurred while banning the user: {e}",
                            color=discord.Color.red()
                        )
                        await self.ctx.send(embed=embed)
                elif action == "No Action":
                    embed = discord.Embed(
                        title="No action taken",
                        description="No action was taken against the reported user.",
                        color=discord.Color.orange()
                    )
                    await self.ctx.send(embed=embed)

                # Improved language for reporter notification
                if self.reporter:
                    try:
                        if self.answers[1] == "Invalid":
                            result_text = (
                                f"Thank you for submitting your report (ID: `{self.report_id}`). "
                                "After a thorough review, the moderation team has determined that the report does not violate our rules or guidelines, "
                                "and no action will be taken. If you have further concerns, please feel free to reach out to the moderation team."
                            )
                        else:
                            action_text = {
                                "Warning": "The reported user has received a formal warning.",
                                "Timeout": "The reported user has been temporarily restricted from the server for 24 hours.",
                                "Ban": "The reported user has been permanently removed (banned) from the server.",
                                "No Action": "No action was taken against the reported user."
                            }.get(action, f"Action taken: {action}.")
                            result_text = (
                                f"Thank you for submitting your report (ID: `{self.report_id}`). "
                                "Our moderation team has reviewed your report and determined it to be valid. "
                                f"{action_text} If you have any questions or further concerns, please contact the moderation team."
                            )
                        embed = discord.Embed(
                            title="Update on Your Report",
                            description=result_text,
                            color=discord.Color.from_rgb(255, 255, 254)
                        )
                        await self.reporter.send(embed=embed)
                    except discord.Forbidden:
                        embed = discord.Embed(
                            title="Couldn't send report update",
                            description="I couldn't send a DM to the reporter to update them on this report. You may need to reach out manually to the user if you're inclined.",
                            color=discord.Color.red()
                        )
                        await self.ctx.send(embed=embed)
                    except Exception as e:
                        embed = discord.Embed(
                            title="Couldn't send report update",
                            description=f"An error occurred while DMing the reporter: {e}",
                            color=discord.Color.red()
                        )
                        await self.ctx.send(embed=embed)

                embed = discord.Embed(
                    title="Report Processed",
                    description=f"Report `{self.report_id}` has been reviewed and closed. Action taken: {action}.",
                    color=0x2bbd8e
                )
                await self.ctx.send(embed=embed)
                self.stop()

        view = HandleReportView(ctx, report_id, reporter, reported_user)
        await view.handle_report()
