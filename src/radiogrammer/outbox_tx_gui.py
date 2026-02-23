# src/radiogrammer/outbox_tx_gui.py
from __future__ import annotations

import time
import re
from pathlib import Path
from typing import Callable, List, Optional, Any, Tuple

from radiogrammer.kiss import KissTcpClient
from radiogrammer.storage import load_radiogram, move_file
from radiogrammer.render import render_lines
from radiogrammer.models import APRSLine

LogFn = Callable[[str], None]

_MSGID_RE = re.compile(r"^([A-Z0-9]{1,10})", re.IGNORECASE)


def my_callsign_regex(my_call: str) -> re.Pattern:
    base = my_call.split("-")[0].upper()
    return re.compile(rf"^{re.escape(base)}(?:-\d{{1,2}})?\s*$", re.IGNORECASE)


def split_base_and_msgid(text: str, msgid: Optional[str]) -> tuple[str, Optional[str]]:
    """
    Return (base_text, msgid). If msgid wasn't parsed by the decoder, try to extract
    it from a '{MSGID' suffix in the text.
    """
    raw = (text or "").strip()
    base = raw
    mid = msgid

    if "{" in raw:
        base, tail = raw.split("{", 1)
        base = base.strip()
        if not mid:
            tail = tail.strip()
            m = _MSGID_RE.match(tail)
            if m:
                mid = m.group(1).upper()

    return base, (mid.upper() if mid else None)


def _tx_ack(
    kiss: KissTcpClient,
    *,
    my_call: str,
    ax25_dest: str,
    digis: List[str],
    msgid: str,
    log: LogFn,
):
    kiss.send_aprs_info(
        src=my_call,
        ax25_dest=ax25_dest,
        digis=digis,
        payload=f":NTSGTE   :ack{msgid}",
    )
    log(f"TX: ack{msgid}")


def _take_messages(kiss: KissTcpClient, rxbuf: list[Any]) -> list[Any]:
    """Return messages to process now: buffered + newly polled."""
    msgs: list[Any] = []
    if rxbuf:
        msgs.extend(rxbuf)
        rxbuf.clear()
    msgs.extend(kiss.poll_aprs_messages())
    return msgs


def wait_for_ack(
    kiss: KissTcpClient,
    *,
    my_call: str,
    expected_msgid: str,
    rxbuf: list[Any],
    timeout: float = 60.0,
) -> bool:
    """
    Wait for 'ack<expected_msgid>' addressed to my_call.
    IMPORTANT: preserves other messages in rxbuf for later stages.
    """
    to_me = my_callsign_regex(my_call)
    deadline = time.time() + timeout
    expected = f"ack{expected_msgid}".lower()

    while time.time() < deadline:
        msgs = _take_messages(kiss, rxbuf)

        keep: list[Any] = []
        found = False

        for m in msgs:
            if not to_me.match(m.addressee9):
                keep.append(m)
                continue

            txt = (m.text or "").strip().lower()
            if txt.startswith(expected):
                found = True
                continue  # consume

            keep.append(m)

        rxbuf.extend(keep)

        if found:
            return True

        time.sleep(0.05)

    return False


def wait_for_ready_to_copy(
    kiss: KissTcpClient,
    *,
    my_call: str,
    ax25_dest: str,
    digis: List[str],
    expected_n: int,
    log: LogFn,
    rxbuf: list[Any],
    timeout: float = 60.0,
) -> bool:
    """
    Wait for: 'Ready to copy <n> radiogram(s)' addressed to us.
    ACK it if it has a msgid (either decoder-provided or extracted from '{...').
    Preserves unrelated messages in rxbuf.
    """
    to_me = my_callsign_regex(my_call)
    deadline = time.time() + timeout

    ready_re = re.compile(
        r"^\s*Ready to copy\s+(\d+)\s+radiogram",
        re.IGNORECASE,
    )

    while time.time() < deadline:
        msgs = _take_messages(kiss, rxbuf)

        keep: list[Any] = []
        found = False

        for m in msgs:
            if not to_me.match(m.addressee9):
                keep.append(m)
                continue

            base_text, mid = split_base_and_msgid(m.text, getattr(m, "msgid", None))

            mm = ready_re.match(base_text)
            if mm:
                n = int(mm.group(1))
                log(f"RX: {base_text!r} msgid={mid}")

                if mid:
                    _tx_ack(
                        kiss,
                        my_call=my_call,
                        ax25_dest=ax25_dest,
                        digis=digis,
                        msgid=mid,
                        log=log,
                    )

                if n != expected_n:
                    log(
                        f"Warning: Ready-to-copy count {n} != expected {expected_n} "
                        f"(continuing anyway)."
                    )

                found = True
                continue  # consume it

            # Keep other to-me messages for later phases
            keep.append(m)

        rxbuf.extend(keep)

        if found:
            return True

        time.sleep(0.05)

    return False


