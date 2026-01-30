# bot.py
# Discord Lua "deobfuscator" bot (.dump + /dump)
# Basic string unescaping + concatenation collapsing + junk removal
# Outputs as deobfuscated.txt
# Deploy on Railway: set DISCORD_BOT_TOKEN variable

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
MAX_FILE_SIZE   = 512 * 1024          # ~0.5 MB
MAX_URL_CONTENT = 300 * 1024

# ────────────────────────────────────────────────
# Cleanup function (multi-pass unescape + concat + junk removal)
# ────────────────────────────────────────────────

def try_deobfuscate_lua(raw: str) -> str:
    code = raw.strip()

    # Aggressive multi-pass unescape
    def unescape_match(m):
        s = m.group(0)
        try:
            if s.startswith(r'\x'):
                return bytes.fromhex(s[2:]).decode('utf-8', errors='replace')
            if s.startswith(r'\u'):
                return chr(int(s[2:], 16))
            if s[1:].isdigit() and len(s[1:]) <= 3:
                return chr(int(s[1:]))
            return s
        except:
            return s

    for _ in range(10):
        code = re.sub(r'\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4,6}|\\[0-7]{1,3}|\\.', unescape_match, code)

    # Collapse string concatenations (repeat until no more changes)
    prev = ""
    while '..' in code and code != prev:
        prev = code
        code = re.sub(r'(["\'])(.*?)\1\s*\.\.\s*(["\'])(.*?)\3', r'\1\2\4\1', code, flags=re.DOTALL)

    # Remove common junk patterns
    code = re.sub(r'(?m)^\s*(local\s+)?[a-zA-Z_]\w*\s*=\s*["\'].*?["\']\s*;', '', code)
    code = re.sub(r'(?m)^[a-zA-Z_]\w*\s*=\s*["\'].*?["\']\s*;', '', code)
    code = re.sub(r'(?ms)if\s+(false|nil)\s+then.*?end\s*(else.*?end)?', '', code)

    # Simple indentation attempt
    lines = []
    indent = 0
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append('')
            continue

        if stripped in ('end', 'else', 'elseif', 'until'):
            indent = max(0, indent - 1)

        lines.append('  ' * indent + stripped)

        if any(stripped.startswith(k) for k in ('function', 'if', 'for', 'while', 'repeat', 'do')):
            if not stripped.endswith(('end', 'do', 'then')):
                indent += 1

    cleaned = '\n'.join(lines)

    # Final line wrapping
    final = textwrap.fill(cleaned, width=100,
                          replace_whitespace=False,
                          break_long_words=False)

    header = (
        "-- Basic deobfuscation attempt (WeAreDevs / light Prometheus style)\n"
        "-- Unescaped strings, collapsed concatenations, removed some junk\n"
        "-- Heavy VM obfuscators (MoonSec V3, IronBrew, Luraph, PSU) will still look messy\n"
        "-- Open in VS Code / Notepad++ → Encoding → UTF-8 if you see garbled text\n\n"
    )

    return header + final + "\n\n-- end of cleaned output"


# ────────────────────────────────────────────────
# Fetch raw content from URL
# ────────────────────────────────────────────────

