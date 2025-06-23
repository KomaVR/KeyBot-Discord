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
from github import Github, InputFileContent, GithubException

load_dotenv()
TOKEN           = os.getenv('DISCORD_TOKEN')
ADMIN_ROLE_NAME = os.getenv('ADMIN_ROLE_NAME', 'KeyManager')
OWNER_ID        = int(os.getenv('OWNER_ID', '0'))
HMAC_SECRET     = os.getenv('HMAC_SECRET', '')
GIST_TOKEN      = os.getenv('KEYS_GIST_TOKEN')
GIST_ID         = os.getenv('GIST_ID')

# Load or init panel config
config = {}
if os.path.exists('config.json'):
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
    except Exception:
        config = {}

def is_admin(member: discord.Member) -> bool:
    if OWNER_ID and member.id == OWNER_ID:
        return True
    return any(r.name == ADMIN_ROLE_NAME for r in member.roles)

def generate_key(length: int = 12) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def fetch_entries():
    """
    Returns: (keys_list, gist_object)
    gist file 'keys.txt' should contain one key per line.
    """
    if not GIST_TOKEN or not GIST_ID:
        raise RuntimeError("GIST_TOKEN or GIST_ID not set")
    gh = Github(GIST_TOKEN)
    try:
        gist = gh.get_gist(GIST_ID)
    except Exception as e:
        raise RuntimeError(f"Failed to fetch gist: {e}")
    file = gist.files.get('keys.txt')
    lines = []
    if file and file.content is not None:
        for ln in file.content.splitlines():
            ln = ln.strip()
            if ln:
                lines.append(ln)
    return lines, gist

def push_entries(keys, gist):
    """
    keys: list of key strings.
    Overwrite gist 'keys.txt' so that each key is on its own line.
    """
    content = "\n".join(keys)
    try:
        gist.edit(
            description=f"keys.txt updated @ {datetime.utcnow().isoformat()}",
            files={'keys.txt': InputFileContent(content)}
        )
    except GithubException as e:
        raise RuntimeError(f"GitHub API error updating gist: status={getattr(e,'status','?')} data={getattr(e,'data',e)}")
    except Exception as e:
        raise RuntimeError(f"Unexpected error updating gist: {e}")

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
        try:
            keys, gist = fetch_entries()
        except Exception as e:
            await interaction.response.send_message(f'❌ Error fetching entries: {e}', ephemeral=True)
            return

        # append new key
        keys.append(key)
        try:
            push_entries(keys, gist)
        except Exception as e:
            await interaction.response.send_message(f'❌ Error updating gist: {e}', ephemeral=True)
            return

        # Build license payload & HMAC-sign it
        # We sign only {"key": key} so Node verifier matches exactly that.
        issued_at = datetime.utcnow().isoformat()
        payload = {'key': key}
        data = json.dumps(payload, separators=(',',':')).encode()
        sig = hmac.new(HMAC_SECRET.encode(), data, hashlib.sha256).hexdigest()

        # Include issued_at metadata separately (not part of HMAC)
        blob = {
            'payload': payload,
            'issued_at': issued_at,
            'signature': sig
        }
        buf = io.BytesIO(json.dumps(blob).encode())
        buf.name = 'license.lic'

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
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message('❌ You need Manage Server permission.', ephemeral=True)
        return

    config['channel_id'] = channel.id
    config['role_id']    = role.id

    view = KeyGenView(role.id)
    msg = await channel.send(f'📋 Key Generator for role **{role.name}**. Click below to generate:', view=view)
    config['panel_message_id'] = msg.id
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
    try:
        keys, _ = fetch_entries()
    except Exception as e:
        await interaction.response.send_message(f'❌ Error fetching entries: {e}', ephemeral=True)
        return
    count = len(keys)
    bio = io.BytesIO("\n".join(keys).encode())
    bio.name = 'keys.txt'
    try:
        await interaction.user.send(content=f"Total unredeemed keys: {count}", file=discord.File(bio))
        await interaction.response.send_message('✅ Sent you the keys via DM.', ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message('❌ Could not DM you the list—check your privacy settings.', ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f'❌ Failed to send list: {e}', ephemeral=True)

@bot.tree.command(name='redeem', description='Redeem a key to get your role')
@app_commands.describe(key='The license key')
async def redeem(interaction: discord.Interaction, key: str):
    try:
        keys, gist = fetch_entries()
    except Exception as e:
        await interaction.response.send_message(f'❌ Error fetching entries: {e}', ephemeral=True)
        return

    if key in keys:
        role = interaction.guild.get_role(config.get('role_id'))
        if not role:
            await interaction.response.send_message('❌ Role not found on this server.', ephemeral=True)
            return
        try:
            await interaction.user.add_roles(role)
        except Exception:
            await interaction.response.send_message('❌ Failed to assign role; check bot permissions.', ephemeral=True)
            return
        # remove key so it can't be reused
        keys.remove(key)
        try:
            push_entries(keys, gist)
        except Exception as ex:
            await interaction.response.send_message('⚠️ Role assigned, but failed to update gist: ' + str(ex), ephemeral=True)
            return
        await interaction.response.send_message(f'✅ Redeemed key and assigned role **{role.name}**!', ephemeral=True)
    else:
        await interaction.response.send_message('❌ Invalid or already redeemed key.', ephemeral=True)

bot.run(TOKEN)
