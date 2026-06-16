"""
Run this locally ONCE after auth.py to get the Telethon StringSession.
Copy the output and paste it into Railway as SESSION_STRING env variable.
"""
import asyncio
import glob
import sys

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"


async def main():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    sessions = glob.glob("*.session")
    if not sessions:
        print("No .session files found. Run auth.py first.")
        sys.exit(1)

    if len(sessions) == 1:
        path = sessions[0]
    else:
        for i, s in enumerate(sessions, 1):
            print(f"  {i}. {s}")
        choice = input("Pick number: ").strip()
        path = sessions[int(choice) - 1]

    rep_name = path.replace(".session", "")

    # Load existing session and export as StringSession
    client = TelegramClient(rep_name, API_ID, API_HASH)
    await client.connect()

    string_session = StringSession.save(client.session)
    await client.disconnect()

    print(f"\nREP_NAME={rep_name}")
    print(f"SESSION_STRING={string_session}")
    print("\nPaste both of these as environment variables in Railway.")


asyncio.run(main())
