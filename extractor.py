import asyncio
import os
import re
import glob
import base64
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from collections import defaultdict

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8853762167:AAFk3YJsSB-ARNzHoskJWtZ-I4gR6lBl9dg")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "6715159293")


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    try:
        urllib.request.urlopen(url, data, timeout=10)
    except Exception as e:
        print(f"Telegram send failed: {e}")
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (
    User, MessageMediaDocument, MessageService,
    MessageActionPhoneCall, PhoneCallDiscardReasonMissed,
    PhoneCallDiscardReasonBusy, PhoneCallDiscardReasonHangup,
)

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"

TASHKENT = timezone(timedelta(hours=5))

# Shift: 18:00–03:00 Tashkent (crosses midnight)
SHIFT_START = 18
SHIFT_END = 3   # next day

COMPANY_KEYWORDS = ["llc", "inc", "freight", "trucking", "logistics", "group", "transport"]
PHONE_RE = re.compile(r'(\+1[\s\-]?\(?\d{3}\)?\s?\d{3}[\s\-]\d{4}|\b\d{10}\b)')
DEAL_RE = re.compile(r'#[A-Za-z]?\d{3,}')
HANDOFF_WORDS = ["call this", "check", "pls call", "qivorila", "shunga"]
ONE_WORD_RE = re.compile(r'^(ok|okay|sure|yes|👍|ha|хорошо|ок|да)[\s.!]*$', re.IGNORECASE)

MIN_CALL_SECONDS = 60


def in_shift(dt: datetime) -> bool:
    """True if dt (Tashkent time) falls within 18:00–03:00 shift."""
    h = dt.hour
    return h >= SHIFT_START or h < SHIFT_END


def find_sessions():
    return [s.replace(".session", "") for s in glob.glob("*.session")]


def get_client(rep_name: str) -> TelegramClient:
    """Use SESSION_STRING env var on Railway, local .session file otherwise."""
    session_b64 = os.environ.get("SESSION_STRING")
    if session_b64:
        # Decode base64 → write temp .session file → use it
        session_bytes = base64.b64decode(session_b64)
        tmp_path = "/tmp/railway.session"
        with open(tmp_path, "wb") as f:
            f.write(session_bytes)
        return TelegramClient(tmp_path.replace(".session", ""), API_ID, API_HASH)
    return TelegramClient(rep_name, API_ID, API_HASH)


def extract_companies(text: str) -> list[str]:
    words = text.split()
    found = []
    for i, w in enumerate(words):
        if any(kw in w.lower() for kw in COMPANY_KEYWORDS):
            # grab surrounding word as company name
            start = max(0, i - 1)
            end = min(len(words), i + 2)
            found.append(" ".join(words[start:end]))
    return found


def extract_phones(text: str) -> list[str]:
    return PHONE_RE.findall(text)


def extract_deals(text: str) -> list[str]:
    return DEAL_RE.findall(text)


def has_handoff(text: str) -> bool:
    t = text.lower()
    return any(hw in t for hw in HANDOFF_WORDS)


def is_one_word_reply(text: str) -> bool:
    return bool(ONE_WORD_RE.match(text.strip()))


