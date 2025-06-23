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
TOKEN           = os.getenv('DISCORD_TOKEN')
ADMIN_ROLE_NAME = os.getenv('ADMIN_ROLE_NAME', 'KeyManager')
OWNER_ID        = int(os.getenv('OWNER_ID', '0'))
HMAC_SECRET     = os.getenv('HMAC_SECRET', '')
GIST_TOKEN = os.getenv('KEYS_GIST_TOKEN', '').strip()
GIST_ID    = os.getenv('GIST_ID', '').strip()

# Load or init panel config
config = {}
if os.path.exists('config.json'):
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
    except Exception:
        config = {}

def is_admin(member: discord.Member) -> bool:
    # Synchronous check: owner or has role name ADMIN_ROLE_NAME
    if OWNER_ID and member.id == OWNER_ID:
        return True
    return any(r.name == ADMIN_ROLE_NAME for r in member.roles)

def generate_key(length: int = 12) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def fetch_entries():
    """
    Fetch the current entries from the private gist's keys.txt.
    Returns: (entries_list, gist_object)
    entries_list is a list of dicts: {'key':..., 'role_id':int, 'redeemed_by':str or None, 'redeemed_at':str or None}
    """
    if not GIST_TOKEN or not GIST_ID:
        raise RuntimeError("GIST_TOKEN or GIST_ID not set")
    gh = Github(GIST_TOKEN)
    gist = gh.get_gist(GIST_ID)
    file = gist.files.get('keys.txt')
    lines = []
    if file and file.content is not None:
        lines = file.content.splitlines()
    entries = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split(',', 3)
        while len(parts) < 4:
            parts.append('')
        key, role_id_str, redeemed_by, redeemed_at = parts
        try:
            role_id = int(role_id_str)
        except:
            # Skip malformed line
            continue
        entries.append({
            'key': key,
            'role_id': role_id,
            'redeemed_by': redeemed_by or None,
            'redeemed_at': redeemed_at or None
        })
    return entries, gist

def push_entries(entries, gist):
    """
    Given a list of entry dicts and the gist object, rewrite keys.txt in the gist.
    """
    lines = []
    for e in entries:
        key = e.get('key')
        role_id = e.get('role_id')
        redeemed_by = e.get('redeemed_by') or ''
        redeemed_at = e.get('redeemed_at') or ''
        lines.append(f"{key},{role_id},{redeemed_by},{redeemed_at}")
    content = "\n".join(lines)
    # Edit the gist file
    gist.edit(
        files={'keys.txt': {"content": content}},
        description=f"keys.txt updated @ {datetime.utcnow().isoformat()}",
    )

