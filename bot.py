import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import time
from datetime import datetime, timedelta

# --- Configuration ---
# IMPORTANT: Replace 'YOUR_BOT_TOKEN' with your actual bot token
# You can get this from the Discord Developer Portal: https://discord.com/developers/applications
# It's recommended to use environment variables for security, but for simplicity, we'll place it here for now.
# Example using environment variable: TOKEN = os.getenv('DISCORD_BOT_TOKEN')
TOKEN = 'API KEY'
STATS_FILE = 'stats.json'
SAVE_INTERVAL_SECONDS = 60 # How often to save stats to the file

# --- Bot Setup ---
# Define necessary intents
intents = discord.Intents.default()
intents.voice_states = True # Needed for tracking voice channel activity
intents.members = True      # Needed to get member objects for commands

# Create bot instance
bot = commands.Bot(command_prefix="!", intents=intents) # Prefix isn't used for slash commands but is required

# --- Data Handling ---
user_stats = {}
# Dictionary to keep track of when users joined a voice channel {user_id: join_timestamp}
voice_join_times = {}
# Dictionary to keep track of when users started streaming {user_id: stream_start_timestamp}
stream_start_times = {}

def load_stats():
    """Loads stats from the JSON file."""
    global user_stats
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, 'r') as f:
                user_stats = json.load(f)
                # Convert keys back to integers if necessary (JSON saves keys as strings)
                user_stats = {int(k): v for k, v in user_stats.items()}
            print(f"Loaded stats from {STATS_FILE}")
        except json.JSONDecodeError:
            print(f"Error reading {STATS_FILE}. Starting with empty stats.")
            user_stats = {}
        except Exception as e:
            print(f"An unexpected error occurred loading stats: {e}")
            user_stats = {}
    else:
        print(f"{STATS_FILE} not found. Starting with empty stats.")
        user_stats = {}

def save_stats():
    """Saves the current stats to the JSON file."""
    try:
        with open(STATS_FILE, 'w') as f:
            # Convert keys to strings for JSON compatibility
            stats_to_save = {str(k): v for k, v in user_stats.items()}
            json.dump(stats_to_save, f, indent=4)
        # print(f"Stats saved to {STATS_FILE}") # Optional: uncomment for debugging
    except Exception as e:
        print(f"Error saving stats to {STATS_FILE}: {e}")

@tasks.loop(seconds=SAVE_INTERVAL_SECONDS)
async def save_stats_task():
    """Background task to periodically save stats."""
    save_stats()

# --- Helper Functions ---
def get_user_stat(user_id, stat_name, default=0):
    """Safely gets a specific stat for a user, initializing if needed."""
    user_id = int(user_id) # Ensure user_id is integer
    if user_id not in user_stats:
        user_stats[user_id] = {"call_time": 0, "stream_time": 0, "join_count": 0}
    return user_stats[user_id].get(stat_name, default)

def increment_user_stat(user_id, stat_name, value=1):
    """Increments a specific stat for a user."""
    user_id = int(user_id)
    current_value = get_user_stat(user_id, stat_name)
    if user_id not in user_stats: # Initialize if first time
         user_stats[user_id] = {"call_time": 0, "stream_time": 0, "join_count": 0}
    user_stats[user_id][stat_name] = current_value + value

def format_duration(seconds):
    """Formats seconds into a human-readable string (e.g., 1h 23m 45s)."""
    if seconds < 0: seconds = 0 # Avoid negative durations
    delta = timedelta(seconds=int(seconds))
    return str(delta)

