from random import randint
import discord
from discord import player
from discord.client import _loop
from discord.ext import commands, tasks
import datetime
import pytz
import asyncio
import json
import os

TOKEN = 'MTM1NTk2ODQ1MzQ2NjUyNTg2Ng.Gtga0A.QH5BBUQnrKNIxJv-ccolkj-FStfq1fxvi3SRBw'

CONFIG_FILE = "guild_config.json"
POLL_FILE = "polls.json"

def load_guild_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r") as f:
        content = f.read()
        if not content.strip():  # Check if the file is empty or contains only whitespace
            return {}
        return json.loads(content)


def save_guild_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def load_polls():
    if not os.path.exists(POLL_FILE):
        return {}
    with open(POLL_FILE, "r") as f:
        content = f.read()
        if not content.strip():  # Check if the file is empty or contains only whitespace
            return {}
        return json.loads(content)

def save_polls(polls):
    with open(POLL_FILE, "w") as f:
        json.dump(polls, f, indent=2)

def is_storyteller(interaction):
    if interaction.guild is None:
        interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return False
    config = load_guild_config()
    guild_id = str(interaction.guild.id)
    if interaction.user.guild_permissions.administrator:
        return True
    if guild_id not in config or "storyteller_role_id" not in config.get(guild_id, {}):
        interaction.response.send_message("Storyteller role is not configured. Contact an admin", ephemeral=True)
        return False
    if interaction.user.roles.__contains__(interaction.guild.get_role(config[guild_id]["storyteller_role_id"])):
        return True
    interaction.response.send_message("You must be a storyteller to use this command.", ephemeral=True)
    return False

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    try:
        synced = await bot.tree.sync()  # Registers slash commands with Discord
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(f'Error syncing commands: {e}')

def get_next_occurrence(day_name, hour, minute, user_tz_str):
    # Map day names to weekday numbers (Monday=0, Sunday=6)
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    target_weekday = days.index(day_name)

    # Get current time in user's timezone
    user_tz = pytz.timezone(user_tz_str)
    now = datetime.datetime.now(user_tz)

    # Build the next target datetime
    days_ahead = (target_weekday - now.weekday() + 7) % 7
    if days_ahead == 0 and (now.hour, now.minute) >= (hour, minute):
        days_ahead = 7  # If today but time has passed, go to next week

    next_date = now + datetime.timedelta(days=days_ahead)
    next_dt = user_tz.localize(datetime.datetime(
        year=next_date.year,
        month=next_date.month,
        day=next_date.day,
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0
    ))
    # Convert to Unix timestamp (UTC)
    unix_timestamp = int(next_dt.timestamp())
    return unix_timestamp

@bot.tree.command(name="create_poll", description="Send a poll message in the polls channel")
async def create_poll(
    interaction: discord.Interaction,
    day: str = "Saturday", #Parameter for day, default saturday
    hour: int = 20, #Parameter for hour, default 20
    minute: int = 0  #Parameter for minute, default 0
):
    if not is_storyteller(interaction):
        return

    day = day.capitalize()  # Ensure the day is capitalized for consistency

    poll_channel_id = load_guild_config().get(str(interaction.guild.id), {}).get("poll_channel_id")
    poll_channel = bot.get_channel(poll_channel_id)

    if poll_channel is None:
        await interaction.response.send_message("Channel not found.", ephemeral=True)
        return

    # Send the message
    if not (0 <= hour < 24) or not (0 <= minute < 60):
        await interaction.response.send_message("Invalid time specified.", ephemeral=True)
        return
    if day not in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        await interaction.response.send_message("Invalid day specified.", ephemeral=True)
        return
    unix_time = get_next_occurrence(day, hour, minute, "Europe/London")

    message = await poll_channel.send(f"📢 Play BOTC on <t:{unix_time}:F> (<t:{unix_time}:R>)")
    emoji_list = [
        "👍",
        "👎",
        "📖",
        "⏰"
        ]

    # Add reactions
    for emoji in emoji_list:
        try:
            await message.add_reaction(emoji)
        except Exception as e:
            await interaction.response.send_message(f"Failed to add reaction: {emoji}", ephemeral=True)
            return

    polls = load_polls()
    guild_id = str(interaction.guild.id)
    if guild_id not in polls:
        polls[guild_id] = {}

    poll_id = randint(1, 9999)
    while poll_id in polls[guild_id]:
        poll_id = randint(1, 9999)
    polls[guild_id][poll_id] = {}

    polls[guild_id][poll_id]["end_time"] = unix_time
    polls[guild_id][poll_id]["message_id"] = message.id
    polls[guild_id][poll_id]["pings"] = [] # Stores ping message ids
    save_polls(polls)

    await interaction.response.send_message("Message sent and reactions added!", ephemeral=True)

