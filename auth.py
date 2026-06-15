import asyncio
import os
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError


API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"


async def authorize():
    phone = input("Enter phone number (with country code, e.g. +12025551234): ").strip()

    rep_name = input("Enter rep name for session file (e.g. john_doe): ").strip()
    session_path = f"{rep_name}.session"

    client = TelegramClient(rep_name, API_ID, API_HASH)

    await client.connect()

    if await client.is_user_authorized():
        print(f"Already authorized. Session saved as '{session_path}'.")
        await client.disconnect()
        return

    await client.send_code_request(phone)
    print(f"OTP sent to {phone} via Telegram.")

    otp = input("Enter the OTP you received: ").strip()

    try:
        await client.sign_in(phone, otp)
    except SessionPasswordNeededError:
        password = input("Two-step verification is enabled. Enter your password: ").strip()
        await client.sign_in(password=password)

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"\nAuthorization successful!")
        print(f"  Logged in as: {me.first_name} {me.last_name or ''} (@{me.username or 'no username'})")
        print(f"  Session saved as: '{session_path}'")
    else:
        print("Authorization failed.")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(authorize())