# --- Event Handling ---
@bot.event
async def on_ready():
    """Called when the bot is ready and connected to Discord."""
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    load_stats()
    save_stats_task.start() # Start the periodic saving task
    try:
        # Sync slash commands globally. Can take up to an hour for global sync.
        # For faster testing, sync to a specific guild:
        # synced = await bot.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Error syncing commands: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    """Called when a member's voice state changes."""
    user_id = member.id

    # --- Join/Leave Tracking ---
    # User joined a voice channel (was not in one before, is now)
    if before.channel is None and after.channel is not None:
        print(f"{member.display_name} joined voice channel {after.channel.name}")
        voice_join_times[user_id] = time.time()
        increment_user_stat(user_id, "join_count")

    # User left a voice channel (was in one before, is not now)
    elif before.channel is not None and after.channel is None:
        print(f"{member.display_name} left voice channel {before.channel.name}")
        if user_id in voice_join_times:
            join_time = voice_join_times.pop(user_id)
            duration = time.time() - join_time
            increment_user_stat(user_id, "call_time", duration)
            print(f"Added {duration:.2f}s call time for {member.display_name}")
        # Also handle if they were streaming when they left
        if user_id in stream_start_times:
            start_time = stream_start_times.pop(user_id)
            duration = time.time() - start_time
            increment_user_stat(user_id, "stream_time", duration)
            print(f"Stopped tracking stream time for {member.display_name} (left call)")


    # --- Streaming Tracking ---
    # User started streaming (wasn't streaming, now is)
    if not before.self_stream and after.self_stream:
        print(f"{member.display_name} started streaming in {after.channel.name}")
        stream_start_times[user_id] = time.time()

    # User stopped streaming (was streaming, now isn't, but still in call)
    elif before.self_stream and not after.self_stream and after.channel is not None:
        print(f"{member.display_name} stopped streaming in {after.channel.name}")
        if user_id in stream_start_times:
            start_time = stream_start_times.pop(user_id)
            duration = time.time() - start_time
            increment_user_stat(user_id, "stream_time", duration)
            print(f"Added {duration:.2f}s stream time for {member.display_name}")

# --- Slash Commands ---
@bot.tree.command(name="calltime", description="Shows the total time a user has spent in voice calls.")
@app_commands.describe(user="The user to check the call time for.")
async def calltime(interaction: discord.Interaction, user: discord.Member):
    """Slash command to display call time."""
    await interaction.response.defer(ephemeral=True) # Acknowledge command, hide response initially
    total_seconds = get_user_stat(user.id, "call_time")
    # Add time for current session if user is currently in a call
    if user.id in voice_join_times:
        current_session_duration = time.time() - voice_join_times[user.id]
        total_seconds += current_session_duration

    formatted_time = format_duration(total_seconds)
    await interaction.followup.send(f"{user.display_name} has spent **{formatted_time}** in voice calls.", ephemeral=True)

@bot.tree.command(name="streamtime", description="Shows the total time a user has spent streaming in calls.")
@app_commands.describe(user="The user to check the stream time for.")
async def streamtime(interaction: discord.Interaction, user: discord.Member):
    """Slash command to display stream time."""
    await interaction.response.defer(ephemeral=True)
    total_seconds = get_user_stat(user.id, "stream_time")
     # Add time for current session if user is currently streaming
    if user.id in stream_start_times:
        current_session_duration = time.time() - stream_start_times[user.id]
        total_seconds += current_session_duration

    formatted_time = format_duration(total_seconds)
    await interaction.followup.send(f"{user.display_name} has spent **{formatted_time}** streaming.", ephemeral=True)

@bot.tree.command(name="joincount", description="Shows how many times a user has joined a voice call.")
@app_commands.describe(user="The user to check the join count for.")
async def joincount(interaction: discord.Interaction, user: discord.Member):
    """Slash command to display join count."""
    await interaction.response.defer(ephemeral=True)
    count = get_user_stat(user.id, "join_count")
    await interaction.followup.send(f"{user.display_name} has joined voice calls **{count}** times.", ephemeral=True)

# --- Bot Execution ---
if __name__ == "__main__":
    if TOKEN == 'YOUR_BOT_TOKEN':
        print("ERROR: Please replace 'YOUR_BOT_TOKEN' in bot.py with your actual Discord bot token.")
    else:
        try:
            bot.run(TOKEN)
        except discord.LoginFailure:
            print("ERROR: Invalid Discord bot token provided. Please check your token in bot.py.")
        except Exception as e:
            print(f"An error occurred while running the bot: {e}")
        finally:
            # Ensure stats are saved when the bot stops unexpectedly
            print("Bot shutting down. Saving final stats...")
            save_stats()
            print("Stats saved.")
