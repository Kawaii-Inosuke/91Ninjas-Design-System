"""Local duplicate-topic check (skill thresholds) and visual duplicates."""
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from studio_agents.history import History, hamming, phash


@pytest.fixture
def h(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STUDIO_MODELS_DIR", str(Path.home() / ".cache" / "91studio" / "models"))  # one download for all runs
    hist = History(tmp_path / "history.db")
    yield hist
    hist.close()


def test_same_topic_is_reported_with_its_files_never_blocked(h, tmp_path):
    png = tmp_path / "a.png"
    Image.new("RGB", (200, 120), "white").save(png)
    rid = h.reserve("demo", "Steps to verify a payment request", "a checklist before paying", ["call back", "check details"])
    r = h.check("demo", "How to verify payment requests", "steps to check before paying")
    assert r.verdict == "similar" and r.matches[0].id == rid and r.matches[0].status == "reserved"
    h.approve(rid, png, {"png": str(png)}, layout="vertical_list/number")
    r = h.check("demo", "How to verify payment requests", "steps to check before paying")
    assert r.verdict == "similar" and r.matches[0].similarity >= 0.80        # very close, still only reported
    assert r.matches[0].files == {"png": str(png)}                           # the designer is shown where it is
    assert r.avoid_layouts == ["vertical_list/number"]


def test_other_topics_and_clients_are_not_similar(h):
    h.add_past("demo", "Steps to verify a payment request", "checklist")
    assert h.check("demo", "Choosing office furniture for a small team", "comparison").verdict == "ok"
    assert h.check("other-client", "Steps to verify a payment request", "checklist").verdict == "ok"
    assert h.check("demo", "Steps to verify a payment request", "checklist").verdict == "similar"


def test_abandoned_reservations_stop_counting(h):
    rid = h.reserve("demo", "Quarterly tax deadlines", "timeline")
    assert h.check("demo", "Quarterly tax deadlines", "timeline").verdict == "similar"
    h.release(rid)
    assert h.check("demo", "Quarterly tax deadlines", "timeline").verdict == "ok"


def test_visual_lookalikes_are_reported_not_refused(h, tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    for p, shift in ((a, 0), (b, 2)):
        im = Image.new("RGB", (400, 300), "white")
        ImageDraw.Draw(im).rectangle((40 + shift, 40, 300, 200), fill=(1, 100, 240))
        im.save(p)
    first = h.reserve("demo", "Topic one", "x")
    h.approve(first, a, {})
    second = h.reserve("demo", "A completely different subject", "y")
    r = h.approve(second, b, {})
    assert r["status"] == "approved" and r["visual_duplicates"][0]["id"] == first


def test_phash_is_stable_and_discriminating(tmp_path):
    a, c = tmp_path / "a.png", tmp_path / "c.png"
    im = Image.new("RGB", (300, 300), "white")
    ImageDraw.Draw(im).ellipse((50, 50, 250, 250), fill="black")
    im.save(a)
    im2 = Image.new("RGB", (300, 300), "white")
    ImageDraw.Draw(im2).rectangle((0, 0, 150, 300), fill="black")
    im2.save(c)
    assert hamming(phash(a), phash(a)) == 0
    assert hamming(phash(a), phash(c)) > 8
