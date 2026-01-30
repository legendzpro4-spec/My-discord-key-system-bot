# bot.py
# Discord bot: .dump command → basic Lua "deobfuscation" → returns file
# Deploy-ready for Railway.app – uses environment variable for token

import discord
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

PREFIX = "."                          # you can change this
MAX_CODE_LENGTH = 4000
MAX_FILE_SIZE   = 512 * 1024          # ~0.5 MB
MAX_URL_CONTENT = 300 * 1024

# ────────────────────────────────────────────────
# Basic string cleanup + light beautify
# (not real deobfuscation for VM protectors like Luraph/MoonSec)
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
# Fetch raw Lua from URL
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
# .dump command
# ────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None,
    case_insensitive=True
)


@bot.command(name="dump")
async def cmd_dump(ctx: commands.Context, *, arg: str = None):
    lua_source = ""

    # 1. Attached file
    if ctx.message.attachments:
        att = ctx.message.attachments[0]
        if att.size > MAX_FILE_SIZE:
            return await ctx.send("File too large (max ~500 KB).")
        if not att.filename.lower().endswith(('.lua', '.luau', '.txt')):
            return await ctx.send("Please attach a .lua / .txt file.")

        try:
            bytesdata = io.BytesIO()
            await att.save(bytesdata)
            lua_source = bytesdata.getvalue().decode("utf-8", errors="replace")
        except Exception as e:
            return await ctx.send(f"Could not read attachment: {e}")

    # 2. URL
    elif arg and (arg.startswith("http://") or arg.startswith("https://")):
        content = await fetch_raw_lua(arg.strip())
        if content is None:
            return await ctx.send("Could not fetch valid raw Lua code.\nUse pastebin raw / gist raw / rentry raw etc.")
        lua_source = content

    # 3. Direct small code
    elif arg:
        if len(arg) > MAX_CODE_LENGTH:
            return await ctx.send("Code too long → attach file or use URL.")
        lua_source = arg

    else:
        return await ctx.send(
            f"**Usage:**\n"
            f"`{PREFIX}dump` + attach .lua file\n"
            f"`{PREFIX}dump https://pastebin.com/raw/XXXX`\n"
            f"`{PREFIX}dump local a = ...` (small code)\n\n"
            f"→ Result = **deobfuscated.lua** file"
        )

    if not lua_source.strip():
        return await ctx.send("No valid Lua code found.")

    try:
        result = try_deobfuscate_lua(lua_source)
    except Exception as e:
        return await ctx.send(f"Processing error:\n```{str(e)[:1500]}```")

    file_like = io.StringIO(result)
    discord_file = discord.File(file_like, filename="deobfuscated.lua")

    await ctx.send(
        "**Basic cleanup done**\nHeavy VM obfuscation needs real tools / AI",
        file=discord_file,
        reference=ctx.message
    )


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}  |  Prefix: {PREFIX}")
    print("Bot is ready → .dump should work")


# ────────────────────────────────────────────────
# Start bot – token from Railway variable
# ────────────────────────────────────────────────

if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN environment variable is missing!")
        exit(1)

    bot.run(TOKEN)
