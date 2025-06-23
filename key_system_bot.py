# key_system_bot.py
import os
import json
import sqlite3
import secrets
import string
from datetime import datetime

import discord
from discord import app_commands, ui, ButtonStyle
from discord.ext import commands
from dotenv import load_dotenv

# ─── Load environment ──────────────────────────────────────────────────────────
load_dotenv()
TOKEN            = os.getenv("DISCORD_TOKEN")
ADMIN_ROLE_NAME  = os.getenv("ADMIN_ROLE_NAME", "KeyManager")
OWNER_ID         = int(os.getenv("OWNER_ID", "0"))

# ─── Paths & DB setup ─────────────────────────────────────────────────────────
DB_PATH     = "keys.db"
CONFIG_PATH = "config.json"
KEYS_FILE   = "keys.txt"

conn   = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS keys (
    key         TEXT   PRIMARY KEY,
    role_id     INTEGER,
    redeemed_by INTEGER,
    redeemed_at TEXT
)
""")
conn.commit()

if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
else:
    config = {}

# ─── Bot & Intents ────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
bot     = commands.Bot(command_prefix="!", intents=intents)

# ─── Helpers ──────────────────────────────────────────────────────────────────
def is_admin(member: discord.Member) -> bool:
    if OWNER_ID and member.id == OWNER_ID:
        return True
    return any(r.name == ADMIN_ROLE_NAME for r in member.roles)

def gen_key(length: int = 16) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

# ─── Persistent Button View ───────────────────────────────────────────────────
class KeyGenView(ui.View):
    def __init__(self, role_id: int):
        super().__init__(timeout=None)
        self.role_id = role_id

    @ui.button(label="Generate Key", style=ButtonStyle.primary)
    async def gen_button(self, interaction: discord.Interaction, button: ui.Button):
        if not is_admin(interaction.user):
            return await interaction.response.send_message("❌ Not authorized", ephemeral=True)

        key = gen_key()
        cursor.execute(
            "INSERT OR IGNORE INTO keys(key, role_id) VALUES(?,?)",
            (key, self.role_id)
        )
        conn.commit()
        with open(KEYS_FILE, "a") as f:
            f.write(key + "\n")

        await interaction.response.send_message(f"🔑 Key generated: `{key}`", ephemeral=True)

# ─── Events ───────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")

    # re-attach persistent view if set
    if {"panel_message_id","channel_id","role_id"} <= set(config.keys()):
        view = KeyGenView(config["role_id"])
        bot.add_view(view, message_id=config["panel_message_id"])

# ─── Slash Commands ───────────────────────────────────────────────────────────
@bot.tree.command(name="setup", description="Configure the key-gen panel")
@app_commands.describe(
    channel="Where to post the Generate Key button",
    role="Role to assign when redeeming"
)
async def setup(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    role:    discord.Role
):
    if not (interaction.user.guild_permissions.manage_guild or interaction.user.id == OWNER_ID):
        return await interaction.response.send_message("❌ You need Manage Guild permissions", ephemeral=True)

    config["channel_id"]       = channel.id
    config["role_id"]          = role.id
    view                       = KeyGenView(role.id)
    msg                        = await channel.send(
        f"📋 Key Generator for **{role.name}** — click below to make new keys!",
        view=view
    )
    config["panel_message_id"] = msg.id

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f)

    await interaction.response.send_message("✅ Setup complete!", ephemeral=True)

@bot.tree.command(name="listkeys", description="Download the keys.txt file")
async def listkeys(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("❌ Not authorized", ephemeral=True)

    if not os.path.exists(KEYS_FILE):
        return await interaction.response.send_message("No keys have been generated yet.", ephemeral=True)

    await interaction.response.send_message("📥 Here are all the keys:", 
        file=discord.File(KEYS_FILE), ephemeral=True)

@bot.tree.command(name="redeem", description="Redeem a key for your role")
@app_commands.describe(key="The key you want to redeem")
async def redeem(interaction: discord.Interaction, key: str):
    cursor.execute("SELECT role_id, redeemed_by FROM keys WHERE key=?", (key,))
    row = cursor.fetchone()
    if not row:
        return await interaction.response.send_message("❌ Invalid key", ephemeral=True)

    role_id, redeemed_by = row
    if redeemed_by:
        return await interaction.response.send_message("❌ This key has already been used", ephemeral=True)

    role = interaction.guild.get_role(role_id)
    if not role:
        return await interaction.response.send_message("❌ Configured role not found", ephemeral=True)

    await interaction.user.add_roles(role)
    cursor.execute(
        "UPDATE keys SET redeemed_by=?, redeemed_at=? WHERE key=?",
        (interaction.user.id, datetime.utcnow().isoformat(), key)
    )
    conn.commit()

    await interaction.response.send_message(f"✅ Successfully redeemed `{key}`!", ephemeral=True)

# ─── Run Bot ──────────────────────────────────────────────────────────────────
bot.run(TOKEN)