def qtc_handshake(
    kiss: KissTcpClient,
    *,
    my_call: str,
    ax25_dest: str,
    digis: List[str],
    count: int,
    log: LogFn,
    rxbuf: list[Any],
    max_tries : int = 5,
    ack_timeout: float = 120.0,
    back_off: float = 1.0
) -> tuple[str, dict]:
    """
    Send QTC <count>, wait for ack<msgid>, then wait for Ready-to-copy and ACK it.
    Returns: ("ok", {}) if handshake completed
             ("await_user", {qtc info dict}) if no ACK after retries
    """
    qtc = APRSLine(
        source_callsign=my_call,
        dest_addressee="NTSGTE",
        payload=f"QTC {count}",
    )

    for attempt in range(1, max_tries + 1):

        kiss.send_aprs_info(
            src=my_call,
            ax25_dest=ax25_dest,
            digis=digis,
            payload=f":NTSGTE   :{qtc.payload}{{{qtc.msgid}",
        )
        log(f"TX: {qtc.payload} " + "{" + qtc.msgid)

        ok = wait_for_ack(
            kiss,
            my_call=my_call,
            expected_msgid=qtc.msgid,
            rxbuf=rxbuf,
            timeout=ack_timeout
        )

        if ok:
            log(f"OK: ack{qtc.msgid}")
            break

        if attempt < max_tries:
            log(f"Waited for {ack_timeout} seconds, did not receive ack {qtc.msgid}")
            log(f"Retrying:Attempt {attempt + 1} ...")

    if not ok:
        log(f"FAIL: no ack for msgid {qtc.msgid} after {max_tries} attempts")
        return ("await_user", {
            "line": qtc.payload,
            "msgid": qtc.msgid,
            "attempts": max_tries,
            "timeout": ack_timeout,
        })

    if not wait_for_ready_to_copy(
            kiss,
            my_call=my_call,
            ax25_dest=ax25_dest,
            digis=digis,
            expected_n=count,
            log=log,
            rxbuf=rxbuf,
            timeout=ack_timeout,
        ):
            log("FAIL: did not receive 'Ready to copy ...' from NTSGTE")
            return ("await_user", {
                "line": "Ready to copy",
                "msgid": "N/A",
                "attempts": 1,
                "timeout": ack_timeout,
            })

    return ("ok", {})

# Default tries is 5 with 120 seconds ack timeoout
def send_one_line(
    kiss: KissTcpClient,
    *,
    my_call: str,
    ax25_dest: str,
    digis: List[str],
    line_payload: str,
    rxbuf: list[Any],
    max_tries : int = 5,
    ack_timeout: float = 60.0,
    back_off: float = 1.0,
    log: LogFn,
) -> tuple[str, dict]:
    """
    Send one line and wait for ACK.
    Returns: ("ok", {}) if ACK received
             ("await_user", {line info dict}) if no ACK after retries
    """

    line = APRSLine(
        source_callsign=my_call,
        dest_addressee="NTSGTE",
        payload=line_payload,
    )

    for attempt in range(1, max_tries + 1):
        kiss.send_aprs_info(
            src=my_call,
            ax25_dest=ax25_dest,
            digis=digis,
            payload=f":NTSGTE   :{line.payload}{{{line.msgid}",
        )
        log(f"TX: {line.payload} " + "{" + line.msgid)

        ok = wait_for_ack(
            kiss,
            my_call=my_call,
            expected_msgid=line.msgid,
            rxbuf=rxbuf,
            timeout=ack_timeout,
        )
        if ok:
            log(f"OK: ack{line.msgid} on attempt {attempt}")
            return ("ok", {})

        if attempt < max_tries:
            delay = back_off * attempt
            log(f"Waited for {ack_timeout} seconds, did not receive ack {line.msgid}")
            log(f"Retrying in {delay:.1f} seconds...")
            time.sleep(delay)

    log(f"FAIL: no ack for msgid {line.msgid} after {max_tries} attempts")

    # Return info for user decision
    return ("await_user", {
        "line": line.payload,
        "msgid": line.msgid,
        "attempts": max_tries,
        "timeout": ack_timeout,
    })


