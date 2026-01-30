# bot.py
# Updated: output as .txt + clearer instructions

import discord
from discord import app_commands
from discord.ext import commands
import re
import io
import textwrap
import aiohttp
import asyncio
import os

# ────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────

PREFIX = "."
MAX_CODE_LENGTH = 4000
MAX_FILE_SIZE   = 512 * 1024
MAX_URL_CONTENT = 300 * 1024

# ────────────────────────────────────────────────
# Deobfuscate function (same as before)
# ────────────────────────────────────────────────

def try_deobfuscate_lua(raw: str) -> str:
    def unesc(m):
        s = m.group(0)
        if s.startswith(r'\x'):
            try: return bytes.fromhex(s[2:]).decode('latin1', errors='replace')
            except: return s
        if s.startswith(r'\u'):
            try: return chr(int(s[2:], 16))
            except: return s
        if s[1:].isdigit():
            try: return chr(int(s[1:]))
            except: return s
        return s

    code = re.sub(r'\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4,6}|\\[0-7]{1,3}|\\.', unesc, raw)

    code = re.sub(r'(["\'])(.*?)\1\s*\.\.\s*(["\'])(.*?)\3',
                  r'\1\2\4\1', code, flags=re.DOTALL)

    code = re.sub(r'(?m)^\s*local\s+[a-zA-Z_]\w*\s*=\s*["\'].*?["\']\s*;', '', code)

    lines = []
    indent = 0
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append('')
            continue

        if stripped.startswith(('else', 'elseif')) or stripped == 'end':
            indent = max(0, indent - 1)

        lines.append('    ' * indent + stripped)

        if any(stripped.startswith(w) for w in ('function', 'if', 'for', 'while', 'repeat', 'do')):
            if not stripped.endswith('end'):
                indent += 1
        if 'then' in stripped and not stripped.endswith('end'):
            indent += 1

    cleaned = '\n'.join(lines)
    final = textwrap.fill(cleaned, width=88,
                          replace_whitespace=False,
                          break_long_words=False,
                          drop_whitespace=False)

    header = (
        "-- Basic cleanup attempt (strings unescaped + light reformat)\n"
        "-- NOT real deobfuscation for Luraph / MoonSec / IronBrew / VM protectors\n"
        "-- Use specialized tools or AI for serious obfuscation\n\n"
    )

    return header + final + "\n\n-- end of cleaned output"


# ────────────────────────────────────────────────
# Fetch raw Lua
# ────────────────────────────────────────────────

async def fetch_raw_lua(url: str) -> str | None:
    headers = {"User-Agent": "LuaDeobfBot/1.0 (Discord)"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as r:
                if r.status != 200:
                    return None
                ct = r.headers.get("content-type", "").lower()
                if "text" not in ct and "lua" not in ct:
                    return None
                data = await r.text(errors='replace')
                if len(data) > MAX_URL_CONTENT:
                    return None
                if not any(w in data.lower() for w in ['function', 'local', 'end', 'return']):
                    return None
                return data
    except:
        return None


# ────────────────────────────────────────────────
# Bot setup
# ────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None,
    case_insensitive=True
)


# ────────────────────────────────────────────────
# Shared processing logic
# ────────────────────────────────────────────────

