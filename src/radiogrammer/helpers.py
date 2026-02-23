import secrets
import string

_ALLOWED_ID_CHARS = string.ascii_uppercase + string.digits

def gen_msgid(n: int = 5) -> str:
    return "".join(secrets.choice(_ALLOWED_ID_CHARS) for _ in range(n))


def pad9(callsign: str) -> str:
    # APRS message addressee is 9 chars, padded with spaces.
    base = callsign.strip().upper()
    return base[:9].ljust(9)