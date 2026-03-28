import discord
from discord import app_commands
from discord.ext import commands

import db
from views import ManageView, SignupView, OPugSignupView, SixsSignupView, FreshPugManageView


def is_hoster(interaction):
    role_id = interaction.client.config.get("hoster_role_id")
    if not role_id:
        return True
    return any(str(r.id) == str(role_id) for r in interaction.user.roles)


class ManageCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        matches = await db.get_all_active_matches()
        for match in matches:
            match_type = match["type"]
            if match_type in ("6s_mix", "6s_opug"):
                view = SixsSignupView(match["id"])
            elif match_type == "opug":
                view = OPugSignupView(match["id"])
            else:
                view = SignupView(match["id"])
            self.bot.add_view(view)

    @app_commands.command(name="manage", description="Open the manage panel for the match in this channel.")
    async def manage(self, interaction):
        if not is_hoster(interaction):
            await interaction.response.send_message(
                "❌ You need the hoster role to use this command.", ephemeral=True
            )
            return

        match = await db.get_match_by_channel(interaction.channel_id)
        if not match:
            await interaction.response.send_message(
                "❌ No active match found in this channel.", ephemeral=True
            )
            return


        match_id = match["id"]

        from views import build_manage_text, FreshPugManageView
        if match["type"] in ("fresh_pug", "6s_fresh_pug"):
            mode = "Fresh PUG 6v6" if match["type"] == "6s_fresh_pug" else "Fresh PUG"
            view = FreshPugManageView(match_id)
            await interaction.response.send_message(
                f"**{mode}** — {match['division']} | <t:{match['timestamp']}:F>",
                view=view, ephemeral=True
            )
        else:
            text, _ = await build_manage_text(match_id)
            view = await ManageView.create(match_id)
            await interaction.response.send_message(text, view=view, ephemeral=True)




async def setup(bot):
    await bot.add_cog(ManageCog(bot))
