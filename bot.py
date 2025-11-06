import os
import json
import asyncio
import discord
from discord.ext import commands

try:
    import aiohttp  
except Exception:
    aiohttp = None

# path to the file where team data will be stored
DATA_FILE = "teams.json"

# id of the channel where notifications will be sent
NOTIFY_CHANNEL_ID = 1410967493383819335

# id of the role that allows creating teams
CREATE_TEAM_ROLE_ID = 1410981260821794969

# id of the discord server
GUILD_ID = 1410964177262088214  

intents = discord.Intents.default()
intents.message_content = True
intents.members = True


class MyBot(commands.Bot):
    async def setup_hook(self):
        await self.tree.sync()
        print("synchronized") 


bot = MyBot(command_prefix="!", intents=intents)


def load_data():
    if not os.path.exists(DATA_FILE) or os.stat(DATA_FILE).st_size == 0:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"teams": {}}, f, ensure_ascii=False)
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def get_user_team(user_id, data):
    for name, team in data["teams"].items():
        if user_id in team["members"]:
            return name
    return None


async def get_notify_channel():
    ch = bot.get_channel(NOTIFY_CHANNEL_ID)
    if ch is None:
        try:
            ch = await bot.fetch_channel(NOTIFY_CHANNEL_ID)
        except Exception:
            ch = None
    return ch


async def async_retry(coro_func, *args, max_attempts=4, base_delay=0.7, **kwargs):
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_func(*args, **kwargs)
        except discord.Forbidden:
            raise
        except discord.HTTPException as e:
            # ретраим только временные/сетевые и 5xx
            if 500 <= getattr(e, "status", 0) < 600 and attempt < max_attempts:
                await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
                last_exc = e
                continue
            raise
        except Exception as e:
            # сетевые ошибки aiohttp
            if aiohttp and isinstance(e, aiohttp.ClientOSError) and attempt < max_attempts:
                await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
                last_exc = e
                continue
            # другие ошибки не крутим долго
            raise
    if last_exc:
        raise last_exc


async def safe_send(channel: discord.abc.Messageable, content: str):
    if channel is None:
        return
    try:
        await async_retry(channel.send, content)
    except Exception:
        pass


async def ensure_guild(guild: discord.Guild | None) -> discord.Guild | None:
    if guild:
        return guild
    return bot.get_guild(GUILD_ID)

async def ensure_team_role(guild: discord.Guild, teamname: str) -> discord.Role | None:
    if guild is None:
        guild = await ensure_guild(None)
        if guild is None:
            return None
    role = discord.utils.get(guild.roles, name=teamname)
    if role:
        return role
    try:
        role = await async_retry(guild.create_role, name=teamname, reason="Team role create")
    except discord.Forbidden:
        return None
    return role


async def add_team_role(guild: discord.Guild, member_id: int, teamname: str):
    guild = await ensure_guild(guild)
    if guild is None:
        return
    role = await ensure_team_role(guild, teamname)
    if not role:
        return
    try:
        member = guild.get_member(member_id) or await async_retry(guild.fetch_member, member_id)
    except discord.NotFound:
        return
    try:
        await async_retry(member.add_roles, role, reason="Team join")
    except discord.Forbidden:
        pass


async def remove_team_role(guild: discord.Guild, member_id: int, teamname: str):
    guild = await ensure_guild(guild)
    if guild is None:
        return
    role = discord.utils.get(guild.roles, name=teamname)
    if not role:
        return
    try:
        member = guild.get_member(member_id) or await async_retry(guild.fetch_member, member_id)
    except discord.NotFound:
        return
    try:
        await async_retry(member.remove_roles, role, reason="Team leave/kick")
    except discord.Forbidden:
        pass


async def delete_team_role(guild: discord.Guild, teamname: str):
    guild = await ensure_guild(guild)
    if guild is None:
        return
    role = discord.utils.get(guild.roles, name=teamname)
    if not role:
        return
    try:
        await async_retry(role.delete, reason="Team disband")
    except discord.Forbidden:
        pass