async def extract(rep_name: str):
    client = get_client(rep_name)
    await client.connect()

    if not await client.is_user_authorized():
        print(f"Session '{rep_name}' is not authorized. Run auth.py first.")
        await client.disconnect()
        return

    me = await client.get_me()
    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc - timedelta(hours=24)

    # ── accumulators ─────────────────────────────────────────────────────────
    out_calls, in_calls = [], []          # (duration_sec,)
    out_msg_count = 0
    in_msg_count = 0

    reply_times_shift = []               # minutes
    reply_times_outshift = []

    new_companies, cur_companies = [], []
    new_phones, cur_phones = [], []
    new_deals, cur_deals = [], []
    new_handoffs = cur_handoffs = 0

    one_word_total = 0
    out_msg_with_text = 0

    # For active hours
    out_timestamps = []

    # Track which contacts have history before the 24h window
    seen_contacts_before: set[int] = set()
    seen_contacts_in_period: set[int] = set()

    # First pass: check each dialog for older history to classify new/current
    print("Classifying contacts (new vs current)...")
    async for dialog in client.iter_dialogs():
        if not isinstance(dialog.entity, User):
            continue
        uid = dialog.entity.id
        # Check if there's any message older than cutoff
        async for msg in client.iter_messages(dialog.entity, limit=1, offset_date=cutoff_utc):
            seen_contacts_before.add(uid)
            break

    print("Scanning last 24h messages and calls...")

    # Second pass: collect data within 24h window
    async for dialog in client.iter_dialogs():
        if not isinstance(dialog.entity, User):
            continue
        entity = dialog.entity
        uid = entity.id

        # Collect messages in period, keyed by time for reply-time calc
        period_msgs = []  # (datetime_utc, is_outgoing, text)

        async for message in client.iter_messages(entity, limit=None):
            if message.date < cutoff_utc:
                break

            dt_tash = message.date.astimezone(TASHKENT)

            # ── Phone calls (MessageService) ──────────────────────────────
            if isinstance(message.action, MessageActionPhoneCall):
                action = message.action
                duration = getattr(action, "duration", 0) or 0
                reason = getattr(action, "reason", None)
                # Skip missed/busy calls
                if isinstance(reason, (PhoneCallDiscardReasonMissed, PhoneCallDiscardReasonBusy)):
                    continue
                if duration < MIN_CALL_SECONDS:
                    continue
                if message.out:
                    out_calls.append(duration)
                else:
                    in_calls.append(duration)
                continue

            if message.text is None and not message.media:
                continue

            text = message.text or ""
            is_out = message.out

            period_msgs.append((message.date, is_out, text))

            if is_out:
                out_msg_count += 1
                out_timestamps.append(dt_tash)
                if text:
                    out_msg_with_text += 1
                    if is_one_word_reply(text):
                        one_word_total += 1

                # Entity classification
                is_new = uid not in seen_contacts_before
                target_co = new_companies if is_new else cur_companies
                target_ph = new_phones if is_new else cur_phones
                target_de = new_deals if is_new else cur_deals

                target_co.extend(extract_companies(text))
                target_ph.extend(extract_phones(text))
                target_de.extend(extract_deals(text))
                if has_handoff(text):
                    if is_new:
                        new_handoffs += 1
                    else:
                        cur_handoffs += 1

                seen_contacts_in_period.add(uid)
            else:
                in_msg_count += 1

        # ── Reply time calculation ────────────────────────────────────────
        # Sort period_msgs by time ascending
        period_msgs.sort(key=lambda x: x[0])
        pending_incoming: datetime | None = None

        for dt_utc, is_out, _ in period_msgs:
            dt_tash = dt_utc.astimezone(TASHKENT)
            if not is_out:
                if pending_incoming is None:
                    pending_incoming = dt_utc
            else:
                if pending_incoming is not None:
                    delta_min = (dt_utc - pending_incoming).total_seconds() / 60
                    if in_shift(pending_incoming.astimezone(TASHKENT)):
                        reply_times_shift.append(delta_min)
                    else:
                        reply_times_outshift.append(delta_min)
                    pending_incoming = None

    await client.disconnect()

    # ── Compute stats ─────────────────────────────────────────────────────
    def fmt_calls(lst):
        count = len(lst)
        total_min = sum(lst) // 60
        return count, total_min

    out_c, out_min = fmt_calls(out_calls)
    in_c, in_min = fmt_calls(in_calls)

    def avg_max(lst):
        if not lst:
            return "N/A", "N/A"
        return f"{sum(lst)/len(lst):.0f}", f"{max(lst):.0f}"

    avg_s, max_s = avg_max(reply_times_shift)
    avg_o, max_o = avg_max(reply_times_outshift)

    one_word_ratio = (
        f"{one_word_total * 100 // out_msg_with_text}%"
        if out_msg_with_text else "N/A"
    )

    # Active hours
    if out_timestamps:
        earliest = min(out_timestamps)
        latest = max(out_timestamps)
        active_str = f"{earliest.strftime('%H:%M')}–{latest.strftime('%H:%M')}"
    else:
        active_str = "None"

    # Dead gaps during shift (>2h between consecutive outgoing msgs)
    shift_ts = sorted(t for t in out_timestamps if in_shift(t))
    gaps = 0
    for i in range(1, len(shift_ts)):
        delta = (shift_ts[i] - shift_ts[i - 1]).total_seconds() / 3600
        if delta > 2:
            gaps += 1
    dead_gaps = f"{gaps} gap(s) >2h" if gaps else "None"

    # FLAG logic
    flag = ""
    avg_shift_val = float(avg_s) if avg_s != "N/A" else 0
    ratio_val = int(one_word_ratio.replace("%", "")) if one_word_ratio != "N/A" else 0
    new_co_count = len(set(new_companies))
    new_ph_count = len(set(new_phones))

    if avg_shift_val > 30:
        flag = f"Avg reply time during shift is {avg_s} min — exceeds 30-min threshold."
    elif ratio_val > 50:
        flag = f"One-word reply ratio is {one_word_ratio} — more than half of outgoing messages are non-substantive."
    elif new_co_count == 0 and new_ph_count == 0:
        flag = "Zero new companies and zero phone numbers found — no new client outreach detected."
    else:
        flag = "No critical flags."

    # ── Report ────────────────────────────────────────────────────────────
    date_str = datetime.now(TASHKENT).strftime("%Y-%m-%d")
    os.makedirs("reports", exist_ok=True)
    report_path = os.path.join("reports", f"{date_str}_summary.txt")

    report = f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CALLS
  Outgoing (≥1 min):  {out_c} calls | {out_min} min
  Incoming (≥1 min):  {in_c} calls | {in_min} min

