# src/radiogrammer/storage_help.py
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, List

from radiogrammer.models import Radiogram

Box = Literal["outbox", "archive", "sent"]

def radiogrammer_home() -> Path:
    """
    Where radiograms are stored on disk.
    Override with RADIOGRAMMER_HOME if you want.
    """
    env = os.environ.get("RADIOGRAMMER_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".radiogrammer").resolve()

def box_dir(box: Box) -> Path:
    p = radiogrammer_home() / box
    p.mkdir(parents=True, exist_ok=True)
    return p

def _filename_for(rg: Radiogram) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    nr = rg.preamble.number
    return f"{ts}_NR{nr}_{uuid.uuid4().hex[:8]}.json"

def save_radiogram(rg: Radiogram, box: Box) -> Path:
    p = box_dir(box) / _filename_for(rg)
    p.write_text(json.dumps(rg.model_dump(), indent=2), encoding="utf-8")
    return p

def list_files(box: Box) -> List[Path]:
    d = box_dir(box)
    return sorted(d.glob("*.json"), reverse=True)

def load_radiogram(path: Path) -> Radiogram:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Radiogram.model_validate(data)

def move_file(path: Path, to_box: Box) -> Path:
    dest_dir = box_dir(to_box)
    dest_path = dest_dir / path.name
    return path.rename(dest_path)

