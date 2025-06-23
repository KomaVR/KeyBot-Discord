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

load_dotenv()
TOKEN            = os.getenv('DISCORD_TOKEN')
ADMIN_ROLE_NAME  = os.getenv('ADMIN_ROLE_NAME', 'KeyManager')
OWNER_ID         = int(os.getenv('OWNER_ID', '0'))
HMAC_SECRET      = os.getenv('HMAC_SECRET', '')

DB_PATH     = 'keys.db'
CONFIG_PATH = 'config.json'
KEYS_FILE   = 'keys.txt'

conn = sqlite3.connect(DB_PATH)
c    = conn.cursor()
c.execute('''
CREATE TABLE IF NOT EXISTS keys (
    key TEXT PRIMARY KEY,
    role_id INTEGER,
    redeemed_by INTEGER,
    redeemed_at TEXT
);
''')
conn.commit()

config = {}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)

async def is_admin(member: discord.Member) -> bool:
    if OWNER_ID and member.id == OWNER_ID:
        return True
    return any(r.name == ADMIN_ROLE_NAME for r in member.roles)

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
        key = generate_key()
        c.execute('INSERT OR IGNORE INTO keys (key, role_id) VALUES (?, ?)', (key, self.role_id))
        conn.commit()

        # Write to local keys.txt
        with open(KEYS_FILE, 'a') as f:
            f.write(f"{key}\n")

        # Build & send license.lic
        payload = {'key': key}
        data    = json.dumps(payload, separators=(',',':')).encode()
        sig     = hmac.new(HMAC_SECRET.encode(), data, hashlib.sha256).hexdigest()
        blob    = {'payload': payload, 'signature': sig}

        buf = io.BytesIO(json.dumps(blob).encode())
        buf.name = 'license.lic'
        try:
            await interaction.user.send(
                content='🔑 Here is your license. Place license.lic inside the “license” folder next to your DLL.',
                file=discord.File(buf)
            )
            await interaction.response.send_message('✅ License sent via DM!', ephemeral=True)
        except:
            await interaction.response.send_message('❌ Could not DM you—check your privacy settings.', ephemeral=True)

class KeyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if 'panel_message_id' in config and 'role_id' in config:
            self.add_view(KeyGenView(config['role_id']), message_id=config['panel_message_id'])
        await self.tree.sync()

bot = KeyBot()

@bot.tree.command(name='setup', description='Configure the key-gen panel')
@app_commands.describe(channel='Text channel for the panel', role='Role to assign on redeem')
async def setup(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message('❌ You need Manage Server permission.', ephemeral=True)
    config['channel_id'] = channel.id
    config['role_id']    = role.id

    view = KeyGenView(role.id)
    msg  = await channel.send(f'📋 Key & License Generator for **{role.name}**:', view=view)
    config['panel_message_id'] = msg.id

    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f)

    await interaction.response.send_message(f'Setup complete! Panel posted in {channel.mention}.', ephemeral=True)

@bot.tree.command(name='listkeys', description='List all saved keys')
async def listkeys(interaction: discord.Interaction):
    if not await is_admin(interaction.user):
        return await interaction.response.send_message('❌ You are not authorized.', ephemeral=True)

    c.execute('SELECT key, role_id, redeemed_by, redeemed_at FROM keys')
    rows = c.fetchall()
    if not rows:
        return await interaction.response.send_message('No keys stored yet.', ephemeral=True)

    # Rewrite keys.txt from DB
    with open(KEYS_FILE, 'w') as f:
        for key, role_id, redeemed_by, redeemed_at in rows:
            f.write(f"{key},{role_id},{redeemed_by or ''},{redeemed_at or ''}\n")

    await interaction.response.send_message(
        '📄 All keys:',
        file=discord.File(KEYS_FILE),
        ephemeral=True
    )

@bot.tree.command(name='redeem', description='Redeem a key to get your role')
@app_commands.describe(key='The license key')
async def redeem(interaction: discord.Interaction, key: str):
    c.execute('SELECT role_id, redeemed_by FROM keys WHERE key = ?', (key,))
    row = c.fetchone()
    if not row:
        return await interaction.response.send_message('Invalid key.', ephemeral=True)
    role_id, redeemed_by = row
    if redeemed_by:
        return await interaction.response.send_message('Key already redeemed.', ephemeral=True)

    role = interaction.guild.get_role(role_id)
    if not role:
        return await interaction.response.send_message('Role not found.', ephemeral=True)

    await interaction.user.add_roles(role)
    now = datetime.utcnow().isoformat()
    c.execute('UPDATE keys SET redeemed_by = ?, redeemed_at = ? WHERE key = ?', (interaction.user.id, now, key))
    conn.commit()

    await interaction.response.send_message(f'✅ Redeemed and assigned **{role.name}**.', ephemeral=True)

bot.run(TOKEN)
