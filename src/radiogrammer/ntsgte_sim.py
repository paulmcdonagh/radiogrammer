# src/radiogrammer/ntsgte_sim.py
from __future__ import annotations

import argparse
import random
import re
import secrets
import string
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from radiogrammer.kiss import KissTcpClient
from radiogrammer.helpers import gen_msgid, pad9

# _ALLOWED_ID_CHARS = string.ascii_uppercase + string.digits


# def gen_msgid(n: int = 5) -> str:
#     return "".join(secrets.choice(_ALLOWED_ID_CHARS) for _ in range(n))


# def pad9(callsign: str) -> str:
#     # APRS message addressee is 9 chars, padded with spaces.
#     base = callsign.strip().upper()
#     return base[:9].ljust(9)


def callsign_regex(callsign: str) -> re.Pattern:
    base = callsign.split("-")[0].upper()
    return re.compile(rf"^{re.escape(base)}(?:-\d{{1,2}})?\s*$", re.IGNORECASE)


@dataclass
class Session:
    sender: str
    lines: List[str] = field(default_factory=list)
    roger_msgid: Optional[str] = None
    final73_msgid: Optional[str] = None
    pending_acks: Set[str] = field(default_factory=set)
    completed: bool = False
    last_activity: float = field(default_factory=time.time)


