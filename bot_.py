import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask
from threading import Thread
import sqlite3
from datetime import datetime, timedelta
from io import BytesIO
import os
import uuid

# ---------------- CONFIG ----------------
BOT_TOKEN = os.environ.get("DISCORD_TOKEN")  # <- Railway variable
DB_FILE = "keys.db"
OWNER_IDS = [1424707396395339776]

# ---------------- DATABASE ----------------
def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS keys
                     (key TEXT PRIMARY KEY, reward TEXT, created_at TEXT, expires_at TEXT, used_by TEXT, used_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS products
                     (guild_id INTEGER, product_id TEXT, panel_title TEXT, panel_desc TEXT, panel_color INTEGER, panel_emoji TEXT, panel_image TEXT, redeem_role TEXT, script_content TEXT, PRIMARY KEY (guild_id, product_id))''')
        try:
            c.execute("SELECT panel_image FROM products LIMIT 1")
        except sqlite3.OperationalError:
            c.execute("ALTER TABLE products ADD COLUMN panel_image TEXT")
        c.execute('''CREATE TABLE IF NOT EXISTS whitelist
                     (guild_id INTEGER, user_id TEXT, hwid TEXT, PRIMARY KEY (guild_id, user_id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS whitelist_requests
                     (guild_id INTEGER, user_id TEXT, hwid TEXT, requested_at TEXT, PRIMARY KEY (guild_id, user_id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS managers
                     (user_id TEXT PRIMARY KEY)''')
        conn.commit()

init_db()

# ---------------- BOT ----------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

def is_admin_or_owner(interaction: discord.Interaction):
    if interaction.user.id in OWNER_IDS:
        return True
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM managers WHERE user_id=?", (str(interaction.user.id),))
        return bool(c.fetchone())

# ---------------- PANEL UI ----------------
class ProductPanel(discord.ui.View):
    def __init__(self, guild_id: int, product_id: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.product_id = product_id

    @discord.ui.button(label="Redeem Key", style=discord.ButtonStyle.green, emoji="🔑", custom_id="redeem_key")
    async def redeem_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RedeemModal(self.guild_id, self.product_id))

    @discord.ui.button(label="Get Script", style=discord.ButtonStyle.blurple, emoji="📜", custom_id="get_script")
    async def get_script(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT hwid FROM whitelist WHERE guild_id=? AND user_id=?", (self.guild_id, user_id))
            row = c.fetchone()
            if not row:
                await interaction.response.send_message("❌ You are not whitelisted.", ephemeral=True)
                return
            c.execute("SELECT script_content FROM products WHERE guild_id=? AND product_id=?", (self.guild_id, self.product_id))
            product = c.fetchone()
            script_text = product['script_content'] if product else None
            if not script_text:
                await interaction.response.send_message("No script has been set for this product.", ephemeral=True)
                return
            if len(script_text) > 1900:
                file = BytesIO(script_text.encode('utf-8'))
                await interaction.response.send_message(file=discord.File(file, filename=f"{self.product_id}_script.txt"), ephemeral=True)
            else:
                await interaction.response.send_message(f"```lua\n{script_text}\n```", ephemeral=True)

    @discord.ui.button(label="Request Whitelist", style=discord.ButtonStyle.gray, emoji="🔒", custom_id="request_whitelist")
    async def request_whitelist(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WhitelistRequestModal(self.guild_id, self.product_id, str(interaction.user.id)))

# ---------------- MODALS ----------------
class RedeemModal(discord.ui.Modal):
    key_input = discord.ui.TextInput(label="Enter your key", style=discord.TextStyle.short, required=True, max_length=40)

    def __init__(self, guild_id: int, product_id: str):
        super().__init__(title=f"Redeem Key - {product_id}")
        self.guild_id = guild_id
        self.product_id = product_id

    async def on_submit(self, interaction: discord.Interaction):
        key = self.key_input.value.strip().upper()
        user_id = str(interaction.user.id)
        now = datetime.utcnow()
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT reward, expires_at, used_by FROM keys WHERE key=?", (key,))
            row = c.fetchone()
            if not row:
                await interaction.response.send_message("❌ Invalid key.", ephemeral=True)
                return
            if row['reward'] != self.product_id:
                await interaction.response.send_message(f"❌ This key is for another product ({row['reward']}).", ephemeral=True)
                return
            if row['used_by'] and row['used_by'] != user_id:
                await interaction.response.send_message("❌ Key already used.", ephemeral=True)
                return
            if row['expires_at'] and datetime.fromisoformat(row['expires_at']) < now:
                await interaction.response.send_message("❌ Key expired.", ephemeral=True)
                return
            c.execute("INSERT OR REPLACE INTO whitelist (guild_id,user_id,hwid) VALUES (?,?,?)", (self.guild_id, user_id, ""))
            c.execute("UPDATE keys SET used_by=?, used_at=? WHERE key=?", (user_id, now.isoformat(), key))
            conn.commit()
        await interaction.response.send_message(f"✅ Key redeemed! You are now whitelisted for {self.product_id}.", ephemeral=True)

class WhitelistRequestModal(discord.ui.Modal):
    roblox_id_input = discord.ui.TextInput(label="Roblox User ID", placeholder="Enter your Roblox ID.", style=discord.TextStyle.short, required=True, max_length=20)

    def __init__(self, guild_id: int, product_id: str, user_id: str):
        super().__init__(title="Request Whitelist")
        self.guild_id = guild_id
        self.product_id = product_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        roblox_id = self.roblox_id_input.value.strip()
        now = datetime.utcnow()
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO whitelist_requests (guild_id,user_id,hwid,requested_at) VALUES (?,?,?,?)", (self.guild_id, self.user_id, roblox_id, now.isoformat()))
            conn.commit()
        await interaction.response.send_message(f"✅ Whitelist request submitted for Roblox ID: `{roblox_id}`", ephemeral=True)

# ---------------- COMMANDS ----------------
# Your commands remain exactly the same; no changes needed
# ...

# ---------------- ON READY ----------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.tree.sync()

# ---------------- WEB SERVER ----------------
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is running!"

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ Error: DISCORD_TOKEN not found in environment variables.")
    else:
        # Start Flask server in a thread
        Thread(target=lambda: app.run(host='0.0.0.0', port=5000)).start()
        # Start Discord bot
        bot.run(BOT_TOKEN)