# radiogrammer/render.py

from radiogrammer.models import Radiogram, RenderedRadiogram, WinlinkRadiogram, getCheckCount

def render_lines(rg: Radiogram) -> RenderedRadiogram:
    p = rg.preamble
    ck = p.check if p.check is not None else sum(len(ln.split()) for ln in rg.text.lines)

    hx_part = f"{p.hx}" if p.hx else ""
    time_part = f"{p.time_filed} " if p.time_filed else ""

    preamble_line = (
        f"N#\{p.number}\{p.precedence}\{hx_part}\{p.station_of_origin}\{ck}\{p.place_of_origin}\{time_part}\{p.filed_date}"
    ).strip()

    # Address line (adjust these fields to match your Address model)
    a = rg.address
    if(a.street):
        address_line = f"NA\\{a.name}\\{a.street}\\{a.city_state_zip}".strip()
    else:
        address_line = f"NA\\{a.name}\\{a.city_state_zip}".strip()

    out_lines: list[str] = [preamble_line, address_line]

    # Optional phone/email: add nothing if missing/blank
    phone = (a.phone or "").strip()
    if phone:
        out_lines.append(f"NP\\{phone}")

    email = (a.email or "").strip()
    if email:
        out_lines.append(f"NE\\{email}")

    # Number text lines, skipping blanks
    for idx, ln in enumerate(rg.text.lines, start=1):
        ln = (ln or "").strip()
        if ln:
            out_lines.append(f"N{idx}\\{ln}")

    # Signature (only add if non-empty after stripping)
    sig = (rg.signature or "").strip()
    if sig:
        out_lines.append(f"NS\\{sig}")

    return RenderedRadiogram(check=getCheckCount(ck), lines=out_lines)

## ------------------------------------------------------------ ##
## Use this function to copy and paste into a Winlink message to send via Telnet or RMS Express
def render_winlink(rg: Radiogram) -> WinlinkRadiogram:
    ck = sum(len(ln.split()) for ln in rg.text.lines)

    out_lines: list[str] = []
    preamble = f"{rg.preamble.number} {rg.preamble.precedence}"
    if rg.preamble.hx == '':
        preamble += f" {rg.preamble.station_of_origin} {ck} {rg.preamble.place_of_origin}"
    else:
        preamble += f" {rg.preamble.hx} {rg.preamble.station_of_origin} {ck} {rg.preamble.place_of_origin}"
    if rg.preamble.time_filed != '':
        preamble += f" {rg.preamble.time_filed} {rg.preamble.filed_date}"
    else:
        preamble += f" {rg.preamble.filed_date}"
    out_lines.append(preamble)

    name = f"{rg.address.name}"
    out_lines.append(name)

    address = ""
    if rg.address.street != '':
        address += f", {rg.address.street}"
    if rg.address.city_state_zip != '':
        address += f", {rg.address.city_state_zip}"
    if rg.address.phone != '':
        address += f", {rg.address.phone}"
    if rg.address.email != '':
        address += f", {rg.address.email}"
    out_lines.append(address)
    out_lines.append("BT")

    for ln in rg.text.lines:
        out_lines.append(ln)
    out_lines.append("BT")
    if rg.signature != '':
        out_lines.append(rg.signature)

    return WinlinkRadiogram (check=getCheckCount(ck), lines=out_lines)