class NTSGTESimulator:
    """
    Fake NTSGTE:
      - listens for messages TO NTSGTE
      - ACKs each incoming msg with {msgid}  (unless "dropped" by probability)
      - when it sees QTC <n>, sends Ready to copy <n> radiogram(s){...}
      - when it sees an NS\\... line, sends Roger{...} then 73{...}
      - waits for ACKs for Roger/73 (best-effort)
    """

    def __init__(
        self,
        kiss: KissTcpClient,
        *,
        service_call: str = "NTSGTE",
        ax25_dest: str = "APK005",
        digis: Optional[List[str]] = None,
        verbose: bool = True,
        processing_delay_s: float = 0.0,
        drop_prob: float = 0.0,
        drop_ack_prob: Optional[float] = None,
        drop_reply_prob: Optional[float] = None,
        drop_ready_to_copy_prob: Optional[float] = None,
    ):
        self.kiss = kiss
        self.service_call = service_call.upper()
        self.ax25_dest = ax25_dest
        self.digis = digis if digis is not None else []
        self.verbose = verbose

        self.processing_delay_s = max(0.0, float(processing_delay_s))

        # Drop probabilities:
        # - drop_prob is a global default
        # - drop_ack_prob applies to "ackxxxxx"
        # - drop_reply_prob applies to Ready/Roger/73/INFO replies
        # - drop_ready_to_copy_prob applies only to the "Ready to copy" QTC reply;
        #   when set, overrides the normal bypass that always sends Ready-to-copy.
        #   Use this to specifically test Phase B (Ready to copy) failure handling.
        self.drop_ack_prob = float(drop_prob if drop_ack_prob is None else drop_ack_prob)
        self.drop_reply_prob = float(drop_prob if drop_reply_prob is None else drop_reply_prob)
        self.drop_ready_to_copy_prob: Optional[float] = (
            None if drop_ready_to_copy_prob is None else float(drop_ready_to_copy_prob)
        )

        self.to_service = callsign_regex(self.service_call)
        self.sessions: Dict[str, Session] = {}
        self.acked_inbound: Set[tuple[str, str]] = set()  # (sender, msgid) we have *actually* ACKed
        self._roger_counter = 1000

    def _should_drop_outbound(self, *, text: str) -> bool:
        t = (text or "").strip().lower()
        p = self.drop_ack_prob if t.startswith("ack") else self.drop_reply_prob
        if p <= 0.0:
            return False
        if p >= 1.0:
            return True
        return random.random() < p

    def _send_msg(self, *, to_call: str, text: str, msgid: Optional[str] = None) -> bool:
        """
        Send APRS message from service_call to to_call.
        If msgid is provided, append '{msgid' (no closing brace).

        Returns True if we actually transmitted; False if dropped (simulated loss).
        """
        if self._should_drop_outbound(text=text):
            if self.verbose:
                print(f"[SIM] DROP outbound -> {to_call}: {text!r} msgid={msgid}")
            return False

        to9 = pad9(to_call)
        if msgid:
            payload = f":{to9}:{text}{{{msgid}"
        else:
            payload = f":{to9}:{text}"

        self.kiss.send_aprs_info(
            src=self.service_call,
            ax25_dest=self.ax25_dest,
            digis=self.digis,
            payload=payload,
        )
        return True

    def _ack_inbound(self, sender: str, inbound_msgid: str) -> bool:
        # ACK format: :SENDER9:ack<inbound_msgid>
        return self._send_msg(to_call=sender, text=f"ack{inbound_msgid}", msgid=None)

    def _maybe_get_session(self, sender: str) -> Session:
        s = self.sessions.get(sender)
        if not s:
            s = Session(sender=sender)
            self.sessions[sender] = s
        return s

    def _handle_ack_to_service(self, sender: str, text: str):
        # text like: ackABCDE
        acked = text.strip()[3:].strip()
        if not acked:
            return
        sess = self.sessions.get(sender)
        if not sess:
            return
        if acked in sess.pending_acks:
            sess.pending_acks.remove(acked)
            sess.last_activity = time.time()
            if self.verbose:
                print(f"[SIM] got ACK from {sender}: ack{acked}")

    def _handle_inbound_to_service(self, sender: str, text: str, msgid: Optional[str]):
        if self.processing_delay_s:
            time.sleep(self.processing_delay_s)

        # If it's an ack for something we sent, handle separately.
        if text.strip().lower().startswith("ack"):
            self._handle_ack_to_service(sender, text)
            return

        sess = self._maybe_get_session(sender)

        # ACK any inbound that has a msgid (but only once per sender/msgid) —
        # IMPORTANT: only mark acked_inbound if we actually transmitted the ACK.
        if msgid:
            key = (sender, msgid)
            if key not in self.acked_inbound:
                sent = self._ack_inbound(sender, msgid)
                if sent:
                    self.acked_inbound.add(key)
                    if self.verbose:
                        print(f"[SIM] ACKed inbound from {sender}: {text!r} {{msgid={msgid}}}")
                else:
                    if self.verbose:
                        print(f"[SIM] (ACK DROPPED) inbound from {sender}: {text!r} {{msgid={msgid}}}")

        # Track session lines; strip trailing {msgid part if present
        base_text = text.split("{", 1)[0].strip()
        sess.lines.append(base_text)
        sess.last_activity = time.time()

        if self.verbose:
            print(f"[SIM] RX to {self.service_call} from {sender}: {base_text!r}")

        # QTC handshake -> "Ready to copy ..."
        if base_text.strip().upper().startswith("QTC"):
            parts = base_text.strip().split()
            number = parts[1] if len(parts) > 1 else "1"

            rid = gen_msgid()

            # Normally we force Ready-to-copy through (NTSGTE always replies to QTC).
            # If drop_ready_to_copy_prob is set, use that probability instead so
            # callers can specifically test Phase B (Ready to copy) drop handling.
            if self.drop_ready_to_copy_prob is not None:
                p = self.drop_ready_to_copy_prob
                dropped = (p >= 1.0) or (p > 0.0 and random.random() < p)
                if dropped:
                    if self.verbose:
                        print(f"[SIM] DROP Ready-to-copy -> {sender} (drop_ready_to_copy_prob={p})")
                    return
            sent = self._send_msg(to_call=sender, text=f"Ready to copy {number} radiogram", msgid=rid)
            if sent:
                sess.pending_acks.add(rid)
                if self.verbose:
                    print(f"[SIM] TX Ready-to-copy{{{rid}}}")
            return

        # INFO / CLEAR
        if base_text.upper() == "INFO":
            rid = gen_msgid()
            sent = self._send_msg(to_call=sender, text="NTSGTE SIM ONLINE - READY", msgid=rid)
            if sent:
                sess.pending_acks.add(rid)
                if self.verbose:
                    print(f"[SIM] TX INFO-reply{{{rid}}}")
            return

        if base_text.upper() == "CLEAR":
            return

        # If we see the NS\ signature line, respond with Roger + 73.
        if base_text.startswith("NS\\") and not sess.completed:
            self._roger_counter += 1

            # Roger
            roger_id = gen_msgid()
            sent_roger = self._send_msg(to_call=sender, text=f"Roger message {self._roger_counter}", msgid=roger_id)
            if sent_roger:
                sess.roger_msgid = roger_id
                sess.pending_acks.add(roger_id)
                if self.verbose:
                    print(f"[SIM] TX Roger{{{roger_id}}}")
            else:
                if self.verbose:
                    print(f"[SIM] (Roger DROPPED) {{msgid={roger_id}}}")

            time.sleep(2.0)

            # 73

            final_id = gen_msgid()
            sent_73 = self._send_msg(
                to_call=sender,
                text=f"{sender} de NTSGTE 73, thanks for using NTSGTE.",
                msgid=final_id,
            )
            if sent_73:
                sess.final73_msgid = final_id
                sess.pending_acks.add(final_id)
                if self.verbose:
                    print(f"[SIM] TX 73{{{final_id}}}")
            else:
                if self.verbose:
                    print(f"[SIM] (73 DROPPED) {{msgid={final_id}}}")

    def reap_idle_sessions(self, max_idle_s: float = 300.0):
        now = time.time()
        for sender in list(self.sessions.keys()):
            if now - self.sessions[sender].last_activity > max_idle_s:
                if self.verbose:
                    print(f"[SIM] reaping idle session for {sender}")
                del self.sessions[sender]

    def run_forever(self, poll_sleep: float = 0.001):
        print(f"[SIM] Fake NTSGTE running as {self.service_call}. Ctrl+C to stop.")
        while True:
            for m in self.kiss.poll_aprs_messages():
                # only messages addressed to NTSGTE
                if not self.to_service.match(m.addressee9):
                    continue

                sender = (m.rf_src or "").strip().upper()
                if not sender:
                    continue

                # ignore our own echoes if they happen
                if sender.split("-")[0] == self.service_call:
                    continue

                self._handle_inbound_to_service(sender=sender, text=m.text, msgid=m.msgid)

                # If we've sent Roger/73 and got both ACKs, consider session complete.
                sess = self.sessions.get(sender)
                if sess and not sess.completed:
                    if sess.roger_msgid and sess.final73_msgid and not sess.pending_acks:
                        sess.completed = True
                        if self.verbose:
                            print(f"[SIM] session complete for {sender}")
                        # keep behavior: allow another session later
                        sess.completed = False

            self.reap_idle_sessions()
            time.sleep(poll_sleep)