@bot.tree.command(name="ping_unvoted", description="Ping users who have not reacted to all polls")
async def ping_unvoted(interaction: discord.Interaction):
    if not is_storyteller(interaction):
        return

    polls = load_polls()
    guild_id = str(interaction.guild.id)
    if guild_id not in polls or not polls[guild_id].strip():
        await interaction.response.send_message("No polls found for this server.")

    poll_channel_id = load_guild_config().get(guild_id, {}).get("poll_channel_id")
    poll_channel = bot.get_channel(poll_channel_id)

    emoji_list = ["👍", "👎", "📖", "⏰"]
    if poll_channel is None:
        await interaction.response.send_message("Poll channel not found.", ephemeral=True)
        return

    unvoted_members = []
    # Fetch all polls for the guild
    for poll_id in polls[guild_id].items():
        poll_data = polls[guild_id][poll_id]
        message_id = poll_data.get("message_id")
        end_time = poll_data.get("end_time")
        message = bot.get_channel(poll_channel_id).get_partial_message(message_id)
        reacted_users = set()
        for reaction in message.reactions:
            if str(reaction.emoji) in emoji_list:
                async for user in reaction.users():
                    if not user.bot:
                        reacted_users.add(user.id)

        guild = interaction.guild
        async for member in guild.fetch_members(limit=None):
            if not member.bot and member.id not in reacted_users and member.id not in unvoted_members:
                unvoted_members.append(member.mention)

    if not unvoted_members:
        await interaction.response.send_message("Everyone has voted!", ephemeral=True)
    else:
        await interaction.response.send_message("Pinged unvoted members.", ephemeral=True)
        response = f""
        for member in unvoted_members:
            response += f"{member.mention}"
        await poll_channel.send(f"Please vote in the polls: {response}")
        polls[guild_id][poll_id]["pings"].add(message.id)
        save_polls(polls)

@bot.tree.command(name="night", description="Move players to private night channels and lock public channels.")
async def night(interaction: discord.Interaction):
    if not is_storyteller(interaction):
        return
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)

    config = load_guild_config()
    guild_id = str(guild.id)

    public_category_id = config.get(guild_id, {}).get("public_category_id")
    night_category_id = config.get(guild_id, {}).get("night_category_id")
    player_role_id = config.get(guild_id, {}).get("player_role_id")
    storyteller_role_id = config.get(guild_id, {}).get("storyteller_role_id")

    player_role = guild.get_role(player_role_id)
    storyteller_role = guild.get_role(storyteller_role_id)
    public_category = guild.get_channel(public_category_id)
    night_category = guild.get_channel(night_category_id)

    if not player_role or not public_category or not night_category:
        response = ""
        if not player_role:
            response += "Player role is not set. "
        if not public_category:
            response += "Public category is not set. "
        if not night_category:
            response += "Night category is not set. "
        await interaction.response.send_message(response.strip(), ephemeral=True)
        return

    # 1. Move each player to their own private night voice channel
    night_channels = []
    for member in player_role.members:
        if member.voice and not member.roles.__contains__(storyteller_role):
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                member: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
            }
            if storyteller_role:
                overwrites[storyteller_role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
            # Create a private voice channel for the player
            channel = await guild.create_voice_channel(
                name=f"Night - {member.display_name}",
                overwrites=overwrites,
                category=night_category
            )
            night_channels.append(channel)
            # Move the player to their channel if they're in a voice channel
            if member.voice and member.voice.channel:
                await member.move_to(channel)

    # 2. Lock all channels in the public category
    for channel in public_category.channels:
        overwrites = channel.overwrites_for(guild.default_role)
        overwrites.view_channel = False
        await channel.set_permissions(guild.default_role, overwrite=overwrites)

    #await interaction.response.send_message(f"Moved players to private night channels and locked public channels.", ephemeral=True)

@bot.tree.command(name = "townsquare", description = "Unlock public channels, move players back to Town Square, and delete night channels.")
async def townsquare(interaction: discord.Interaction):
    if not is_storyteller(interaction):
        return
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)
    
    config = load_guild_config()
    guild_id = str(guild.id)

    player_role_id = config.get(guild_id, {}).get("player_role_id")
    public_category_id = config.get(guild_id, {}).get("public_category_id")
    night_category_id = config.get(guild_id, {}).get("night_category_id")
    townsquare_channel_id = config.get(guild_id, {}).get("townsquare_channel_id")
    storyteller_role_id = config.get(guild_id, {}).get("storyteller_role_id")

    player_role = guild.get_role(player_role_id)
    public_category = guild.get_channel(public_category_id)
    night_category = guild.get_channel(night_category_id)
    townsquare_channel = guild.get_channel(townsquare_channel_id)

    if not player_role or not public_category or not night_category:
        response = ""
        if not player_role:
            response += "Player role is not set. "
        if not public_category:
            response += "Public category is not set. "
        if not night_category:
            response += "Night category is not set. "
        await interaction.response.send_message(response.strip(), ephemeral=True)
        return

    # 1. Unlock all channels in the public category
    for channel in public_category.channels:
        overwrites = channel.overwrites_for(guild.default_role)
        overwrites.view_channel = True
        await channel.set_permissions(guild.default_role, overwrite=overwrites)

    # 2. Move players back to the Town Square channel
    for member in player_role.members:
        if member.voice and member.voice.channel:
            if townsquare_channel:
                await member.move_to(townsquare_channel)

    # 3. Delete all private night channels
    for channel in night_category.channels:
        try:
            await channel.delete()
        except discord.Forbidden:
            print(f"Failed to delete channel {channel.name}. Check permissions.")
        except Exception as e:
            print(f"Error deleting channel {channel.name}: {e}")

    #await interaction.response.send_message("Moved players back to Town Square", ephemeral=True)

