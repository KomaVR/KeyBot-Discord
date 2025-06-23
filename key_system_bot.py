import os
import json
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
from github import Github

load_dotenv()
TOKEN            = os.getenv('DISCORD_TOKEN')
ADMIN_ROLE_NAME  = os.getenv('ADMIN_ROLE_NAME', 'KeyManager')
OWNER_ID         = int(os.getenv('OWNER_ID', '0'))
HMAC_SECRET      = os.getenv('HMAC_SECRET', '')
GIST_TOKEN       = os.getenv('KEYS_GIST_TOKEN')
GIST_ID          = os.getenv('GIST_ID')

config = {}
if os.path.exists('config.json'):
    with open('config.json', 'r') as f:
        config = json.load(f)

def is_admin(member: discord.Member) -> bool:
    if OWNER_ID and member.id == OWNER_ID:
        return True
    return any(r.name == ADMIN_ROLE_NAME for r in member.roles)

def generate_key(length: int = 12) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def fetch_entries():
    gh = Github(GIST_TOKEN)
    gist = gh.get_gist(GIST_ID)
    file = gist.files.get('keys.txt')
    lines = file.content.splitlines() if file and file.content else []
    entries = []
    for line in lines:
        if not line or line.startswith('#'):
            continue
        parts = line.split(',', 3)
        while len(parts) < 4:
            parts.append('')
        key, role_id, redeemed_by, redeemed_at = parts
        entries.append({
            'key': key,
            'role_id': int(role_id),
            'redeemed_by': redeemed_by or None,
            'redeemed_at': redeemed_at or None
        })
    return entries, gist

def push_entries(entries, gist):
    lines = []
    for e in entries:
        rb = e['redeemed_by'] or ''
        ra = e['redeemed_at'] or ''
        lines.append(f"{e['key']},{e['role_id']},{rb},{ra}")
    content = "\n".join(lines)
    gist.edit(
        files={'keys.txt': {"content": content}},
        description=f"keys.txt updated @ {datetime.utcnow().isoformat()}"
    )

class KeyGenView(View):
    def __init__(self, role_id: int):
        super().__init__(timeout=None)
        self.role_id = role_id

    @button(label='Generate Key & License', style=ButtonStyle.primary)
    async def generate_button(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message('❌ You are not authorized.', ephemeral=True)
            return
        key = generate_key()
        entries, gist = fetch_entries()
        entries.append({
            'key': key,
            'role_id': self.role_id,
            'redeemed_by': None,
            'redeemed_at': None
        })
        push_entries(entries, gist)
        payload = {'key': key}
        data    = json.dumps(payload, separators=(',',':')).encode()
        sig     = hmac.new(HMAC_SECRET.encode(), data, hashlib.sha256).hexdigest()
        blob    = {'payload': payload, 'signature': sig}
        buf = io.BytesIO(json.dumps(blob).encode())
        buf.name = 'license.lic'
        try:
            await interaction.user.send(
                content='🔑 Here is your license. Place license.lic in the “license” folder next to your DLL.',
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
        await interaction.response.send_message('❌ You need Manage Server permission.', ephemeral=True)
        return
    config['channel_id'] = channel.id
    config['role_id']    = role.id
    view = KeyGenView(role.id)
    msg  = await channel.send(f'📋 Key & License Generator for **{role.name}**:', view=view)
    config['panel_message_id'] = msg.id
    with open('config.json', 'w') as f:
        json.dump(config, f)
    await interaction.response.send_message(f'Setup complete! Panel posted in {channel.mention}.', ephemeral=True)

@bot.tree.command(name='listkeys', description='List all saved keys')
async def listkeys(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message('❌ You are not authorized.', ephemeral=True)
        return
    entries, gist = fetch_entries()
    count = len(entries)
    await interaction.response.send_message(f'Total keys stored: {count}', ephemeral=True)

@bot.tree.command(name='redeem', description='Redeem a key to get your role')
@app_commands.describe(key='The license key')
async def redeem(interaction: discord.Interaction, key: str):
    if not is_admin(interaction.user) and False:
        pass
    entries, gist = fetch_entries()
    for e in entries:
        if e['key'] == key:
            if e['redeemed_by']:
                await interaction.response.send_message('Key already redeemed.', ephemeral=True)
                return
            role = interaction.guild.get_role(e['role_id'])
            if not role:
                await interaction.response.send_message('Role not found.', ephemeral=True)
                return
            await interaction.user.add_roles(role)
            now = datetime.utcnow().isoformat()
            e['redeemed_by'] = str(interaction.user.id)
            e['redeemed_at'] = now
            push_entries(entries, gist)
            await interaction.response.send_message(f'✅ Redeemed and assigned **{role.name}**.', ephemeral=True)
            return
    await interaction.response.send_message('Invalid key.', ephemeral=True)

bot.run(TOKEN)