class invite_button(discord.ui.View):
    def __init__(self, inviter_id, team_name):
        super().__init__(timeout=None)
        self.inviter_id = inviter_id
        self.team_name = team_name

    @discord.ui.button(label="✅ join", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        user_id = user.id
        guild = interaction.guild or bot.get_guild(GUILD_ID)
        data = load_data()

        if not guild:
            await interaction.response.send_message("error: guild not found.", ephemeral=True)
            return

        if get_user_team(user_id, data):
            await interaction.response.send_message("you are already in a team.", ephemeral=True)
            return

        team = data["teams"].get(self.team_name)
        if not team:
            await interaction.response.send_message("team not found.", ephemeral=True)
            return

        if len(team["members"]) >= team["max_members"]:
            await interaction.response.send_message("team is full.", ephemeral=True)
            return

        team["members"].append(user_id)
        save_data(data)

        await add_team_role(guild, user_id, self.team_name)

        await interaction.response.send_message(f"you joined the team **{self.team_name}**.", ephemeral=True)

        ch = await get_notify_channel()
        await safe_send(ch, f"➡️ {user.mention} joined the team **{self.team_name}**")

@bot.tree.command(name="createteam")
async def createteam(interaction: discord.Interaction, teamname: str, teamtag: str, picture: str, description: str):
    guild = interaction.guild or bot.get_guild(GUILD_ID)
    data = load_data()
    user = interaction.user
    user_id = user.id

    if CREATE_TEAM_ROLE_ID not in [role.id for role in user.roles]:
        await interaction.response.send_message("you don't have permission to create a team.", ephemeral=True)
        return

    if get_user_team(user_id, data):
        await interaction.response.send_message("you are already in a team.", ephemeral=True)
        return

    if teamname in data["teams"]:
        await interaction.response.send_message("a team with this name already exists.", ephemeral=True)
        return

    data["teams"][teamname] = {
        "owner_id": user_id,
        "members": [user_id],
        "max_members": 10,
        "tag": teamtag,
        "picture": picture,
        "description": description
    }
    save_data(data)

    await add_team_role(guild, user_id, teamname)

    await interaction.response.send_message(f"team **{teamname}** created!", ephemeral=True)

    ch = await get_notify_channel()
    await safe_send(ch, f"🆕 team **{teamname}** created by captain {user.mention}")


@bot.tree.command(name="invite")
async def invite(interaction: discord.Interaction, player: discord.User):
    data = load_data()
    inviter_id = interaction.user.id
    teamname = get_user_team(inviter_id, data)

    if not teamname:
        await interaction.response.send_message("you are not in a team.", ephemeral=True)
        return

    if inviter_id != data["teams"][teamname]["owner_id"]:
        await interaction.response.send_message("only the team owner can send invites.", ephemeral=True)
        return

    try:
        await player.send(f"**do you want to join the team {teamname}?**", view=invite_button(inviter_id, teamname))
        await interaction.response.send_message(f"invite sent to {player.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("could not send dm to the user.", ephemeral=True)
@bot.tree.command(name="manageteam")
async def manageteam(interaction: discord.Interaction):
    data = load_data()
    user_id = interaction.user.id
    teamname = get_user_team(user_id, data)

    if not teamname:
        await interaction.response.send_message("you are not in a team.", ephemeral=True)
        return

    team = data["teams"][teamname]
    is_owner = user_id == team["owner_id"]
    guild = interaction.guild or bot.get_guild(GUILD_ID)

    embed = discord.Embed(title="gorilla tag comp manager", color=discord.Color.blue())
    embed.add_field(
        name=f"**{teamname}**",
        value=f"{team.get('description', 'no description')}\n{len(team['members'])}/{team['max_members']}",
        inline=False
    )
    embed.set_thumbnail(url=team["picture"])

    view = discord.ui.View()

    async def show_roster(inter: discord.Interaction):
        members = []
        for member_id in team["members"]:
            u = await bot.fetch_user(member_id)
            members.append(f"- {u.mention}")
        await inter.response.send_message(f"👥 team roster **{teamname}**:\n" + "\n".join(members), ephemeral=True)

    roster_button = discord.ui.Button(label="Roster", style=discord.ButtonStyle.primary)
    roster_button.callback = show_roster
    view.add_item(roster_button)

    if not is_owner:
        async def leave_team(inter: discord.Interaction):
            if user_id in team["members"]:
                team["members"].remove(user_id)
                save_data(data)
                await remove_team_role(guild, user_id, teamname)
                ch = await get_notify_channel()
                await safe_send(ch, f"⬅️ {interaction.user.mention} left the team **{teamname}**")
            await inter.response.send_message("you left the team.", ephemeral=True)

        leave_button = discord.ui.Button(label="Leave Team", style=discord.ButtonStyle.danger)
        leave_button.callback = leave_team
        view.add_item(leave_button)

    if is_owner:
        async def disband_team(inter: discord.Interaction):
            members = list(team["members"])
            del data["teams"][teamname]
            save_data(data)

            await delete_team_role(guild, teamname)

            ch = await get_notify_channel()
            await safe_send(ch, f"🗑️ team **{teamname}** was disbanded")

            await inter.response.send_message(f"team **{teamname}** was disbanded.", ephemeral=True)

        disband_button = discord.ui.Button(label="Disband Team", style=discord.ButtonStyle.danger)
        disband_button.callback = disband_team
        view.add_item(disband_button)

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="kick")
async def kick(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    user_id = interaction.user.id
    target_id = user.id
    teamname = get_user_team(user_id, data)
    guild = interaction.guild or bot.get_guild(GUILD_ID)

    if not teamname:
        await interaction.response.send_message("you are not in a team.", ephemeral=True)
        return

    team = data["teams"][teamname]

    if user_id != team["owner_id"]:
        await interaction.response.send_message("only the team owner can kick members.", ephemeral=True)
        return

    if target_id not in team["members"]:
        await interaction.response.send_message("this user is not in your team.", ephemeral=True)
        return

    if target_id == user_id:
        await interaction.response.send_message("you cannot kick yourself.", ephemeral=True)
        return

    team["members"].remove(target_id)
    save_data(data)

    await remove_team_role(guild, target_id, teamname)

    ch = await get_notify_channel()
    await safe_send(ch, f"🚫 {user.mention} was kicked from the team **{teamname}**")

    await interaction.response.send_message(f"{user.mention} was removed from the team **{teamname}**.", ephemeral=True)


@bot.event
async def on_ready():
    print(f" bot is running")


if __name__ == "__main__":
    bot.run("token")
