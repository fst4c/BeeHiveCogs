import discord
from datetime import timedelta

__all__ = ["ModerationActionView"]

class ModerationActionView(discord.ui.View):
    def __init__(self, cog, message, timeout_issued, *, timeout_duration):
        super().__init__(timeout=None)
        self.cog = cog
        self.message = message
        self.timeout_issued = timeout_issued
        self.timeout_duration = timeout_duration

        # Store the ID of the user who was moderated (the message author)
        self.moderated_user_id = message.author.id

        # Add Untimeout button only if a timeout was issued
        if timeout_issued:
            self.add_item(ModerationActionView.UntimeoutButton(cog, message, row=1, moderated_user_id=self.moderated_user_id))

        # Add Restore button if message was deleted and info is available
        if message.id in cog._deleted_messages:
            self.add_item(ModerationActionView.RestoreButton(cog, message, row=1, moderated_user_id=self.moderated_user_id))

        # Add Warn button (always on row 1)
        self.add_item(ModerationActionView.WarnButton(cog, message, row=1, moderated_user_id=self.moderated_user_id))

        # Only show Timeout button if timeouts are enabled (timeout_duration > 0)
        if self.timeout_duration == 0:
            self.add_item(ModerationActionView.TimeoutButton(cog, message, timeout_duration, row=1, moderated_user_id=self.moderated_user_id))

        # Add kick and ban buttons (always on row 1)
        self.add_item(ModerationActionView.KickButton(cog, message, row=1, moderated_user_id=self.moderated_user_id))
        self.add_item(ModerationActionView.BanButton(cog, message, row=1, moderated_user_id=self.moderated_user_id))

        # Add Dismiss button to delete the log message (row 2)
        self.add_item(ModerationActionView.DismissButton(cog, message, row=2, moderated_user_id=self.moderated_user_id))

        # Add Translate button (always on row 2)
        self.add_item(ModerationActionView.TranslateButton(cog, message, row=2, moderated_user_id=self.moderated_user_id))

        # Add Explain button (always on row 2)
        self.add_item(ModerationActionView.ExplainButton(cog, message, row=2, moderated_user_id=self.moderated_user_id))

        # Add jump to conversation button LAST (so it appears underneath, on row 2)
        self.add_item(discord.ui.Button(label="See conversation", url=message.jump_url, row=2))

    class TimeoutButton(discord.ui.Button):
        def __init__(self, cog, message, timeout_duration, row=1, moderated_user_id=None):
            super().__init__(label="Timeout", style=discord.ButtonStyle.grey, custom_id=f"timeout_{message.author.id}_{message.id}", emoji="⏳", row=row)
            self.cog = cog
            self.message = message
            self.timeout_duration = timeout_duration
            self.moderated_user_id = moderated_user_id

        async def callback(self, interaction: discord.Interaction):
            if (
                interaction.user.id == self.moderated_user_id
                and not getattr(interaction.user.guild_permissions, "administrator", False)
            ):
                await interaction.response.send_message("You cannot interact with moderation logs of your own actions.", ephemeral=True)
                return
            try:
                member = self.message.guild.get_member(self.message.author.id)
                if not member:
                    await interaction.response.send_message("User not found in this server.", ephemeral=True)
                    return
                if hasattr(member, "timed_out_until") and getattr(member, "timed_out_until", None):
                    await interaction.response.send_message("User is already timed out.", ephemeral=True)
                    return
                reason = f"Manual timeout via Omni log button. Message: {self.message.content}"
                await member.timeout(timedelta(minutes=self.timeout_duration), reason=reason)
                self.cog._timeout_issued_for_message[self.message.id] = True
                await interaction.response.send_message(f"User {member.mention} has been timed out for {self.timeout_duration} minutes.", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message(f"Failed to timeout user: {e}", ephemeral=True)

    class UntimeoutButton(discord.ui.Button):
        def __init__(self, cog, message, row=1, moderated_user_id=None):
            super().__init__(label="Untimeout", style=discord.ButtonStyle.grey, custom_id=f"untimeout_{message.author.id}_{message.id}", emoji="✅", row=row)
            self.cog = cog
            self.message = message
            self.moderated_user_id = moderated_user_id

        async def callback(self, interaction: discord.Interaction):
            if (
                interaction.user.id == self.moderated_user_id
                and not getattr(interaction.user.guild_permissions, "administrator", False)
            ):
                await interaction.response.send_message("You cannot interact with moderation logs of your own actions.", ephemeral=True)
                return
            try:
                member = self.message.guild.get_member(self.message.author.id)
                if not member:
                    await interaction.response.send_message("User not found in this server.", ephemeral=True)
                    return

                await member.timeout(None, reason="Staff member removed a timeout issued by Omni")
                self.cog._timeout_issued_for_message[self.message.id] = False
                self.label = "Timeout lifted"
                self.disabled = True
                await interaction.response.defer()
                try:
                    await interaction.message.edit(view=self.view)
                except Exception:
                    pass
            except Exception as e:
                await interaction.response.send_message(f"Failed to untimeout user: {e}", ephemeral=True)

    class WarnButton(discord.ui.Button):
        def __init__(self, cog, message, row=1, moderated_user_id=None):
            super().__init__(label="Warn", style=discord.ButtonStyle.grey, custom_id=f"warn_{message.author.id}_{message.id}", emoji="⚠️", row=row)
            self.cog = cog
            self.message = message
            self.moderated_user_id = moderated_user_id

        async def callback(self, interaction: discord.Interaction):
            if (
                interaction.user.id == self.moderated_user_id
                and not getattr(interaction.user.guild_permissions, "administrator", False)
            ):
                embed = discord.Embed(
                    description="You cannot interact with moderation logs of your own actions.",
                    color=discord.Color.orange()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            member = self.message.guild.get_member(self.message.author.id)
            if not member:
                embed = discord.Embed(
                    description="User not found in this server.",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            warning_embed = discord.Embed(
                title="Conduct warning",
                description=(
                    f"Your message was flagged by the AI moderator in **{self.message.guild.name}**. A human moderator later reviewed this alert, agreed the AI's decision, and has issued you a conduct warning as a result."
                ),
                color=0xff4545
            )
            warning_embed.add_field(
                name="Your message",
                value=f"`{self.message.content}`" or "*No content*",
                inline=False
            )
            warning_embed.add_field(
                name="Next steps",
                value="Please review the server rules and Discord's [Terms of Service](https://discord.com/terms) and [Community Guidelines](https://discord.com/guidelines). Further violations of the server's rules may lead to additional punishments, like timeouts, documented warnings, kicks, and bans.",
                inline=False
            )
            warning_embed.set_footer(text="We appreciate your cooperation in making the server a safe place")
            try:
                await member.send(embed=warning_embed)
                self.label = "Warning sent"
            except Exception:
                self.label = "DM's closed"
            self.disabled = True
            await interaction.response.defer()
            try:
                await interaction.message.edit(view=self.view)
            except Exception:
                pass

            guild_id = self.message.guild.id
            user_id = self.message.author.id
            self.cog.memory_user_warnings[guild_id][user_id] += 1
            guild_conf = self.cog.config.guild(self.message.guild)
            user_warnings = await guild_conf.user_warnings()
            user_warnings[str(user_id)] = user_warnings.get(str(user_id), 0) + 1
            await guild_conf.user_warnings.set(user_warnings)

    class TranslateButton(discord.ui.Button):
        def __init__(self, cog, message, row=2, moderated_user_id=None):
            super().__init__(label="Translate content", style=discord.ButtonStyle.grey, custom_id=f"translate_{message.author.id}_{message.id}", emoji="🔡", row=row)
            self.cog = cog
            self.message = message
            self.moderated_user_id = moderated_user_id

        async def callback(self, interaction: discord.Interaction):
            if (
                interaction.user.id == self.moderated_user_id
                and not getattr(interaction.user.guild_permissions, "administrator", False)
            ):
                await interaction.response.send_message("You cannot interact with moderation logs of your own actions.", ephemeral=True)
                return

            class LanguageModal(discord.ui.Modal, title="Translating moderated content"):
                language = discord.ui.TextInput(
                    label="Language to translate to",
                    placeholder="e.g. French, Spanish, Japanese, etc.",
                    required=True,
                    max_length=50,
                )

                async def on_submit(self, modal_interaction: discord.Interaction):
                    await modal_interaction.response.defer(thinking=True)
                    language_value = self.language.value.strip()
                    if not language_value:
                        await modal_interaction.followup.send("No language provided.", ephemeral=True)
                        return
                    translated = await self.cog.translate_to_language(self.message.content, language_value)
                    if translated:
                        embed = discord.Embed(
                            title=f"Moderated content translated to {language_value}",
                            description=translated,
                            color=0xfffffe
                        )
                        await modal_interaction.followup.send(embed=embed, ephemeral=False)
                    else:
                        await modal_interaction.followup.send(
                            f"Failed to translate the message or no translation available.",
                            ephemeral=True
                        )

                def __init__(self, cog, message):
                    super().__init__()
                    self.cog = cog
                    self.message = message

            await interaction.response.send_modal(LanguageModal(self.cog, self.message))

    class ExplainButton(discord.ui.Button):
        def __init__(self, cog, message, row=2, moderated_user_id=None):
            super().__init__(label="Explain decision", style=discord.ButtonStyle.grey, custom_id=f"explain_{message.author.id}_{message.id}", emoji="💡", row=row)
            self.cog = cog
            self.message = message
            self.moderated_user_id = moderated_user_id

        async def callback(self, interaction: discord.Interaction):
            if (
                interaction.user.id == self.moderated_user_id
                and not getattr(interaction.user.guild_permissions, "administrator", False)
            ):
                await interaction.response.send_message("You cannot interact with moderation logs of your own actions.", ephemeral=True)
                return
            if not (getattr(interaction.user.guild_permissions, "administrator", False) or getattr(interaction.user.guild_permissions, "manage_guild", False)):
                await interaction.response.send_message("You do not have permission to use this button.", ephemeral=True)
                return
            self.disabled = True
            self.label = "AI working..."
            await interaction.response.defer()
            try:
                await interaction.message.edit(view=self.view)
            except Exception:
                pass

            try:
                api_key = (await self.cog.bot.get_shared_api_tokens("openai")).get("api_key")
                if not api_key:
                    await interaction.followup.send("No OpenAI API key configured.", ephemeral=True)
                    self.disabled = False
                    self.label = "Explain"
                    try:
                        await interaction.message.edit(view=self.view)
                    except Exception:
                        pass
                    return
                normalized_content = self.cog.normalize_text(self.message.content)
                input_data = [{"type": "text", "text": normalized_content}]
                scores = await self.cog.analyze_content(input_data, api_key, self.message)
                if not scores:
                    await interaction.followup.send("Failed to get moderation scores for this message.", ephemeral=True)
                    self.disabled = False
                    self.label = "Explain"
                    try:
                        await interaction.message.edit(view=self.view)
                    except Exception:
                        pass
                    return
            except Exception as e:
                await interaction.followup.send(f"Failed to get moderation scores: {e}", ephemeral=True)
                self.disabled = False
                self.label = "Explain"
                try:
                    await interaction.message.edit(view=self.view)
                except Exception:
                    pass
                return

            explanation = await self.cog.explain_moderation(self.message.content, scores)

            self.disabled = False
            self.label = "Explain"

            if explanation:
                embed = discord.Embed(
                    title="Why was this message flagged?",
                    description=explanation,
                    color=0x45ABF5
                )
                sorted_scores = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:6]
                for cat, score in sorted_scores:
                    embed.add_field(name=cat.capitalize(), value=f"{score*100:.1f}%", inline=True)
                await interaction.followup.send(embed=embed, ephemeral=False)
            else:
                await interaction.followup.send(
                    "Failed to generate an explanation for this message.",
                    ephemeral=True
                )

            self.disabled = True
            try:
                await interaction.message.edit(view=self.view)
            except Exception:
                pass

    class KickButton(discord.ui.Button):
        def __init__(self, cog, message, row=1, moderated_user_id=None):
            super().__init__(label="Kick", style=discord.ButtonStyle.grey, custom_id=f"kick_{message.author.id}_{message.id}", emoji="👢", row=row)
            self.cog = cog
            self.message = message
            self.moderated_user_id = moderated_user_id
            self.awaiting_confirmation = False

        async def callback(self, interaction: discord.Interaction):
            if (
                interaction.user.id == self.moderated_user_id
                and not getattr(interaction.user.guild_permissions, "administrator", False)
            ):
                await interaction.response.send_message("You cannot interact with moderation logs of your own actions.", ephemeral=True)
                return

            if not self.awaiting_confirmation:
                self.awaiting_confirmation = True
                self.label = "Confirm kick"
                self.style = discord.ButtonStyle.danger
                try:
                    await interaction.response.edit_message(view=self.view)
                except Exception:
                    pass
                return
            else:
                self.disabled = True
                try:
                    await interaction.response.edit_message(view=self.view)
                except Exception:
                    pass
                await self.cog.kick_user(interaction)

    class BanButton(discord.ui.Button):
        def __init__(self, cog, message, row=1, moderated_user_id=None):
            super().__init__(label="Ban", style=discord.ButtonStyle.grey, custom_id=f"ban_{message.author.id}_{message.id}", emoji="🔨", row=row)
            self.cog = cog
            self.message = message
            self.moderated_user_id = moderated_user_id
            self.awaiting_confirmation = False

        async def callback(self, interaction: discord.Interaction):
            if (
                interaction.user.id == self.moderated_user_id
                and not getattr(interaction.user.guild_permissions, "administrator", False)
            ):
                await interaction.response.send_message("You cannot interact with moderation logs of your own actions.", ephemeral=True)
                return

            if not self.awaiting_confirmation:
                self.awaiting_confirmation = True
                self.label = "Confirm ban"
                self.style = discord.ButtonStyle.danger
                try:
                    await interaction.response.edit_message(view=self.view)
                except Exception:
                    pass
                return
            else:
                self.disabled = True
                try:
                    await interaction.response.edit_message(view=self.view)
                except Exception:
                    pass
                await self.cog.ban_user(interaction)

    class RestoreButton(discord.ui.Button):
        def __init__(self, cog, message, row=1, moderated_user_id=None):
            super().__init__(label="Resend", style=discord.ButtonStyle.grey, custom_id=f"restore_{message.author.id}_{message.id}", emoji="♻️", row=row)
            self.cog = cog
            self.message = message
            self.moderated_user_id = moderated_user_id

        async def callback(self, interaction: discord.Interaction):
            if (
                interaction.user.id == self.moderated_user_id
                and not getattr(interaction.user.guild_permissions, "administrator", False)
            ):
                await interaction.response.send_message("You cannot interact with moderation logs of your own actions.", ephemeral=True)
                return
            msg_id = self.message.id
            deleted_info = self.cog._deleted_messages.get(msg_id)
            if not deleted_info:
                await interaction.response.send_message("No deleted message data found to restore.", ephemeral=True)
                return
            guild = interaction.guild
            channel = guild.get_channel(deleted_info["channel_id"])
            if not channel:
                await interaction.response.send_message("Original channel not found.", ephemeral=True)
                return

            content = deleted_info.get("content", "")
            attachments = deleted_info.get("attachments", [])
            if not isinstance(attachments, list):
                attachments = []

            timestamp = deleted_info.get("created_at")
            if not timestamp and hasattr(self.message, "created_at"):
                timestamp = self.message.created_at
            timestamp_str = ""
            if timestamp:
                import datetime
                if isinstance(timestamp, datetime.datetime):
                    unix_ts = int(timestamp.timestamp())
                else:
                    try:
                        unix_ts = int(timestamp)
                    except Exception:
                        unix_ts = None
                if unix_ts:
                    timestamp_str = f"<t:{unix_ts}:R>"
            if content and timestamp_str:
                description = f"{content}\n*Originally sent {timestamp_str}*"
            elif content:
                description = content
            elif timestamp_str:
                description = f"*Originally sent {timestamp_str}*"
            else:
                description = ""

            try:
                if description.strip():
                    author = self.message.author
                    embed = discord.Embed(
                        title=f"",
                        description=description,
                        color=0xfffffe
                    )
                    if author.avatar:
                        embed.set_author(name=f"{author.display_name} said", icon_url=author.avatar.url)
                    else:
                        embed.set_author(name=f"{author.display_name} said")
                    embed.set_footer(text=f"This message was flagged by the AI moderator, but a staff member subsequently approved it to be sent.")
                    await channel.send(embed=embed)
                for img_url in attachments:
                    if img_url:
                        embed = discord.Embed().set_image(url=img_url)
                        await channel.send(embed=embed)
                self.label = "Message re-sent"
                self.disabled = True
                await interaction.response.defer()
                try:
                    await interaction.message.edit(view=self.view)
                except Exception:
                    pass
            except Exception as e:
                await interaction.response.send_message(f"Failed to restore message: {e}", ephemeral=True)

    class DismissButton(discord.ui.Button):
        def __init__(self, cog, message, row=2, moderated_user_id=None):
            super().__init__(label="Dismiss alert", style=discord.ButtonStyle.grey, custom_id=f"dismiss_{message.id}", emoji="🗑️", row=row)
            self.cog = cog
            self.message = message
            self.moderated_user_id = moderated_user_id

        async def callback(self, interaction: discord.Interaction):
            if (
                interaction.user.id == self.moderated_user_id
                and not getattr(interaction.user.guild_permissions, "administrator", False)
            ):
                await interaction.response.send_message("You cannot dismiss moderation logs of your own actions.", ephemeral=True)
                return
            if not (getattr(interaction.user.guild_permissions, "administrator", False) or getattr(interaction.user.guild_permissions, "manage_guild", False)):
                await interaction.response.send_message("You do not have permission to use this button.", ephemeral=True)
                return
            try:
                await interaction.message.delete()
            except Exception as e:
                await interaction.response.send_message(f"Failed to delete log message: {e}", ephemeral=True)