@bot.tree.command(name="clear_game_chat", description="Clears the game chat channel (limit 100 messages).")
async def clear_game_chat(interaction: discord.Interaction):
    if not is_storyteller(interaction):
        return

    config = load_guild_config()
    guild_id = str(interaction.guild.id)
    game_chat_channel_id = config.get(guild_id, {}).get("game_chat_channel_id")

    if not game_chat_channel_id:
        await interaction.response.send_message("Game chat channel is not configured.", ephemeral=True)
        return

    game_chat_channel = interaction.guild.get_channel(game_chat_channel_id)
    async for message in game_chat_channel.history(limit=100):
        try:
            await message.delete()
        except discord.Forbidden:
            await interaction.response.send_message("I do not have permission to delete messages in the game chat channel.", ephemeral=True)
            return
        except discord.NotFound:
            continue
    await interaction.response.send_message("Messages in game chat deleted", ephemeral=True)

### --- CONFIGURATION COMMANDS --- ###    

@bot.tree.command(name="set_player_role", description="Set the player role ID for the bot.")
async def set_player_role(interaction: discord.Interaction, role: discord.Role):
    if not is_storyteller(interaction):
        return
    config = load_guild_config()
    guild_id = str(interaction.guild.id)
    if guild_id not in config:
        config[guild_id] = {}
    config[guild_id]["player_role_id"] = role.id
    save_guild_config(config)
    await interaction.response.send_message(f"Set the player role ID to {role.id} (@{role.name})", ephemeral=True)
    print(f"Player ID set to {role.id} ({role.name}) for guild {interaction.guild.name} by {interaction.user.name}")

@bot.tree.command(name="set_storyteller_role", description="Set the storyteller role ID for the bot.")
async def set_player_role(interaction: discord.Interaction, role: discord.Role):
    if not is_storyteller(interaction):
        return
    config = load_guild_config()
    guild_id = str(interaction.guild.id)
    if guild_id not in config:
        config[guild_id] = {}
    config[guild_id]["storyteller_role_id"] = role.id
    save_guild_config(config)
    await interaction.response.send_message(f"Set the storyteller role ID to {role.id} (@{role.name})", ephemeral=True)
    print(f"Storyteller ID set to {role.id} ({role.name}) for guild {interaction.guild.name} by {interaction.user.name}")

