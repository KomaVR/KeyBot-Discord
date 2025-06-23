import os
import json
import sqlite3
import secrets
import string
from datetime import datetime
import discord
from discord import app_commands
from discord.ui import View, button, Button
from discord import ButtonStyle
from dotenv import load_dotenv
import hmac
import hashlib
import io

# Load environment
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
ADMIN_ROLE_NAME = os.getenv('ADMIN_ROLE_NAME', 'KeyManager')
OWNER_ID = int(os.getenv('OWNER_ID', '0'))      # Bot owner ID (optional)
HMAC_SECRET = os.getenv('HMAC_SECRET', '')      # Secret for signing licenses

# Paths
DB_PATH = 'keys.db'
CONFIG_PATH = 'config.json'
KEYS_FILE = 'keys.txt'

# Initialize DB (creates keys.db & table if missing)
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute('''
CREATE TABLE IF NOT EXISTS keys (
    key TEXT PRIMARY KEY,
    role_id INTEGER,
    redeemed_by INTEGER,
    redeemed_at TEXT
);
''')
conn.commit()

# Load or init config
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)
else:
    config = {}

# Helper: check admin permissions
async def is_admin(member: discord.Member) -> bool:
    if OWNER_ID and member.id == OWNER_ID:
        return True
    return any(r.name == ADMIN_ROLE_NAME for r in member.roles)

# Key generator: 12-character alphanumeric
def generate_key(length: int = 12) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

class KeyGenView(View):
    def __init__(self, role_id: int):
        super().__init__(timeout=None)
        self.role_id = role_id

    @button(label='Generate Key & License', style=ButtonStyle.primary)
    async def generate_button(self, interaction: discord.Interaction, button: Button):
        if not await is_admin(interaction.user):
            return await interaction.response.send_message('❌ You are not authorized.', ephemeral=True)

        # 1) Generate and store the key
        key = generate_key()
        c.execute('INSERT OR IGNORE INTO keys (key, role_id) VALUES (?, ?)', (key, self.role_id))
        conn.commit()
        with open(KEYS_FILE, 'a') as f:
            f.write(f"{key}\n")

        # 2) Build license payload and sign it
        payload = {
            'key':       key,
            'issued_to': str(interaction.user.id),
            'issued_at': datetime.utcnow().isoformat()
        }
        data = json.dumps(payload, separators=(',',':')).encode()
        signature = hmac.new(HMAC_SECRET.encode(), data, hashlib.sha256).hexdigest()
        license_blob = {
            'payload':   payload,
            'signature': signature
        }

        # 3) DM the user the license file
        buf = io.BytesIO(json.dumps(license_blob).encode())
        buf.name = 'license.lic'
        try:
            await interaction.user.send(
                content='🔑 Here is your license file. Keep it private and place it next to your DLL.',
                file=discord.File(buf)
            )
            await interaction.response.send_message('✅ License sent via DM!', ephemeral=True)
        except Exception:
            await interaction.response.send_message('❌ Could not send DM. Check your privacy settings.', ephemeral=True)

class KeyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Register the persistent button view after a restart
        if 'panel_message_id' in config and 'channel_id' in config and 'role_id' in config:
            self.add_view(KeyGenView(config['role_id']), message_id=config['panel_message_id'])
        # Sync slash commands
        await self.tree.sync()

bot = KeyBot()

@bot.tree.command(name='setup', description='Configure the key-gen panel')
@app_commands.describe(
    channel='Text channel for the panel',
    role='Role to assign upon redemption'
)
async def setup(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message('❌ You need Manage Server permission.', ephemeral=True)

    # Save panel config and post the button
    config['channel_id'] = channel.id
    config['role_id']   = role.id
    view = KeyGenView(role.id)
    msg = await channel.send(
        f'📋 Key & License Generator for role **{role.name}**. Click below:',
        view=view
    )
    config['panel_message_id'] = msg.id
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f)

    await interaction.response.send_message(
        f'Setup complete! Panel posted in {channel.mention}.',
        ephemeral=True
    )

@bot.tree.command(name='redeem', description='Redeem a key to get your role')
@app_commands.describe(key='The key to redeem')
async def redeem(interaction: discord.Interaction, key: str):
    c.execute('SELECT role_id, redeemed_by FROM keys WHERE key = ?', (key,))
    row = c.fetchone()
    if not row:
        return await interaction.response.send_message('Invalid key.', ephemeral=True)
    role_id, redeemed_by = row
    if redeemed_by:
        return await interaction.response.send_message('This key has already been redeemed.', ephemeral=True)

    role = interaction.guild.get_role(role_id)
    if not role:
        return await interaction.response.send_message('Role not found on server.', ephemeral=True)

    await interaction.user.add_roles(role)
    now = datetime.utcnow().isoformat()
    c.execute(
        'UPDATE keys SET redeemed_by = ?, redeemed_at = ? WHERE key = ?',
        (interaction.user.id, now, key)
    )
    conn.commit()
    await interaction.response.send_message(
        f'✅ Successfully redeemed key and added role {role.name}!',
        ephemeral=True
    )

@bot.tree.command(name='listkeys', description='Download the keys file')
async def listkeys(interaction: discord.Interaction):
    if not await is_admin(interaction.user):
        return await interaction.response.send_message('❌ You are not authorized.', ephemeral=True)
    if not os.path.exists(KEYS_FILE):
        return await interaction.response.send_message('No keys file found.', ephemeral=True)

    await interaction.response.send_message(
        '📄 Here are the keys:',
        file=discord.File(KEYS_FILE),
        ephemeral=True
    )

bot.run(TOKEN)
