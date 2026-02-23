# radiogrammer/aprs.py
import secrets
import string

_ALLOWED_ID_CHARS = string.ascii_uppercase + string.digits  # safe over RF

def _make_msgid5() -> str:
    return "".join(secrets.choice(_ALLOWED_ID_CHARS) for _ in range(5))

def _normalize_msgid5(msgid: str) -> str:
    # Keep only allowed chars, uppercase, pad or trim to exactly 5
    cleaned = "".join(ch for ch in msgid.upper() if ch in _ALLOWED_ID_CHARS)
    if len(cleaned) >= 5:
        return cleaned[:5]
    # pad deterministically with random chars if too short
    return cleaned + "".join(secrets.choice(_ALLOWED_ID_CHARS) for _ in range(5 - len(cleaned)))

## APRS message info field for text message
def format_aprs_line(
        source_callsign: str,
        dest_addressee: str, 
        text: str, 
        msgid: str | None = None) -> str:
    """
    Produces APRS message *information field* like:
      :NTSGTE   :HELLO{01
    Addressee is padded to 9 chars.
    """

    protocol = "APK005"
    path = "WIDE1-1,WIDE2-1" 
    to9 = (dest_addressee[:9]).ljust(9)
    body = text
    msgid5 = _normalize_msgid5(msgid) if msgid is not None else _make_msgid5()
    body = body + "{" + msgid5
    return f"{source_callsign}>{protocol},{path}::{to9}:{body}"

