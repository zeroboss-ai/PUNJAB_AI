"""
Find your Telegram chat id (one-time helper).

  1. In Telegram, talk to @BotFather: /newbot, copy the token it gives you into .env as TELEGRAM_BOT_TOKEN.
  2. Open YOUR new bot in Telegram, press Start and send it any message ("hi").
  3. Run:  python tools/telegram_chat_id.py
  4. Copy the number it prints into .env as TELEGRAM_CHAT_ID. Restart the terminal.

The bot token is read from .env and is never printed.
"""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not token:
    sys.exit("TELEGRAM_BOT_TOKEN is empty. Put the token from @BotFather in .env first.")
try:
    r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
    data = r.json()
except Exception as e:
    sys.exit(f"Could not reach Telegram: {type(e).__name__}")
if not data.get("ok"):
    sys.exit(f"Telegram said: {str(data.get('description'))[:120]} (is the token right?)")
seen = {}
for u in data.get("result", []):
    m = u.get("message") or u.get("channel_post") or {}
    chat = m.get("chat") or {}
    if chat.get("id") is not None:
        seen[chat["id"]] = chat.get("first_name") or chat.get("title") or chat.get("username") or "?"
if not seen:
    sys.exit("No messages found. Open your bot in Telegram, press Start, send it 'hi', then run this again.")
print("Your chat id(s):")
for cid, name in seen.items():
    print(f"  {cid}   ({name})")
print("\nPut ONE of them in .env as TELEGRAM_CHAT_ID. Only that chat will receive alerts.")
