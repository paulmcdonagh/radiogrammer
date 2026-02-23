# radiogrammer/kiss.py
import socket
from dataclasses import dataclass
from typing import List, Tuple, Optional

FEND  = 0xC0
FESC  = 0xDB
TFEND = 0xDC
TFESC = 0xDD

def kiss_escape(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        if b == FEND:
            out.extend([FESC, TFEND])
        elif b == FESC:
            out.extend([FESC, TFESC])
        else:
            out.append(b)
    return bytes(out)

def kiss_unescape(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == FESC and i + 1 < len(data):
            nb = data[i + 1]
            if nb == TFEND:
                out.append(FEND)
                i += 2
                continue
            if nb == TFESC:
                out.append(FESC)
                i += 2
                continue
        out.append(b)
        i += 1
    return bytes(out)

def kiss_frame(ax25_frame: bytes, port: int = 0) -> bytes:
    cmd = (port & 0x0F) << 4  # port in high nibble, cmd=0 (data)
    return bytes([FEND, cmd]) + kiss_escape(ax25_frame) + bytes([FEND])

def _parse_call(callsign: str) -> Tuple[str, int]:
    callsign = callsign.strip().upper()
    if "-" in callsign:
        call, ssid_s = callsign.split("-", 1)
        return call, int(ssid_s)
    return callsign, 0

def ax25_addr_encode(callsign: str, last: bool) -> bytes:
    call, ssid = _parse_call(callsign)
    call = call[:6].ljust(6)
    out = bytearray((ord(ch) << 1) for ch in call)

    # SSID byte: set bits 5&6; insert SSID; set end-of-address bit if last
    ssid_byte = 0b01100000 | ((ssid & 0x0F) << 1)
    ssid_byte |= 0x01 if last else 0x00
    out.append(ssid_byte)
    return bytes(out)

def ax25_addr_decode(addr7: bytes) -> str:
    call = "".join(chr(b >> 1) for b in addr7[:6]).strip()
    ssid = (addr7[6] >> 1) & 0x0F
    return f"{call}-{ssid}" if ssid else call

def build_ui_frame(dest: str, src: str, digis: List[str], info: bytes) -> bytes:
    addrs = [ax25_addr_encode(dest, last=False)]
    if not digis:
        addrs.append(ax25_addr_encode(src, last=True))
    else:
        addrs.append(ax25_addr_encode(src, last=False))
        for i, d in enumerate(digis):
            addrs.append(ax25_addr_encode(d, last=(i == len(digis) - 1)))

    control = bytes([0x03])  # UI
    pid = bytes([0xF0])      # no layer 3
    return b"".join(addrs) + control + pid + info

@dataclass
class Ax25UIFrame:
    dest: str
    src: str
    digis: List[str]
    info: bytes

def parse_ui_frame(ax25: bytes) -> Optional[Ax25UIFrame]:
    """
    Parse enough AX.25 to extract UI (0x03) + PID (0xF0) info field.
    """
    if len(ax25) < 7 + 7 + 2:
        return None

    i = 0
    dest7 = ax25[i:i+7]; i += 7
    src7  = ax25[i:i+7]; i += 7

    dest = ax25_addr_decode(dest7)
    src  = ax25_addr_decode(src7)

    digis: List[str] = []
    # If src has end-of-address bit set, no digis
    last = bool(src7[6] & 0x01)
    while not last:
        if i + 7 > len(ax25):
            return None
        digi7 = ax25[i:i+7]; i += 7
        digis.append(ax25_addr_decode(digi7))
        last = bool(digi7[6] & 0x01)

    if i + 2 > len(ax25):
        return None
    control = ax25[i]; pid = ax25[i+1]; i += 2
    if control != 0x03 or pid != 0xF0:
        return None

    return Ax25UIFrame(dest=dest, src=src, digis=digis, info=ax25[i:])

# ---- APRS parsing helpers ----

@dataclass
class AprsMessage:
    # Outer (RF) frame
    rf_src: str
    rf_dest: str
    rf_digis: List[str]
    rf_info: str

    # Inner decoded APRS message
    addressee9: str
    text: str
    msgid: Optional[str]
    third_party: bool

def _parse_tnc2_line(line: str) -> Optional[Tuple[str, str, str]]:
    """
    Parse 'SRC>DEST,PATH:INFO' -> (src, dest_path, info)
    """
    if ">" not in line or ":" not in line:
        return None
    left, info = line.split(":", 1)
    src, dest_path = left.split(">", 1)
    return src.strip(), dest_path.strip(), info

def _parse_aprs_message_info(info: str) -> Optional[Tuple[str, str]]:
    """
    APRS message info: ':ADDRESSEE9:TEXT'
    Returns (addressee9, text)
    """
    if not info.startswith(":") or len(info) < 11 or info[10] != ":":
        return None
    addressee9 = info[1:10]  # 9 chars (space padded)
    text = info[11:]
    return addressee9, text

def _extract_msgid(text: str) -> Optional[str]:
    if "{" not in text:
        return None
    tail = text.split("{")[-1].strip()
    return tail or None

def decode_aprs_messages_from_ui(ui: Ax25UIFrame) -> List[AprsMessage]:
    """
    Returns 0..n AprsMessage objects decoded from one RF UI frame.
    Handles third-party packets starting with '}'.
    """
    rf_info = ui.info.decode("ascii", errors="ignore")
    msgs: List[AprsMessage] = []

    # Third-party encapsulation: '}' + full TNC2 frame
    if rf_info.startswith("}"):
        inner = rf_info[1:]
        parsed = _parse_tnc2_line(inner)
        if parsed:
            _inner_src, _inner_dest_path, inner_info = parsed
            m = _parse_aprs_message_info(inner_info)
            if m:
                add9, text = m
                msgs.append(AprsMessage(
                    rf_src=ui.src, rf_dest=ui.dest, rf_digis=ui.digis, rf_info=rf_info,
                    addressee9=add9, text=text, msgid=_extract_msgid(text), third_party=True
                ))
        return msgs

    # Normal (non-encapsulated) APRS message
    m = _parse_aprs_message_info(rf_info)
    if m:
        add9, text = m
        msgs.append(AprsMessage(
            rf_src=ui.src, rf_dest=ui.dest, rf_digis=ui.digis, rf_info=rf_info,
            addressee9=add9, text=text, msgid=_extract_msgid(text), third_party=False
        ))
    return msgs

# ---- Client ----

class KissTcpClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None
        self._rxbuf = bytearray()

    def connect(self, timeout: float = 5.0):
        s = socket.create_connection((self.host, self.port), timeout=timeout)
        s.settimeout(0.5)
        self.sock = s

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def send_ax25(self, ax25_frame: bytes, kiss_port: int = 0):
        if not self.sock:
            raise RuntimeError("KISS socket not connected")
        self.sock.sendall(kiss_frame(ax25_frame, port=kiss_port))

    def send_aprs_info(
        self,
        *,
        src: str,
        ax25_dest: str,
        digis: List[str] = ["WIDE1-1"],
        payload: str,
        kiss_port: int = 0,
    ):
        ax25 = build_ui_frame(
            dest=ax25_dest,
            src=src,
            digis=digis,
            info=payload.encode("ascii", errors="replace"),
        )
        self.send_ax25(ax25, kiss_port=kiss_port)

    def read_ui_frames(self) -> List[Ax25UIFrame]:
        """
        Read and return all complete UI frames currently available.
        """
        if not self.sock:
            raise RuntimeError("KISS socket not connected")

        try:
            data = self.sock.recv(4096)
            if data:
                self._rxbuf.extend(data)
        except socket.timeout:
            pass

        frames: List[Ax25UIFrame] = []

        # Extract KISS frames delimited by FEND
        while True:
            try:
                start = self._rxbuf.index(FEND)
            except ValueError:
                # No FEND at all -> drop junk
                self._rxbuf.clear()
                break

            # drop bytes before first FEND
            if start > 0:
                del self._rxbuf[:start]

            # need another FEND
            try:
                end = self._rxbuf.index(FEND, 1)
            except ValueError:
                break

            raw = bytes(self._rxbuf[1:end])  # between FENDs
            del self._rxbuf[:end+1]

            if not raw:
                continue

            # raw[0] is KISS cmd; we only handle type 0 (data)
            cmd = raw[0]
            typ = cmd & 0x0F
            if typ != 0x00:
                continue

            ax25 = kiss_unescape(raw[1:])
            ui = parse_ui_frame(ax25)
            if ui:
                frames.append(ui)

        return frames

    def poll_aprs_messages(self) -> List[AprsMessage]:
        """
        Read any available frames and decode APRS messages (normal or third-party).
        """
        msgs: List[AprsMessage] = []
        for ui in self.read_ui_frames():
            msgs.extend(decode_aprs_messages_from_ui(ui))
        return msgs