async def process_dump(ctx_or_inter, arg: str = None, is_slash: bool = False, file: discord.Attachment = None):
    source = ""

    attachment = file if is_slash else (ctx_or_inter.message.attachments[0] if ctx_or_inter.message.attachments else None)

    if attachment:
        if attachment.size > MAX_FILE_SIZE:
            msg = "File too large (max ~500 KB)."
            if is_slash: await ctx_or_inter.followup.send(msg, ephemeral=True)
            else: await ctx_or_inter.send(msg)
            return
        if not attachment.filename.lower().endswith(('.lua', '.luau', '.txt')):
            msg = "Please attach a .lua / .txt file."
            if is_slash: await ctx_or_inter.followup.send(msg, ephemeral=True)
            else: await ctx_or_inter.send(msg)
            return
        try:
            bytesdata = io.BytesIO()
            await attachment.save(bytesdata)
            source = bytesdata.getvalue().decode("utf-8", errors="replace")
        except Exception as e:
            msg = f"Could not read file: {e}"
            if is_slash: await ctx_or_inter.followup.send(msg, ephemeral=True)
            else: await ctx_or_inter.send(msg)
            return

    elif arg and (arg.startswith("http://") or arg.startswith("https://")):
        content = await fetch_raw_lua(arg.strip())
        if content is None:
            msg = "Could not fetch valid raw Lua.\nUse pastebin.com/raw/... etc."
            if is_slash: await ctx_or_inter.followup.send(msg, ephemeral=True)
            else: await ctx_or_inter.send(msg)
            return
        source = content

    elif arg:
        if len(arg) > MAX_CODE_LENGTH:
            msg = "Code too long → attach file or use URL."
            if is_slash: await ctx_or_inter.followup.send(msg, ephemeral=True)
            else: await ctx_or_inter.send(msg)
            return
        source = arg

    else:
        usage = (
            f"**Usage examples:**\n"
            f"`{PREFIX}dump` + attach file\n"
            f"`{PREFIX}dump https://pastebin.com/raw/XXXX`\n"
            f"`{PREFIX}dump local _=...` (small code)\n\n"
            f"Or use `/dump` slash command\n\n"
            f"→ Result sent as **deobfuscated.txt** (right-click → Save Link As to avoid zip issues)"
        )
        if is_slash: await ctx_or_inter.followup.send(usage, ephemeral=True)
        else: await ctx_or_inter.send(usage)
        return

    if not source.strip():
        msg = "No valid Lua code found."
        if is_slash: await ctx_or_inter.followup.send(msg, ephemeral=True)
        else: await ctx_or_inter.send(msg)
        return

    try:
        result = try_deobfuscate_lua(source)
    except Exception as e:
        msg = f"Processing error:\n```{str(e)[:1400]}```"
        if is_slash: await ctx_or_inter.followup.send(msg, ephemeral=True)
        else: await ctx_or_inter.send(msg)
        return

    file_like = io.StringIO(result)
    discord_file = discord.File(file_like, filename="deobfuscated.txt")   # ← changed to .txt

    content_msg = (
        "**Basic cleanup finished**\n"
        "Heavy VM obfuscation needs real tools / AI\n\n"
        "**Tip:** Right-click the file below → Save Link As (don't use 'Download All' to avoid zip filename issues)"
    )

    if is_slash:
        await ctx_or_inter.followup.send(content_msg, file=discord_file)
    else:
        await ctx_or_inter.send(content_msg, file=discord_file, reference=ctx_or_inter.message)


# ────────────────────────────────────────────────
# Prefix .dump
# ────────────────────────────────────────────────

@bot.command(name="dump")
async def prefix_dump(ctx: commands.Context, *, arg: str = None):
    await process_dump(ctx, arg, is_slash=False)


# ────────────────────────────────────────────────
# Slash /dump
# ────────────────────────────────────────────────

@bot.tree.command(name="dump", description="Deobfuscate Lua script (code / url / file)")
@app_commands.describe(
    code="Small Lua code directly (optional)",
    url="Raw URL to Lua script (optional)",
    file="Upload .lua / .txt file (optional)"
)
async def slash_dump(
    interaction: discord.Interaction,
    code: str = None,
    url: str = None,
    file: discord.Attachment = None
):
    await interaction.response.defer(thinking=True)

    arg = url if url else code
    await process_dump(interaction, arg, is_slash=True, file=file)


# ────────────────────────────────────────────────
# Ready event
# ────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}  |  Prefix: {PREFIX}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print("Slash sync failed:", e)
    print("Bot is ready")


if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN environment variable is missing!")
        exit(1)

    bot.run(TOKEN)