@bot.tree.command(name="set_public_category", description="Set the public category ID for the bot.")
async def set_player_role(interaction: discord.Interaction, channel: discord.CategoryChannel):
    if not is_storyteller(interaction):
        return
    config = load_guild_config()
    guild_id = str(interaction.guild.id)
    if guild_id not in config:
        config[guild_id] = {}
    config[guild_id]["public_category_id"] = channel.id
    save_guild_config(config)
    await interaction.response.send_message(f"Set the public category ID to {channel.id} ({channel.name})", ephemeral=True)
    print(f"Public Category ID set to {channel.id} ({channel.name}) for guild {interaction.guild.name} by {interaction.user.name}")

@bot.tree.command(name="set_night_category", description="Set the night category ID for the bot.")
async def set_player_role(interaction: discord.Interaction, channel: discord.CategoryChannel):
    if not is_storyteller(interaction):
        return
    config = load_guild_config()
    guild_id = str(interaction.guild.id)
    if guild_id not in config:
        config[guild_id] = {}
    config[guild_id]["night_category_id"] = channel.id
    save_guild_config(config)
    await interaction.response.send_message(f"Set the night category ID to {channel.id} ({channel.name})", ephemeral=True)
    print(f"Night Category ID set to {channel.id} ({channel.name}) for guild {interaction.guild.name} by {interaction.user.name}")

@bot.tree.command(name="set_townsquare", description="Set the townsquare channel ID for the bot.")
async def set_player_role(interaction: discord.Interaction, channel: discord.VoiceChannel):
    if not is_storyteller(interaction):
        return
    config = load_guild_config()
    guild_id = str(interaction.guild.id)
    if guild_id not in config:
        config[guild_id] = {}
    config[guild_id]["townsquare_channel_id"] = channel.id
    save_guild_config(config)
    await interaction.response.send_message(f"Set the townsquare channel ID to {channel.id} ({channel.name})", ephemeral=True)
    print(f"Townsqare ID set to {channel.id} (@{channel.name}) for guild {interaction.guild.name} by {interaction.user.name}")

@bot.tree.command(name="set_game_chat", description="Set the game chat channel ID for the bot.")
async def set_game_chat(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_storyteller(interaction):
        return
    config = load_guild_config()
    guild_id = str(interaction.guild.id)
    if guild_id not in config:
        config[guild_id] = {}
    config[guild_id]["game_chat_channel_id"] = channel.id
    save_guild_config(config)
    await interaction.response.send_message(f"Set the game chat channel ID to {channel.id} ({channel.name})", ephemeral=True)
    print(f"Game chat ID set to {channel.id} (@{channel.name}) for guild {interaction.guild.name} by {interaction.user.name}")

@bot.tree.command(name="set_poll_channel", description="Set the poll channel ID for the bot.")
async def set_game_chat(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_storyteller(interaction):
        return
    config = load_guild_config()
    guild_id = str(interaction.guild.id)
    if guild_id not in config:
        config[guild_id] = {}
    config[guild_id]["poll_channel_id"] = channel.id
    save_guild_config(config)
    await interaction.response.send_message(f"Set the poll channel ID to {channel.id} ({channel.name})", ephemeral=True)
    print(f"Poll channel ID set to {channel.id} (@{channel.name}) for guild {interaction.guild.name} by {interaction.user.name}")

### --- POLL CHECK LOOP --- ###

#@tasks.loop(minutes=1)
async def poll_check():
    if not latest_poll["message_id"] or not latest_poll["end_time"] or latest_poll["pinged"]:
        return
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    time_left = latest_poll["end_time"] - now
    if 0 < time_left <= 3600:  # 1 hour or less left
        channel = bot.get_channel(poll_channel_id)
        if channel is None:
            return
        try:
            poll_message = await channel.fetch_message(latest_poll["message_id"])
        except Exception:
            return
        emoji_list = ["👍", "👎", "📖"]
        reacted_users = set()
        for reaction in poll_message.reactions:
            if str(reaction.emoji) in emoji_list:
                async for user in reaction.users():
                    if not user.bot:
                        reacted_users.add(user.id)
        guild = channel.guild
        unvoted_members = []
        async for member in guild.fetch_members(limit=None):
            if not member.bot and member.id not in reacted_users:
                unvoted_members.append(member.mention)
        if unvoted_members:
            await channel.send(
                f"⏰ Less than 1 hour left to vote! The following users have not voted:\n{', '.join(unvoted_members)}"
            )
        latest_poll["pinged"] = True  # Prevent multiple pings


bot.run(TOKEN)