class KeyGenView(View):
    def __init__(self, role_id: int):
        super().__init__(timeout=None)
        self.role_id = role_id

    @button(label='Generate Key & License', style=ButtonStyle.primary)
    async def generate_button(self, interaction: discord.Interaction, button: Button):
        # Only admins can generate
        if not is_admin(interaction.user):
            await interaction.response.send_message('❌ You are not authorized.', ephemeral=True)
            return

        # Generate a random key
        key = generate_key()
        # Fetch existing entries
        try:
            entries, gist = fetch_entries()
        except Exception as e:
            await interaction.response.send_message(f'❌ Error fetching entries: {e}', ephemeral=True)
            return

        # Append new entry (unredeemed)
        entries.append({
            'key': key,
            'role_id': self.role_id,
            'redeemed_by': None,
            'redeemed_at': None
        })
        # Push updated list back to gist
        try:
            push_entries(entries, gist)
        except Exception as e:
            await interaction.response.send_message(f'❌ Error updating gist: {e}', ephemeral=True)
            return

        # Build license payload & HMAC-sign it
        payload = {
            'key': key,
            'issued_at': datetime.utcnow().isoformat()
        }
        data = json.dumps(payload, separators=(',',':')).encode()
        sig = hmac.new(HMAC_SECRET.encode(), data, hashlib.sha256).hexdigest()
        blob = {'payload': payload, 'signature': sig}
        buf = io.BytesIO(json.dumps(blob).encode())
        buf.name = 'license.lic'

        # DM license file
        try:
            await interaction.user.send(
                content='🔑 Here is your license. Place `license.lic` in the “license” folder next to your DLL.',
                file=discord.File(buf)
            )
            await interaction.response.send_message('✅ License sent via DM!', ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message('❌ Could not DM you—check your privacy settings.', ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f'❌ Failed to send license: {e}', ephemeral=True)

class KeyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # If a panel message was recorded, re-add the view so button persists
        if 'panel_message_id' in config and 'role_id' in config and 'channel_id' in config:
            try:
                channel = self.get_channel(config['channel_id'])
                if channel:
                    self.add_view(KeyGenView(config['role_id']), message_id=config['panel_message_id'])
            except Exception:
                pass
        await self.tree.sync()

bot = KeyBot()

@bot.tree.command(name='setup', description='Configure the key-gen panel')
@app_commands.describe(channel='Text channel for the panel', role='Role to assign on redeem')
async def setup(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    # Only allow Manage Server
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message('❌ You need Manage Server permission.', ephemeral=True)
        return

    # Save config: channel, role, message ID
    config['channel_id'] = channel.id
    config['role_id']    = role.id

    view = KeyGenView(role.id)
    msg = await channel.send(f'📋 Key & License Generator for role **{role.name}**. Click below to generate:', view=view)
    config['panel_message_id'] = msg.id
    # Persist config
    try:
        with open('config.json', 'w') as f:
            json.dump(config, f)
    except Exception:
        pass

    await interaction.response.send_message(f'Setup complete! Panel posted in {channel.mention}.', ephemeral=True)

@bot.tree.command(name='listkeys', description='Show total keys stored')
async def listkeys(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message('❌ You are not authorized.', ephemeral=True)
        return
    # Fetch entries
    try:
        entries, _ = fetch_entries()
    except Exception as e:
        await interaction.response.send_message(f'❌ Error fetching entries: {e}', ephemeral=True)
        return
    count = len(entries)
    # Optionally, DM the full list only to admin, or just show count
    # Here we DM the full CSV as a file for the admin privately
    csv_lines = []
    for e in entries:
        rb = e['redeemed_by'] or ''
        ra = e['redeemed_at'] or ''
        csv_lines.append(f"{e['key']},{e['role_id']},{rb},{ra}")
    # Prepare CSV file in-memory
    bio = io.BytesIO("\n".join(csv_lines).encode())
    bio.name = 'keys.csv'
    try:
        await interaction.user.send(content=f"Total keys: {count}", file=discord.File(bio))
        await interaction.response.send_message('✅ Sent you the full keys list via DM.', ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message('❌ Could not DM you the list—check your privacy settings.', ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f'❌ Failed to send list: {e}', ephemeral=True)

@bot.tree.command(name='redeem', description='Redeem a key to get your role')
@app_commands.describe(key='The license key')
async def redeem(interaction: discord.Interaction, key: str):
    # Fetch entries
    try:
        entries, gist = fetch_entries()
    except Exception as e:
        await interaction.response.send_message(f'❌ Error fetching entries: {e}', ephemeral=True)
        return

    # Find the key
    for e in entries:
        if e['key'] == key:
            # Already redeemed?
            if e['redeemed_by']:
                await interaction.response.send_message('❌ Key already redeemed.', ephemeral=True)
                return
            # Get role
            role = interaction.guild.get_role(e['role_id'])
            if not role:
                await interaction.response.send_message('❌ Role not found on this server.', ephemeral=True)
                return
            # Assign role
            try:
                await interaction.user.add_roles(role)
            except Exception:
                # Could not assign
                await interaction.response.send_message('❌ Failed to assign role; check bot permissions.', ephemeral=True)
                return
            # Mark redeemed
            e['redeemed_by'] = str(interaction.user.id)
            e['redeemed_at'] = datetime.utcnow().isoformat()
            try:
                push_entries(entries, gist)
            except Exception as ex:
                # Role was granted, but gist update failed; inform admin separately?
                await interaction.response.send_message('⚠️ Role assigned, but failed to update redemption status: ' + str(ex), ephemeral=True)
                return
            await interaction.response.send_message(f'✅ Redeemed key and assigned role **{role.name}**!', ephemeral=True)
            return

    # If not found
    await interaction.response.send_message('❌ Invalid key.', ephemeral=True)

bot.run(TOKEN)
