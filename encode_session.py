"""
Run this locally ONCE after auth.py to get the base64 session string.
Copy the output and paste it into Railway as SESSION_STRING env variable.
"""
import base64, glob, sys

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

with open(path, "rb") as f:
    encoded = base64.b64encode(f.read()).decode()

name = path.replace(".session", "")
print(f"\nREP_NAME={name}")
print(f"SESSION_STRING={encoded}")
print("\nPaste both of these as environment variables in Railway.")
