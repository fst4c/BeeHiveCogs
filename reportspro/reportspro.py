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
            title="Reports channel set",
            description=f"Aubmitted reports will now be sent to {channel.mention}.",
            color=0x2bbd8e
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
                title="Role cleared",
                description="The mention role for new reports has been cleared.",
                color=0x2bbd8e
            )
            await ctx.send(embed=embed)
        else:
            await self.config.guild(ctx.guild).mention_role.set(role.id)
            embed = discord.Embed(
                title="Mention role set",
                description=f"The role {role.mention} will be notified for new reports.",
                color=0x2bbd8e
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

        class ReportDropdown(discord.ui.Select):
            def __init__(self, config, cog, interaction, member, reports_channel, capture_chat_history, message):
                self.config = config
                self.cog = cog
                self.interaction = interaction
                self.member = member
                self.reports_channel = reports_channel
                self.capture_chat_history = capture_chat_history
                self.allowed_user_id = interaction.user.id  # Only allow the command invoker
                self.message = message
                self.report_interaction_message = None  # Will be set after view is sent
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
                            extra_details = self.details.value
                            full_description = selected_description + f"\n\nUser details: {extra_details}"
                            await ReportDropdown.finish_report_static(
                                modal_interaction,
                                parent_dropdown,
                                parent_dropdown.config,
                                parent_dropdown.cog,
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
                    self.cog,
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
                interaction, self_ref, config, cog, orig_interaction, member, reports_channel,
                capture_chat_history, message, selected_reason, selected_description, extra_details
            ):
                # Defer the interaction if possible, to ensure it always completes
                try:
                    if hasattr(interaction, "response") and not interaction.response.is_done():
                        await interaction.response.defer(thinking=False, ephemeral=True)
                except Exception:
                    pass

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
                        if hasattr(interaction, "followup"):
                            await interaction.followup.send(embed=embed, ephemeral=True)
                        elif hasattr(interaction, "response") and not interaction.response.is_done():
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
                        if hasattr(interaction, "followup"):
                            await interaction.followup.send(embed=embed, ephemeral=True)
                        elif hasattr(interaction, "response") and not interaction.response.is_done():
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
                        "action_taken": None,
                        "handled_by": None,
                        "handled_at": None,
                    }
                    await config.guild(interaction.guild).reports.set(reports)
                except Exception as e:
                    embed = discord.Embed(
                        title="Error",
                        description=f"Something went wrong while saving the report: {e}",
                        color=discord.Color.red()
                    )
                    try:
                        if hasattr(interaction, "followup"):
                            await interaction.followup.send(embed=embed, ephemeral=True)
                        elif hasattr(interaction, "response") and not interaction.response.is_done():
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
                        if hasattr(interaction, "followup"):
                            await interaction.followup.send(embed=embed, ephemeral=True)
                        elif hasattr(interaction, "response") and not interaction.response.is_done():
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

                    # --- BUTTONS FOR STAFF ACTIONS ---
                    class ReportActionView(discord.ui.View):
                        def __init__(self, cog, reported_user_id, report_id, message_link):
                            super().__init__(timeout=None)
                            self.cog = cog
                            self.reported_user_id = reported_user_id
                            self.report_id = report_id
                            self.message_link = message_link

                        async def interaction_check(self, interaction: discord.Interaction) -> bool:
                            # Only allow users with ban_members or manage_guild to use the buttons
                            perms = interaction.user.guild_permissions
                            if perms.ban_members or perms.kick_members or perms.moderate_members or perms.manage_guild:
                                return True
                            await interaction.response.send_message("You do not have permission to use these actions.", ephemeral=True)
                            return False

                        @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger, emoji="🔨")
                        async def ban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                            guild = interaction.guild
                            user = guild.get_member(int(self.reported_user_id))
                            if not user:
                                await interaction.response.send_message("User not found in this server.", ephemeral=True)
                                return
                            try:
                                await user.ban(reason=f"Staff action via report {self.report_id}")
                                # Update report status in config
                                reports = await self.cog.config.guild(interaction.guild).reports()
                                if self.report_id in reports:
                                    reports[self.report_id]["status"] = "Closed"
                                    reports[self.report_id]["action_taken"] = "Banned"
                                    reports[self.report_id]["handled_by"] = str(interaction.user.id)
                                    reports[self.report_id]["handled_at"] = datetime.now(timezone.utc).isoformat()
                                    await self.cog.config.guild(interaction.guild).reports.set(reports)
                                # Log embed
                                log_embed = discord.Embed(
                                    title="Report Closed: User Banned",
                                    color=discord.Color.red(),
                                    description=f"Report `{self.report_id}` has been closed. {user.mention} was **banned** by {interaction.user.mention}."
                                )
                                log_embed.add_field(name="Report ID", value=f"`{self.report_id}`", inline=True)
                                log_embed.add_field(name="Action", value="Banned", inline=True)
                                log_embed.add_field(name="Handled By", value=interaction.user.mention, inline=True)
                                log_embed.add_field(name="Handled At", value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>", inline=True)
                                log_embed.add_field(name="Reported Message", value=f"[Jump to message]({self.message_link})", inline=False)
                                await interaction.channel.send(embed=log_embed)
                                await interaction.response.send_message(f"{user.mention} has been **banned**.", ephemeral=True)
                            except discord.Forbidden:
                                await interaction.response.send_message("I do not have permission to ban this user.", ephemeral=True)
                            except Exception as e:
                                await interaction.response.send_message(f"Error banning user: {e}", ephemeral=True)

                        @discord.ui.button(label="Kick", style=discord.ButtonStyle.danger, emoji="👢")
                        async def kick_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                            guild = interaction.guild
                            user = guild.get_member(int(self.reported_user_id))
                            if not user:
                                await interaction.response.send_message("User not found in this server.", ephemeral=True)
                                return
                            try:
                                await user.kick(reason=f"Staff action via report {self.report_id}")
                                # Update report status in config
                                reports = await self.cog.config.guild(interaction.guild).reports()
                                if self.report_id in reports:
                                    reports[self.report_id]["status"] = "Closed"
                                    reports[self.report_id]["action_taken"] = "Kicked"
                                    reports[self.report_id]["handled_by"] = str(interaction.user.id)
                                    reports[self.report_id]["handled_at"] = datetime.now(timezone.utc).isoformat()
                                    await self.cog.config.guild(interaction.guild).reports.set(reports)
                                # Log embed
                                log_embed = discord.Embed(
                                    title="Report Closed: User Kicked",
                                    color=discord.Color.orange(),
                                    description=f"Report `{self.report_id}` has been closed. {user.mention} was **kicked** by {interaction.user.mention}."
                                )
                                log_embed.add_field(name="Report ID", value=f"`{self.report_id}`", inline=True)
                                log_embed.add_field(name="Action", value="Kicked", inline=True)
                                log_embed.add_field(name="Handled By", value=interaction.user.mention, inline=True)
                                log_embed.add_field(name="Handled At", value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>", inline=True)
                                log_embed.add_field(name="Reported Message", value=f"[Jump to message]({self.message_link})", inline=False)
                                await interaction.channel.send(embed=log_embed)
                                await interaction.response.send_message(f"{user.mention} has been **kicked**.", ephemeral=True)
                            except discord.Forbidden:
                                await interaction.response.send_message("I do not have permission to kick this user.", ephemeral=True)
                            except Exception as e:
                                await interaction.response.send_message(f"Error kicking user: {e}", ephemeral=True)

                        @discord.ui.button(label="Timeout", style=discord.ButtonStyle.primary, emoji="⏲️")
                        async def timeout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                            guild = interaction.guild
                            user = guild.get_member(int(self.reported_user_id))
                            if not user:
                                await interaction.response.send_message("User not found in this server.", ephemeral=True)
                                return

                            class TimeoutModal(discord.ui.Modal, title="Timeout User"):
                                hours = discord.ui.TextInput(
                                    label="Timeout duration (hours, up to 672):",
                                    style=discord.TextStyle.short,
                                    required=True,
                                    max_length=4,
                                    placeholder="e.g. 24"
                                )

                                def __init__(self):
                                    super().__init__()

                                async def on_submit(self, modal_interaction: discord.Interaction):
                                    try:
                                        hours_val = int(self.hours.value)
                                        if hours_val < 1 or hours_val > 672:
                                            await modal_interaction.response.send_message("Please enter a value between 1 and 672 hours (28 days).", ephemeral=True)
                                            return
                                    except Exception:
                                        await modal_interaction.response.send_message("Invalid number of hours.", ephemeral=True)
                                        return
                                    try:
                                        seconds = hours_val * 3600
                                        if hasattr(user, "timeout"):
                                            await user.timeout(duration=seconds)
                                        elif hasattr(user, "edit"):
                                            await user.edit(timeout_until=datetime.now(timezone.utc) + timedelta(seconds=seconds))
                                        else:
                                            raise Exception("Timeout not supported on this bot version.")
                                        # Update report status in config
                                        reports = await self.cog.config.guild(modal_interaction.guild).reports()
                                        if self.report_id in reports:
                                            reports[self.report_id]["status"] = "Closed"
                                            reports[self.report_id]["action_taken"] = f"Timed out ({hours_val}h)"
                                            reports[self.report_id]["handled_by"] = str(modal_interaction.user.id)
                                            reports[self.report_id]["handled_at"] = datetime.now(timezone.utc).isoformat()
                                            await self.cog.config.guild(modal_interaction.guild).reports.set(reports)
                                        # Log embed
                                        log_embed = discord.Embed(
                                            title="Report Closed: User Timed Out",
                                            color=discord.Color.blue(),
                                            description=f"Report `{self.report_id}` has been closed. {user.mention} was **timed out for {hours_val} hours** by {modal_interaction.user.mention}."
                                        )
                                        log_embed.add_field(name="Report ID", value=f"`{self.report_id}`", inline=True)
                                        log_embed.add_field(name="Action", value=f"Timed out ({hours_val}h)", inline=True)
                                        log_embed.add_field(name="Handled By", value=modal_interaction.user.mention, inline=True)
                                        log_embed.add_field(name="Handled At", value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>", inline=True)
                                        log_embed.add_field(name="Reported Message", value=f"[Jump to message]({self.message_link})", inline=False)
                                        await modal_interaction.channel.send(embed=log_embed)
                                        await modal_interaction.response.send_message(f"{user.mention} has been **timed out for {hours_val} hours**.", ephemeral=True)
                                    except discord.Forbidden:
                                        await modal_interaction.response.send_message("I do not have permission to timeout this user.", ephemeral=True)
                                    except Exception as e:
                                        await modal_interaction.response.send_message(f"Error timing out user: {e}", ephemeral=True)

                            # Attach report_id/message_link to modal instance
                            modal = TimeoutModal()
                            modal.cog = self.cog
                            modal.report_id = self.report_id
                            modal.message_link = self.message_link
                            await interaction.response.send_modal(modal)

                        @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.secondary, emoji="❌")
                        async def dismiss_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                            # Mark the report as closed in config
                            reports = await self.cog.config.guild(interaction.guild).reports()
                            if self.report_id in reports:
                                reports[self.report_id]["status"] = "Closed"
                                reports[self.report_id]["action_taken"] = "Dismissed"
                                reports[self.report_id]["handled_by"] = str(interaction.user.id)
                                reports[self.report_id]["handled_at"] = datetime.now(timezone.utc).isoformat()
                                await self.cog.config.guild(interaction.guild).reports.set(reports)
                            # Log embed
                            log_embed = discord.Embed(
                                title="Report Closed: Dismissed",
                                color=discord.Color.green(),
                                description=f"Report `{self.report_id}` has been **dismissed** by {interaction.user.mention}."
                            )
                            log_embed.add_field(name="Report ID", value=f"`{self.report_id}`", inline=True)
                            log_embed.add_field(name="Action", value="Dismissed", inline=True)
                            log_embed.add_field(name="Handled By", value=interaction.user.mention, inline=True)
                            log_embed.add_field(name="Handled At", value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>", inline=True)
                            log_embed.add_field(name="Reported Message", value=f"[Jump to message]({self.message_link})", inline=False)
                            await interaction.channel.send(embed=log_embed)
                            await interaction.response.send_message("Report dismissed and marked as closed.", ephemeral=True)

                    try:
                        await reports_channel.send(
                            content=mention_text,
                            embed=report_message,
                            allowed_mentions=discord.AllowedMentions(roles=True),
                            view=ReportActionView(cog, member.id, report_id, message.jump_url)
                        )
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
                            if hasattr(interaction, "followup"):
                                await interaction.followup.send(embed=embed, ephemeral=True)
                            elif hasattr(interaction, "response") and not interaction.response.is_done():
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
                            if hasattr(interaction, "followup"):
                                await interaction.followup.send(embed=embed, ephemeral=True)
                            elif hasattr(interaction, "response") and not interaction.response.is_done():
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
                        if hasattr(interaction, "followup"):
                            await interaction.followup.send(embed=embed, ephemeral=True)
                        elif hasattr(interaction, "response") and not interaction.response.is_done():
                            await interaction.response.send_message(embed=embed, ephemeral=True)
                    except Exception:
                        pass
                    return

                # Update the original ephemeral message with the thank you message and remove the view
                thank_you_embed = discord.Embed(
                    description="Your report has been submitted. Thank you for helping to keep this community safe. Our moderators will review your report as soon as possible.",
                    color=0x2bbd8e
                )
                # Always complete the interaction to avoid "This interaction failed"
                try:
                    # Try to send a followup message (since we deferred above)
                    if hasattr(interaction, "followup"):
                        await interaction.followup.send(embed=thank_you_embed, ephemeral=True)
                    elif hasattr(interaction, "response") and not interaction.response.is_done():
                        await interaction.response.send_message(embed=thank_you_embed, ephemeral=True)
                except Exception:
                    # As a last resort, just acknowledge the interaction in some way
                    try:
                        if hasattr(interaction, "response") and not interaction.response.is_done():
                            await interaction.response.send_message(embed=thank_you_embed, ephemeral=True)
                    except Exception:
                        pass

                # Delete the original "report user to the moderators of" embed after thank you message
                try:
                    # interaction.message is the ephemeral message with the embed and dropdown
                    if hasattr(interaction, "message") and interaction.message:
                        await interaction.message.delete()
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
            def __init__(self, config, cog, interaction, member, reports_channel, capture_chat_history, message):
                super().__init__(timeout=180)
                dropdown = ReportDropdown(config, cog, interaction, member, reports_channel, capture_chat_history, message)
                self.add_item(dropdown)

        view = ReportView(self.config, self, interaction, member, reports_channel, self.capture_chat_history, message)

        try:
            sent_msg = await interaction.response.send_message(embed=report_embed, view=view, ephemeral=True)
            # There is no direct way to get the ephemeral message object, but interaction.message will be set in the callback
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

            # Determine embed color and title based on status/action
            status = report_info.get("status", "Open")
            action_taken = report_info.get("action_taken", None)
            handled_by = report_info.get("handled_by", None)
            handled_at = report_info.get("handled_at", None)
            if status.lower() == "closed":
                if action_taken == "Banned":
                    color = discord.Color.red()
                    title = f"Report {report_id} (Closed: Banned)"
                elif action_taken == "Kicked":
                    color = discord.Color.orange()
                    title = f"Report {report_id} (Closed: Kicked)"
                elif action_taken and action_taken.startswith("Timed out"):
                    color = discord.Color.blue()
                    title = f"Report {report_id} (Closed: {action_taken})"
                elif action_taken == "Dismissed":
                    color = discord.Color.green()
                    title = f"Report {report_id} (Closed: Dismissed)"
                else:
                    color = discord.Color.dark_grey()
                    title = f"Report {report_id} (Closed)"
            else:
                color = discord.Color.from_rgb(255, 255, 254)
                title = f"Report {report_id} (Open - Action Needed)"

            embed = discord.Embed(title=title, color=color)
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
                value=status,
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
            if status.lower() == "closed" and action_taken:
                embed.add_field(
                    name="Action Taken",
                    value=action_taken,
                    inline=True
                )
                if handled_by:
                    try:
                        mod = ctx.guild.get_member(int(handled_by))
                        mod_mention = mod.mention if mod else f"<@{handled_by}>"
                    except Exception:
                        mod_mention = f"<@{handled_by}>"
                    embed.add_field(
                        name="Handled By",
                        value=mod_mention,
                        inline=True
                    )
                if handled_at:
                    try:
                        dt = datetime.fromisoformat(handled_at)
                        unix_ts = int(dt.timestamp())
                        handled_at_str = f"<t:{unix_ts}:F>"
                    except Exception:
                        handled_at_str = handled_at
                    embed.add_field(
                        name="Handled At",
                        value=handled_at_str,
                        inline=True
                    )
            embeds.append(embed)

        # Function to handle pagination
        async def send_paginated_embeds(ctx, embeds, report_ids, reported_user_ids, message_links):
            current_page = 0
            # Add staff action buttons to each report view
            class ReportActionView(discord.ui.View):
                def __init__(self, cog, reported_user_id, report_id, message_link):
                    super().__init__(timeout=None)
                    self.cog = cog
                    self.reported_user_id = reported_user_id
                    self.report_id = report_id
                    self.message_link = message_link

                async def interaction_check(self, interaction: discord.Interaction) -> bool:
                    perms = interaction.user.guild_permissions
                    if perms.ban_members or perms.kick_members or perms.moderate_members or perms.manage_guild:
                        return True
                    await interaction.response.send_message("You do not have permission to use these actions.", ephemeral=True)
                    return False

                @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger, emoji="🔨")
                async def ban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    guild = interaction.guild
                    user = guild.get_member(int(self.reported_user_id))
                    if not user:
                        await interaction.response.send_message("User not found in this server.", ephemeral=True)
                        return
                    try:
                        await user.ban(reason=f"Staff action via report {self.report_id}")
                        # Update report status in config
                        reports = await self.cog.config.guild(interaction.guild).reports()
                        if self.report_id in reports:
                            reports[self.report_id]["status"] = "Closed"
                            reports[self.report_id]["action_taken"] = "Banned"
                            reports[self.report_id]["handled_by"] = str(interaction.user.id)
                            reports[self.report_id]["handled_at"] = datetime.now(timezone.utc).isoformat()
                            await self.cog.config.guild(interaction.guild).reports.set(reports)
                        # Log embed
                        log_embed = discord.Embed(
                            title="Report Closed: User Banned",
                            color=discord.Color.red(),
                            description=f"Report `{self.report_id}` has been closed. {user.mention} was **banned** by {interaction.user.mention}."
                        )
                        log_embed.add_field(name="Report ID", value=f"`{self.report_id}`", inline=True)
                        log_embed.add_field(name="Action", value="Banned", inline=True)
                        log_embed.add_field(name="Handled By", value=interaction.user.mention, inline=True)
                        log_embed.add_field(name="Handled At", value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>", inline=True)
                        log_embed.add_field(name="Reported Message", value=f"[Jump to message]({self.message_link})", inline=False)
                        await interaction.channel.send(embed=log_embed)
                        await interaction.response.send_message(f"{user.mention} has been **banned**.", ephemeral=True)
                    except discord.Forbidden:
                        await interaction.response.send_message("I do not have permission to ban this user.", ephemeral=True)
                    except Exception as e:
                        await interaction.response.send_message(f"Error banning user: {e}", ephemeral=True)

                @discord.ui.button(label="Kick", style=discord.ButtonStyle.danger, emoji="👢")
                async def kick_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    guild = interaction.guild
                    user = guild.get_member(int(self.reported_user_id))
                    if not user:
                        await interaction.response.send_message("User not found in this server.", ephemeral=True)
                        return
                    try:
                        await user.kick(reason=f"Staff action via report {self.report_id}")
                        # Update report status in config
                        reports = await self.cog.config.guild(interaction.guild).reports()
                        if self.report_id in reports:
                            reports[self.report_id]["status"] = "Closed"
                            reports[self.report_id]["action_taken"] = "Kicked"
                            reports[self.report_id]["handled_by"] = str(interaction.user.id)
                            reports[self.report_id]["handled_at"] = datetime.now(timezone.utc).isoformat()
                            await self.cog.config.guild(interaction.guild).reports.set(reports)
                        # Log embed
                        log_embed = discord.Embed(
                            title="Report Closed: User Kicked",
                            color=discord.Color.orange(),
                            description=f"Report `{self.report_id}` has been closed. {user.mention} was **kicked** by {interaction.user.mention}."
                        )
                        log_embed.add_field(name="Report ID", value=f"`{self.report_id}`", inline=True)
                        log_embed.add_field(name="Action", value="Kicked", inline=True)
                        log_embed.add_field(name="Handled By", value=interaction.user.mention, inline=True)
                        log_embed.add_field(name="Handled At", value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>", inline=True)
                        log_embed.add_field(name="Reported Message", value=f"[Jump to message]({self.message_link})", inline=False)
                        await interaction.channel.send(embed=log_embed)
                        await interaction.response.send_message(f"{user.mention} has been **kicked**.", ephemeral=True)
                    except discord.Forbidden:
                        await interaction.response.send_message("I do not have permission to kick this user.", ephemeral=True)
                    except Exception as e:
                        await interaction.response.send_message(f"Error kicking user: {e}", ephemeral=True)

                @discord.ui.button(label="Timeout", style=discord.ButtonStyle.primary, emoji="⏲️")
                async def timeout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    guild = interaction.guild
                    user = guild.get_member(int(self.reported_user_id))
                    if not user:
                        await interaction.response.send_message("User not found in this server.", ephemeral=True)
                        return

                    class TimeoutModal(discord.ui.Modal, title="Timeout User"):
                        hours = discord.ui.TextInput(
                            label="Timeout duration (hours, up to 672):",
                            style=discord.TextStyle.short,
                            required=True,
                            max_length=4,
                            placeholder="e.g. 24"
                        )

                        def __init__(self):
                            super().__init__()

                        async def on_submit(self, modal_interaction: discord.Interaction):
                            try:
                                hours_val = int(self.hours.value)
                                if hours_val < 1 or hours_val > 672:
                                    await modal_interaction.response.send_message("Please enter a value between 1 and 672 hours (28 days).", ephemeral=True)
                                    return
                            except Exception:
                                await modal_interaction.response.send_message("Invalid number of hours.", ephemeral=True)
                                return
                            try:
                                seconds = hours_val * 3600
                                if hasattr(user, "timeout"):
                                    await user.timeout(duration=seconds)
                                elif hasattr(user, "edit"):
                                    await user.edit(timeout_until=datetime.now(timezone.utc) + timedelta(seconds=seconds))
                                else:
                                    raise Exception("Timeout not supported on this bot version.")
                                # Update report status in config
                                reports = await self.cog.config.guild(modal_interaction.guild).reports()
                                if self.report_id in reports:
                                    reports[self.report_id]["status"] = "Closed"
                                    reports[self.report_id]["action_taken"] = f"Timed out ({hours_val}h)"
                                    reports[self.report_id]["handled_by"] = str(modal_interaction.user.id)
                                    reports[self.report_id]["handled_at"] = datetime.now(timezone.utc).isoformat()
                                    await self.cog.config.guild(modal_interaction.guild).reports.set(reports)
                                # Log embed
                                log_embed = discord.Embed(
                                    title="Report Closed: User Timed Out",
                                    color=discord.Color.blue(),
                                    description=f"Report `{self.report_id}` has been closed. {user.mention} was **timed out for {hours_val} hours** by {modal_interaction.user.mention}."
                                )
                                log_embed.add_field(name="Report ID", value=f"`{self.report_id}`", inline=True)
                                log_embed.add_field(name="Action", value=f"Timed out ({hours_val}h)", inline=True)
                                log_embed.add_field(name="Handled By", value=modal_interaction.user.mention, inline=True)
                                log_embed.add_field(name="Handled At", value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>", inline=True)
                                log_embed.add_field(name="Reported Message", value=f"[Jump to message]({self.message_link})", inline=False)
                                await modal_interaction.channel.send(embed=log_embed)
                                await modal_interaction.response.send_message(f"{user.mention} has been **timed out for {hours_val} hours**.", ephemeral=True)
                            except discord.Forbidden:
                                await modal_interaction.response.send_message("I do not have permission to timeout this user.", ephemeral=True)
                            except Exception as e:
                                await modal_interaction.response.send_message(f"Error timing out user: {e}", ephemeral=True)

                    # Attach report_id/message_link to modal instance
                    modal = TimeoutModal()
                    modal.cog = self.cog
                    modal.report_id = self.report_id
                    modal.message_link = self.message_link
                    await interaction.response.send_modal(modal)

                @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.secondary, emoji="❌")
                async def dismiss_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    reports = await self.cog.config.guild(interaction.guild).reports()
                    if self.report_id in reports:
                        reports[self.report_id]["status"] = "Closed"
                        reports[self.report_id]["action_taken"] = "Dismissed"
                        reports[self.report_id]["handled_by"] = str(interaction.user.id)
                        reports[self.report_id]["handled_at"] = datetime.now(timezone.utc).isoformat()
                        await self.cog.config.guild(interaction.guild).reports.set(reports)
                    # Log embed
                    log_embed = discord.Embed(
                        title="Report Closed: Dismissed",
                        color=discord.Color.green(),
                        description=f"Report `{self.report_id}` has been **dismissed** by {interaction.user.mention}."
                    )
                    log_embed.add_field(name="Report ID", value=f"`{self.report_id}`", inline=True)
                    log_embed.add_field(name="Action", value="Dismissed", inline=True)
                    log_embed.add_field(name="Handled By", value=interaction.user.mention, inline=True)
                    log_embed.add_field(name="Handled At", value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>", inline=True)
                    log_embed.add_field(name="Reported Message", value=f"[Jump to message]({self.message_link})", inline=False)
                    await interaction.channel.send(embed=log_embed)
                    await interaction.response.send_message("Report dismissed and marked as closed.", ephemeral=True)

            # Send the first embed with buttons
            view = ReportActionView(ctx.cog, reported_user_ids[current_page], report_ids[current_page], message_links[current_page])
            message = await ctx.send(embed=embeds[current_page], view=view)

            # Add reaction controls for pagination
            if len(embeds) > 1:
                await message.add_reaction("⬅️")
                await message.add_reaction("➡️")
            await message.add_reaction("❌")  # Add close emoji

            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ["⬅️", "➡️", "❌"] and reaction.message.id == message.id

            while True:
                try:
                    reaction, user = await ctx.bot.wait_for('reaction_add', timeout=120.0, check=check)

                    if str(reaction.emoji) == "➡️" and current_page < len(embeds) - 1:
                        current_page += 1
                        new_view = ReportActionView(ctx.cog, reported_user_ids[current_page], report_ids[current_page], message_links[current_page])
                        await message.edit(embed=embeds[current_page], view=new_view)
                    elif str(reaction.emoji) == "⬅️" and current_page > 0:
                        current_page -= 1
                        new_view = ReportActionView(ctx.cog, reported_user_ids[current_page], report_ids[current_page], message_links[current_page])
                        await message.edit(embed=embeds[current_page], view=new_view)
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

        # Prepare lists for report_ids, reported_user_ids, and message_links for pagination
        report_ids = []
        reported_user_ids = []
        message_links = []
        for report_id, report_info in filtered_reports:
            report_ids.append(report_id)
            reported_user_ids.append(report_info.get('reported_user'))
            message_links.append(report_info.get('reported_message_link'))

        await send_paginated_embeds(ctx, embeds, report_ids, reported_user_ids, message_links)

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
