import discord

async def on_member_join(self, member: discord.Member):
    """
    Track invites and record who invited whom.
    """
    inviter_id = await self._find_inviter(member)
    if inviter_id:
        # Save the invited user to the inviter's invited_users list
        inviter_conf = self.config.user_from_id(inviter_id)
        invited_users = await inviter_conf.invited_users()
        if member.id not in invited_users:
            invited_users.append(member.id)
            await inviter_conf.invited_users.set(invited_users)
        # Optionally, notify the inviter
        try:
            user = self.bot.get_user(inviter_id)
            if user:
                embed = discord.Embed(
                    title="You brought a friend!",
                    description=f"Thanks for inviting {member.mention} to SchoolworkAI!\n\nIf they sign up and complete onboarding, you'll get $1 of free SchoolworkAI usage as our way of saying thank you.\n\nDon't forget to remind them to **/signup**.",
                    color=0x476b89
                )
                await user.send(embed=embed)
        except Exception as e:
            print(f"Error notifying inviter {inviter_id}: {e}")

async def on_member_update(self, before: discord.Member, after: discord.Member):
    """
    When a member's roles or status change, check if they have just become a customer (i.e., completed onboarding).
    If so, check if they were invited and grant invite credits if eligible.
    """
    # Only act if the user just got the customer role
    before_roles = set(before.roles)
    after_roles = set(after.roles)
    from .config import CUSTOMER_ROLE_ID  # Import here to avoid circular import
    customer_role = after.guild.get_role(CUSTOMER_ROLE_ID)
    if customer_role and customer_role not in before_roles and customer_role in after_roles:
        # See if this user was invited by someone
        for inviter_id, inviteds in self._invite_cache.get(after.guild.id, {}).items():
            if after.id in inviteds:
                await self._grant_invite_credits(inviter_id)
                break

async def on_app_command_completion(self, interaction: discord.Interaction, command: discord.app_commands.Command):
    # This is a placeholder in case you want to handle app command completions
    pass