MESSAGES
  Outgoing:           {out_msg_count}
  Incoming:           {in_msg_count}

REPLY TIME
  During shift   (18:00–03:00):  avg {avg_s} min | max {max_s} min
  Out of shift   (03:00–18:00):  avg {avg_o} min | max {max_o} min

MESSAGE ANALYSIS
  New clients:
    Companies mentioned:    {len(set(new_companies))}
    Phone numbers shared:   {len(set(new_phones))}
    Deal references (#ID):  {len(set(new_deals))}
    Handoff requests:       {new_handoffs}

  Current clients:
    Companies mentioned:    {len(set(cur_companies))}
    Phone numbers shared:   {len(set(cur_phones))}
    Deal references (#ID):  {len(set(cur_deals))}
    Handoff requests:       {cur_handoffs}

  One-word reply ratio:     {one_word_ratio}
  Active hours:             {active_str}
  Dead gaps during shift:   {dead_gaps}

FLAG: {flag}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    print(report)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"Rep: {me.first_name} {me.last_name or ''} (@{me.username or 'N/A'})\n")
        f.write(f"Session: {rep_name}\n\n")
        f.write(report)

    print(f"\nReport saved to: {report_path}")

    header = f"<b>Daily Report — {rep_name} — {date_str}</b>\n\n"
    send_telegram(header + f"<pre>{report}</pre>")


async def main():
    # On Railway: REP_NAME env var is required alongside SESSION_STRING
    rep_name = os.environ.get("REP_NAME")
    if rep_name:
        print(f"Railway mode — using REP_NAME={rep_name}")
        await extract(rep_name)
        return

    sessions = find_sessions()
    if not sessions:
        print("No .session files found. Run auth.py first.")
        return

    if len(sessions) == 1:
        rep_name = sessions[0]
        print(f"Using session: {rep_name}")
    else:
        print("Available sessions:")
        for i, s in enumerate(sessions, 1):
            print(f"  {i}. {s}")
        choice = input("Enter number or session name: ").strip()
        rep_name = sessions[int(choice) - 1] if choice.isdigit() else choice

    await extract(rep_name)


if __name__ == "__main__":
    asyncio.run(main())
