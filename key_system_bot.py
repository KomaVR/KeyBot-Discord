import os
import json
import sqlite3
import secrets
import string
from datetime import datetime
from discord.ext import commands
from discord import ui, ButtonStyle
from dotenv import load_dotenv

# Load environment
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
ADMIN_ROLE_NAME = os.getenv('ADMIN_ROLE_NAME', 'KeyManager')
OWNER_ID = int(os.getenv('OWNER_ID', '0'))  # Discord User ID of bot owner (optional)

# Paths
DB_PATH = 'keys.db'
CONFIG_PATH = 'config.json'
KEYS_FILE = 'keys.txt'

# Initialize SQLite
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute('''
    CREATE TABLE IF NOT EXISTS keys (
        key TEXT PRIMARY KEY,
        role_id INTEGER,
        redeemed_by INTEGER,
        redeemed_at TEXT
    )
''')
conn.commit()

# Load or init config
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)
else:
    config = {}

# Bot setup
tree = commands.Bot(command_prefix='!', intents=commands.Intents.default())
tree.intents.members = True

# Helper functions
def generate_key(length: int = 16) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

async def is_admin(user):
    # Check for Admin role or owner
    if OWNER_ID and user.id == OWNER_ID:
        return True
    return any(role.name == ADMIN_ROLE_NAME for role in user.roles)

class KeyGenView(ui.View):
    def __init__(self, role_id):
        super().__init__(timeout=None)
        self.role_id = role_id

    @ui.button(label='Generate Key', style=ButtonStyle.primary)
    async def generate_button(self, interaction: ui.Interaction, button: ui.Button):
        if not await is_admin(interaction.user):
            return await interaction.response.send_message('❌ You are not authorized.', ephemeral=True)
        # Generate and store key
        key = generate_key()
        c.execute('INSERT OR IGNORE INTO keys (key, role_id) VALUES (?, ?)', (key, self.role_id))
        conn.commit()
        # Append to file
        with open(KEYS_FILE, 'a') as f:
            f.write(f"{key}\n")
        await interaction.response.send_message(f'🔑 Key generated: `{key}`', ephemeral=True)

@tree.event
async def on_ready():
    print(f'Logged in as {tree.user}')
    # Register persistent view if set
    if 'panel_message_id' in config and 'channel_id' in config and 'role_id' in config:
        view = KeyGenView(config['role_id'])
        tree.add_view(view, message_id=config['panel_message_id'])

@tree.command(name='setup')
@commands.has_permissions(manage_guild=True)
async def setup(ctx, channel: commands.TextChannel, role: commands.RoleConverter):
    """
    Configure the key-gen panel.
    Usage: !setup #channel @Role
    """
    # Save config\    
    config['channel_id'] = channel.id
    config['role_id'] = role.id
    # Send panel message
    view = KeyGenView(role.id)
    msg = await channel.send(f'📋 Key Generator for role **{role.name}**. Click below to generate keys:', view=view)
    config['panel_message_id'] = msg.id
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f)
    await ctx.send(f'Setup complete! Panel posted in {channel.mention}.')

# Command to view and remove keys file (optional)
@tree.command(name='listkeys')
async def list_keys(ctx):
    """Send the keys file as attachment (Admins only)."""
    if not await is_admin(ctx.author):
        return await ctx.send('❌ Not authorized.')
    if not os.path.exists(KEYS_FILE):
        return await ctx.send('No keys file found.')
    await ctx.send(file=discord.File(KEYS_FILE))

# Keep existing commands if desired...
# bot.run

tree.run(TOKEN)
