"""Local design history and the "this already exists" check (D-010: local; D-020: never blocks).

Each design run reserves a row (topic, angle, key points) before rendering; approval records the layout,
files and a perceptual hash; abandoning releases it. A new brief is compared with this client's rows:
similarity = max(cosine(topic+angle), cosine(topic alone)), on all-MiniLM-L6-v2 vectors (fastembed, ONNX,
offline after the first download). Anything at ≥ DEDUP_WARN is reported as "similar" with its files, and the
designer decides whether to generate again: designers often want several versions of one topic (D-020).
Reservations older than RESERVATION_DAYS stop counting.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from studio_agents import config

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
RESERVATION_DAYS = 7
FINISHED = ("approved", "past")       # past = a client's old work, added when the client was created

SCHEMA = """
create table if not exists designs (
  id text primary key, client text not null, topic text not null, angle text not null default '',
  key_points text not null default '[]', layout text, brand_version text,
  status text not null check (status in ('reserved', 'approved', 'abandoned', 'past')),
  created_at text not null, approved_at text, files text not null default '{}', phash text,
  embedding blob, topic_embedding blob);
create index if not exists designs_client on designs (client, status);
"""


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding
    return TextEmbedding(MODEL, cache_dir=str(config.models_dir()))


def embed(texts: list[str]) -> np.ndarray:
    """Unit-length vectors (rows) for texts."""
    v = np.array(list(_model().embed(texts)), dtype=np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


def phash(png: Path) -> str:
    """64-bit perceptual hash (DCT of a 32×32 grey image, low 8×8 frequencies above their median)."""
    a = np.asarray(Image.open(png).convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    n = 32
    c = np.cos(np.pi * (2 * np.arange(n)[None, :] + 1) * np.arange(n)[:, None] / (2 * n))
    low = (c @ a @ c.T)[:8, :8]
    bits = (low > np.median(low)).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


@dataclass
class Match:
    id: str
    topic: str
    angle: str
    status: str
    layout: str | None
    created_at: str
    similarity: float
    files: dict = field(default_factory=dict)      # where the approved design's files are


@dataclass
class CheckResult:
    verdict: str                          # ok | similar
    matches: list[Match] = field(default_factory=list)
    avoid_layouts: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"verdict": self.verdict, "avoid_layouts": self.avoid_layouts,
                "matches": [m.__dict__ for m in self.matches]}


class History:
    def __init__(self, path: Path | None = None):
        self.path = path or config.history_db()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    @staticmethod
    def _vectors(topic: str, angle: str) -> tuple[bytes, bytes]:
        v = embed([f"{topic}. {angle}".strip(". "), topic])
        return v[0].tobytes(), v[1].tobytes()

    def check(self, client: str, topic: str, angle: str = "", layout_window: int = config.LAYOUT_WINDOW,
              exclude: str | None = None, limit: int = 5) -> CheckResult:
        both, alone = (np.frombuffer(b, dtype=np.float32) for b in self._vectors(topic, angle))
        since = (datetime.now(timezone.utc) - timedelta(days=RESERVATION_DAYS)).isoformat()
        rows = self.db.execute(
            "select * from designs where client = ? and status != 'abandoned' and embedding is not null "
            "and (status != 'reserved' or created_at > ?) and id is not ?", (client, since, exclude)).fetchall()
        matches = []
        for r in rows:
            e = np.frombuffer(r["embedding"], dtype=np.float32)
            t = np.frombuffer(r["topic_embedding"], dtype=np.float32)
            sim = float(max(e @ both, t @ alone))
            matches.append(Match(r["id"], r["topic"], r["angle"], r["status"], r["layout"], r["created_at"], round(sim, 3),
                                 json.loads(r["files"] or "{}")))
        matches.sort(key=lambda m: -m.similarity)
        hits = [m for m in matches if m.similarity >= config.DEDUP_WARN]
        return CheckResult("similar" if hits else "ok", hits[:limit], self.recent_layouts(client, layout_window))

    def recent_layouts(self, client: str, n: int) -> list[str]:
        rows = self.db.execute("select layout from designs where client = ? and status = 'approved' and layout is not null "
                               "order by approved_at desc limit ?", (client, n)).fetchall()
        return [r["layout"] for r in rows]

    def reserve(self, client: str, topic: str, angle: str = "", key_points: list[str] = (), layout: str | None = None,
                brand_version: str | None = None, status: str = "reserved") -> str:
        e, t = self._vectors(topic, angle)
        rid = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("insert into designs (id, client, topic, angle, key_points, layout, brand_version, status, created_at, "
                        "approved_at, embedding, topic_embedding) values (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (rid, client, topic, angle, json.dumps(list(key_points)), layout, brand_version, status, now,
                         now if status in FINISHED else None, e, t))
        self.db.commit()
        return rid

    def release(self, rid: str) -> None:
        self.db.execute("update designs set status = 'abandoned' where id = ? and status = 'reserved'", (rid,))
        self.db.commit()

    def approve(self, rid: str, png: Path, files: dict[str, str], layout: str | None = None) -> dict:
        """Mark a reserved design approved; also reports approved designs that look the same (for information)."""
        row = self.db.execute("select client from designs where id = ? and status = 'reserved'", (rid,)).fetchone()
        if not row:
            return {"error": f"no reserved design {rid}"}
        ph = phash(png)
        dupes = [{"id": r["id"], "topic": r["topic"], "distance": hamming(ph, r["phash"])}
                 for r in self.db.execute("select id, topic, phash from designs where client = ? and phash is not null "
                                          "and status in ('approved', 'past') and id != ?", (row["client"], rid))
                 if hamming(ph, r["phash"]) <= config.PHASH_MAX_DISTANCE]
        self.db.execute("update designs set status = 'approved', approved_at = ?, files = ?, phash = ?, "
                        "layout = coalesce(?, layout) where id = ?",
                        (datetime.now(timezone.utc).isoformat(), json.dumps(files), ph, layout, rid))
        self.db.commit()
        return {"id": rid, "status": "approved", "visual_duplicates": dupes}

    def add_past(self, client: str, topic: str, angle: str = "", layout: str | None = None, png: Path | None = None) -> str:
        """A client's old infographic (tagged by the Brand Onboarder), so new work never repeats it."""
        rid = self.reserve(client, topic, angle, layout=layout, status="past")
        if png:
            self.db.execute("update designs set phash = ? where id = ?", (phash(png), rid))
            self.db.commit()
        return rid

    def get(self, rid: str) -> dict | None:
        r = self.db.execute("select id, client, topic, angle, key_points, layout, status, created_at, approved_at, files "
                            "from designs where id = ?", (rid,)).fetchone()
        return dict(r) if r else None