def _parse_digis(arg: str) -> List[str]:
    arg = (arg or "").strip()
    if not arg:
        return []
    return [p.strip() for p in arg.split(",") if p.strip()]


def main():
    ap = argparse.ArgumentParser(description="Radiogrammer fake NTSGTE simulator (KISS TCP).")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9001)
    ap.add_argument("--service-call", default="NTSGTE")
    ap.add_argument("--ax25-dest", default="APK005")
    ap.add_argument("--digis", default="", help="Comma-separated digis, e.g. 'WIDE1-1,WIDE2-1' (blank=none)")
    ap.add_argument("--processing-delay", type=float, default=2.0, help="Seconds to sleep before responding")
    ap.add_argument("--drop-prob", type=float, default=0.9, help="Default drop probability for *any* outbound reply")
    ap.add_argument("--drop-ack-prob", type=float, default=0.4, help="Drop probability for ACK replies (overrides --drop-prob)")
    ap.add_argument("--drop-reply-prob", type=float, default=None, help="Drop probability for non-ACK replies (overrides --drop-prob)")
    ap.add_argument(
        "--drop-ready-to-copy-prob", type=float, default=1.0,
        help="Drop probability specifically for the 'Ready to copy' QTC reply (0.0–1.0). "
             "Overrides the normal behaviour where Ready-to-copy is always sent. "
             "Use 1.0 to guarantee Phase B failure for testing.",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    kiss = KissTcpClient(args.host, args.port)
    kiss.connect()

    try:
        print(
            f"Drop probs: ACKs={args.drop_ack_prob if args.drop_ack_prob is not None else args.drop_prob}, "
            f"Replies={args.drop_reply_prob if args.drop_reply_prob is not None else args.drop_prob}, "
            f"ReadyToCopy={args.drop_ready_to_copy_prob if args.drop_ready_to_copy_prob is not None else '(normal — always sent)'}"
        )
        sim = NTSGTESimulator(
            kiss,
            service_call=args.service_call,
            ax25_dest=args.ax25_dest,
            digis=_parse_digis(args.digis),
            verbose=not args.quiet,
            processing_delay_s=args.processing_delay,
            drop_prob=args.drop_prob,
            drop_ack_prob=args.drop_ack_prob,
            drop_reply_prob=args.drop_reply_prob,
            drop_ready_to_copy_prob=args.drop_ready_to_copy_prob,
        )
        sim.run_forever()
    finally:
        kiss.close()


if __name__ == "__main__":
    main()
