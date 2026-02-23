# radiogrammer/models.py
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List
import re

## --- Helper Functions ------------------------------------ ##
# Logic to check the word count of the radiogram text
def calculate_word_count(text: str) -> int:
    words = text.strip().split()
    return len(words)

def getCheckCount(text: str) -> int:
    regex = r'ARL\s(\d+)'
    match = re.search(regex, text)
    if match:
        return int(match.group(1))
    else:
        return int(text)

def generate_msgid(cls) -> str:
    import secrets
    import string
    _ALLOWED_ID_CHARS = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(_ALLOWED_ID_CHARS) for _ in range(5))

## ------------------------------------------------------------ ##
class RadioOperator(BaseModel):
    callsign: str
    name: Optional[str] = ''
    email: Optional[str] = ''
    phone: Optional[str] = ''

## ------------------------------------------------------------ ##
class Address(BaseModel):
    name: str
    street: Optional[str] = ''
    city_state_zip: Optional[str] = ''
    phone: Optional[str] = ''
    email: Optional[str] = ''

## ------------------------------------------------------------ ##
class Preamble(BaseModel):
    number: str
    precedence: str            # R, W, P, EMERGENCY
    hx: Optional[str] = ''   # e.g. HXC
    check: str  # word count for validation, cannot include 'ARL'
    station_of_origin: str     # callsign
    place_of_origin: str       # e.g. "BOSTON MA"
    time_filed: Optional[str] = ''  # "1830Z" etc
    filed_date: str            # "DEC 29"

## ------------------------------------------------------------ ##
class RadiogramText(BaseModel):

    lines: List[str] = Field(default_factory=list)

    @field_validator('lines')

    @classmethod
    def validate_word_count(cls, lines: List[str]) -> List[str]:

        norm = []
        for ln in lines:
            ln2 = " ".join(ln.strip().split())
            if ln2:
                norm.append(ln2)

        if len(norm) > 5:
            raise ValueError("Radiogram text cannot exceed 5 lines")
        
        for i, ln in enumerate(norm, start=1):
            wc = calculate_word_count(ln)
            if wc > 5:
                raise ValueError(f"Line {i} exceeds 5 words")
         
        word_count = sum(calculate_word_count(ln) for ln in norm) 

        if word_count == 0:
            raise ValueError("Radiogram text cannot be empty")

        return norm
    
## ------------------------------------------------------------ ##
class BookKeeping(BaseModel):
    received_from: Optional[str] = None
    sent_to: Optional[str] = None

## ------------------------------------------------------------ ##
class Radiogram(BaseModel):
    preamble: Preamble
    address: Address
    text: RadiogramText
    signature: str
    bookkeeping: Optional[BookKeeping] = None

    @model_validator(mode='after')
    def enforce_word_count(self) -> 'Radiogram':
        computed = calculate_word_count(" ".join(self.text.lines))
        if computed != getCheckCount(self.preamble.check):
            raise ValueError(f"Preamble check {self.preamble.check} does not match actual word count {computed}")
        return self

## ------------------------------------------------------------ ##
class RenderedRadiogram(BaseModel):
    check: int
    lines: List[str]

## ------------------------------------------------------------ ##
class WinlinkRadiogram(BaseModel):
    check: int
    lines: List[str]

## ------------------------------------------------------------ ##
class APRSLine(BaseModel):
    source_callsign: str
    protocol: str = "APK005"
    dest_addressee: str
    payload: str
    msgid: str = Field(default_factory=generate_msgid)

## ------------------------------------------------------------ ##
class AprsMessage(BaseModel):
    """
    Normalized APRS message packet (sufficient for your ACK/Ready/Roger/73 flows).
    """
    raw: str
    rf_src: str               # source callsign
    addressee9: str           # 9-char padded addressee field (e.g. "KC1YMT   ")
    text: str                 # message text (without {msgid if present)
    msgid: Optional[str]      # msgid (without braces), if present
    third_party: bool = False # came in with leading '}' wrapper
    path: str = ""            # raw path (comma-separated)