def drain_and_ack_anything_to_me(
    kiss: KissTcpClient,
    *,
    my_call: str,
    ax25_dest: str,
    digis: List[str],
    log: LogFn,
    rxbuf: list[Any],
    wait_for_seconds: float,
) -> None:
    """
    After NS\\..., NTSGTE will typically send Roger{...} and 73{...}.
    ACK anything with msgid addressed to us for 'seconds' seconds.
    Also drains rxbuf first so we don't miss early arrivals.
    """
    to_me = my_callsign_regex(my_call)
    deadline = time.time() + wait_for_seconds
    acked: set[str] = set()

    while time.time() < deadline:
        msgs = _take_messages(kiss, rxbuf)

        keep: list[Any] = []
        for m in msgs:
            if not to_me.match(m.addressee9):
                keep.append(m)
                continue

            base_text, mid = split_base_and_msgid(m.text, getattr(m, "msgid", None))

            if mid and mid not in acked and not base_text.strip().lower().startswith("ack"):
                _tx_ack(
                    kiss,
                    my_call=my_call,
                    ax25_dest=ax25_dest,
                    digis=digis,
                    msgid=mid,
                    log=log,
                )
                acked.add(mid)
                log(f"RX: {base_text!r} -> ACKed {mid}")
            else:
                keep.append(m)

        rxbuf.extend(keep)
        time.sleep(0.05)


UserDecisionFn = Callable[[dict], str]  # Returns "continue", "retry", or "abort"

def relay_outbox_file(
    kiss: KissTcpClient,
    *,
    path: Path,
    my_call: str,
    ax25_dest: str,
    digis: List[str],
    log: LogFn,
    ask_user_decision: Optional[UserDecisionFn] = None,
) -> Optional[Path]:
    rg = load_radiogram(path)
    rendered = render_lines(rg)

    rxbuf: list[Any] = []

    log(f"Relaying file: {path.name}")

    # Handshake for 1 radiogram - with user decision support
    # TODO: make retries and time out reasonable configurable parameters
    while True:
        status, info = qtc_handshake(
            kiss,
            my_call=my_call,
            ax25_dest=ax25_dest,
            digis=digis,
            count=1,
            log=log,
            rxbuf=rxbuf,
            max_tries=2,
            ack_timeout=30.0,
        )

        if status == "ok":
            break  # Handshake successful, proceed to sending lines

        elif status == "await_user":
            if ask_user_decision is None:
                # No user decision callback, abort
                log("STOP: QTC handshake failed (leaving in outbox).")
                return None

            # Ask user what to do
            decision = ask_user_decision(info)

            if decision == "continue":
                log("USER DECISION: Continue without QTC ACK (proceeding to send lines)")
                break  # Proceed despite missing handshake ACK

            elif decision == "retry":
                log("USER DECISION: Retry QTC handshake")
                continue  # Retry the handshake

            else:  # "abort"
                log("USER DECISION: Abort relay")
                log("STOP: user aborted during QTC handshake (leaving in outbox).")
                return None

        else:
            # Unknown status, abort
            log(f"STOP: unknown status {status} during handshake (leaving in outbox).")
            return None

    # Send each rendered payload line (N#, NA, N1.., NS..), waiting for ACK each time
    for payload in rendered.lines:
        if not payload.strip():
            continue

        while True:  # Loop to handle retry from user decision
            # There are default retries and back-off in send_one_line() function definition
            #TODO Make those retries and timeouts configurable parameters
            status, info = send_one_line(
                kiss,
                my_call=my_call,
                ax25_dest=ax25_dest,
                digis=digis,
                line_payload=payload,
                rxbuf=rxbuf,
                log=log,
                max_tries=2,
            )

            if status == "ok":
                break  # Move to next line

            elif status == "await_user":
                if ask_user_decision is None:
                    # No user decision callback, abort
                    log("STOP: missing ACK (leaving message in outbox).")
                    return None

                # Ask user what to do
                decision = ask_user_decision(info)

                if decision == "continue":
                    log("USER DECISION: Continue without ACK")
                    break  # Move to next line despite missing ACK

                elif decision == "retry":
                    log("USER DECISION: Retry sending line")
                    continue  # Retry the while loop

                else:  # "abort"
                    log("USER DECISION: Abort relay")
                    log("STOP: user aborted (leaving message in outbox).")
                    return None

            else:
                # Unknown status, abort
                log(f"STOP: unknown status {status} (leaving message in outbox).")
                return None

    # ACK Roger/73 that come back
    drain_and_ack_anything_to_me(
        kiss,
        my_call=my_call,
        ax25_dest=ax25_dest,
        digis=digis,
        log=log,
        rxbuf=rxbuf,
        wait_for_seconds=90.0,
    )

    sent_path = move_file(path, "sent")
    log(f"Moved to sent: {sent_path.name}")
    return sent_path
