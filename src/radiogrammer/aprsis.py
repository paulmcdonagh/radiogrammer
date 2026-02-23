# src/radiogrammer/aprsis.py
from __future__ import annotations

import socket
from radiogrammer.models import AprsMessage
from radiogrammer.helpers import pad9

from typing import Optional, List


# @dataclass
# class AprsMessage:
#     """
#     Normalized APRS message packet (sufficient for your ACK/Ready/Roger/73 flows).
#     """
#     raw: str
#     rf_src: str               # source callsign
#     addressee9: str           # 9-char padded addressee field (e.g. "KC1YMT   ")
#     text: str                 # message text (without {msgid if present)
#     msgid: Optional[str]      # msgid (without braces), if present
#     third_party: bool = False # came in with leading '}' wrapper
#     path: str = ""            # raw path (comma-separated)


# def _pad9(call: str) -> str:
#     return call.strip().upper()[:9].ljust(9)


def _split_msgid(text: str) -> tuple[str, Optional[str]]:
    """
    APRS msgid is carried as '{xxxxx' suffix (no closing brace).
    Return (base_text, msgid)
    """
    if "{" not in text:
        return text, None
    base, tail = text.split("{", 1)
    base = base.strip()
    mid = tail.strip()
    # msgid is typically short alnum; keep up to 10 just in case
    mid = "".join(ch for ch in mid if ch.isalnum())[:10] or None
    return base, mid


def parse_aprsis_line(line: str) -> Optional[AprsMessage]:
    """
    Parse an APRS-IS packet line into an AprsMessage if it's an APRS 'message' packet.
    Examples:
      NTSGTE>APN20H,TCPIP*:...::KC1YMT   :ackA8WT0
      }NTSGTE>APN20H,TCPIP,...::KC1YMT   :Ready to copy 1 radiogram(s){QE2SJ
    """
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    third_party = raw.startswith("}")
    if third_party:
        raw_inner = raw[1:].lstrip()
    else:
        raw_inner = raw

    if ">" not in raw_inner or ":" not in raw_inner:
        return None

    src, rest = raw_inner.split(">", 1)
    src = src.strip().upper()

    # Split header/info on first ':'
    header, info = rest.split(":", 1)
    header = header.strip()
    info = info.rstrip("\r\n")

    # header is like: DEST,PATH,PATH...
    parts = header.split(",")
    # ax25_dest = parts[0]  # not needed for now
    path = ",".join(parts[1:]) if len(parts) > 1 else ""

    # APRS message info field format:
    # :ADDRESSEE9:TEXT{MSGID
    if not info.startswith(":"):
        return None
    if len(info) < 11:
        return None
    if info[10] != ":":
        return None

    addressee9 = info[1:10]  # exactly 9 chars
    text_full = info[11:].strip()

    text, msgid = _split_msgid(text_full)

    return AprsMessage(
        raw=line.rstrip("\r\n"),
        rf_src=src,
        addressee9=addressee9,
        text=text,
        msgid=msgid,
        third_party=third_party,
        path=path,
    )


class APRSISClient:
    """
    Minimal APRS-IS client for receiving message packets (ACKs / Ready-to-copy / Roger / 73).

    - Use passcode=-1 for receive-only.
    - Provide an APRS-IS filter to reduce traffic, e.g. group message filter: "g/KC1YMT"
    """
    def __init__(
        self,
        host: str = "rotate.aprs2.net",
        port: int = 14580,
        *,
        callsign: str,
        passcode: int = -1,
        app_name: str = "radiogrammer",
        app_version: str = "0.1.0",
        filter_expr: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.callsign = callsign.strip().upper()
        self.passcode = int(passcode)
        self.app_name = app_name
        self.app_version = app_version
        self.filter_expr = filter_expr

        self.sock: Optional[socket.socket] = None
        self._rxbuf = bytearray()

    def connect(self, timeout: float = 5.0):
        s = socket.create_connection((self.host, self.port), timeout=timeout)
        s.settimeout(0.2)  # short timeout for polling
        # Low latency: disable Nagle
        try:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

        self.sock = s

        # login line
        login = f"user {self.callsign} pass {self.passcode} vers {self.app_name} {self.app_version}"
        if self.filter_expr:
            login += f" filter {self.filter_expr}"
        login += "\n"
        self.sock.sendall(login.encode("ascii", errors="replace"))

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None
                self._rxbuf.clear()

    def _read_available(self) -> List[str]:
        """
        Read whatever's available and return complete lines (without trailing newline).
        """
        if not self.sock:
            raise RuntimeError("APRS-IS socket not connected")

        lines: List[str] = []
        while True:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                # server closed
                self.close()
                break
            self._rxbuf.extend(chunk)

            while True:
                nl = self._rxbuf.find(b"\n")
                if nl < 0:
                    break
                line = self._rxbuf[:nl].decode("utf-8", errors="replace").rstrip("\r")
                del self._rxbuf[: nl + 1]
                lines.append(line)

            # keep looping to drain; recv will timeout quickly if no more
        return lines

    def poll_messages(self) -> List[AprsMessage]:
        """
        Poll APRS-IS for lines, parse to AprsMessage objects.
        Returns only APRS 'message' packets.
        """
        msgs: List[AprsMessage] = []
        for line in self._read_available():
            m = parse_aprsis_line(line)
            if m:
                msgs.append(m)
        return msgs