async def fetch_raw_lua(url: str) -> str | None:
    headers = {"User-Agent": "LuaDeobfBot/1.0"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    return None
                ct = resp.headers.get("content-type", "").lower()
                if "text" not in ct and "lua" not in ct:
                    return None
                text = await resp.text(errors='replace')
                if len(text) > MAX_URL_CONTENT:
                    return None
                if not any(kw in text.lower() for kw in ['function', 'local', 'end', 'return']):
                    return None
                return text
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

async def process_dump(ctx_or_inter, arg: str = None, is_slash: bool = False, attachment: discord.Attachment = None):
    source = ""

    # File
    att = attachment if is_slash else (ctx_or_inter.message.attachments[0] if ctx_or_inter.message.attachments else None)
    if att:
        if att.size > MAX_FILE_SIZE:
            msg = "File too large (max ~500 KB)."
            if is_slash: await ctx_or_inter.followup.send(msg, ephemeral=True)
            else: await ctx_or_inter.send(msg)
            return
        if not att.filename.lower().endswith(('.lua', '.luau', '.txt')):
            msg = "Please attach a .lua / .txt file."
            if is_slash: await ctx_or_inter.followup.send(msg, ephemeral=True)
            else: await ctx_or_inter.send(msg)
            return
        try:
            buf = io.BytesIO()
            await att.save(buf)
            source = buf.getvalue().decode("utf-8", errors="replace")
        except Exception as e:
            msg = f"Failed to read file: {e}"
            if is_slash: await ctx_or_inter.followup.send(msg, ephemeral=True)
            else: await ctx_or_inter.send(msg)
            return

    # URL
    elif arg and (arg.startswith("http://") or arg.startswith("https://")):
        content = await fetch_raw_lua(arg.strip())
        if content:
            source = content
        else:
            msg = "Could not fetch valid Lua from that URL.\nTry pastebin raw / gist raw / rentry raw."
            if is_slash: await ctx_or_inter.followup.send(msg, ephemeral=True)
            else: await ctx_or_inter.send(msg)
            return

    # Pasted code
    elif arg:
        if len(arg) > MAX_CODE_LENGTH:
            msg = "Code too long → attach file or use URL instead."
            if is_slash: await ctx_or_inter.followup.send(msg, ephemeral=True)
            else: await ctx_or_inter.send(msg)
            return
        source = arg

    # Nothing
    else:
        usage = (
            f"**How to use:**\n"
            f"• `{PREFIX}dump` + attach .lua file\n"
            f"• `{PREFIX}dump https://pastebin.com/raw/XXXX`\n"
            f"• `{PREFIX}dump local a = ...` (small code)\n\n"
            f"Or use the `/dump` slash command\n\n"
            f"→ Result comes as **deobfuscated.txt**"
        )
        if is_slash: await ctx_or_inter.followup.send(usage, ephemeral=True)
        else: await ctx_or_inter.send(usage)
        return

    if not source.strip():
        msg = "No valid Lua code found."
        if is_slash: await ctx_or_inter.followup.send(msg, ephemeral=True)
        else: await ctx_or_inter.send(msg)
        return

    result = try_deobfuscate_lua(source)

    file_io = io.StringIO(result)
    discord_file = discord.File(file_io, filename="deobfuscated.txt")

    reply = (
        "**Cleanup finished**\n"
        "This is a basic attempt — heavy VM obfuscation (MoonSec V3, IronBrew, Luraph...) "
        "will still look messy or broken.\n\n"
        "**Tip:** Right-click the file below → Save Link As\n"
        "Open in VS Code / Notepad++ → Encoding → UTF-8 if text looks garbled."
    )

    if is_slash:
        await ctx_or_inter.followup.send(reply, file=discord_file)
    else:
        await ctx_or_inter.send(reply, file=discord_file, reference=ctx_or_inter.message)


# ────────────────────────────────────────────────
# Prefix command: .dump
# ────────────────────────────────────────────────

@bot.command(name="dump")
async def prefix_dump(ctx: commands.Context, *, arg: str = None):
    await process_dump(ctx, arg, is_slash=False)


# ────────────────────────────────────────────────
# Slash command: /dump
# ────────────────────────────────────────────────

@bot.tree.command(name="dump", description="Basic Lua cleanup (file / url / pasted code)")
@app_commands.describe(
    code="Paste small code directly (optional)",
    url="Raw link to Lua script (optional)",
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
    await process_dump(interaction, arg, is_slash=True, attachment=file)


# ────────────────────────────────────────────────
# Startup
# ────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} | Prefix: {PREFIX}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Slash sync failed: {e}")
    print("Bot ready")


if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN environment variable not set")
        exit(1)
    bot.run(TOKEN)
