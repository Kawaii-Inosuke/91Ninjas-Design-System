"""A design run end to end with a scripted model (no API calls): gates, critic loop, duplicates, history."""
import json
import shutil
from pathlib import Path

import pytest

from studio_agents import models
from studio_agents.history import History
from studio_agents.runs import RunError, RunManager
from studio_agents.steps import Brief, CriticReport, Finding
from studio_agents.render.spec import Spec

FIX = Path(__file__).parent / "fixtures"
SPEC = json.loads((FIX / "specs.json").read_text())["vertical_list"]
BRIEF = Brief(usable=True, title="Five steps to safer payments", topic="Steps to verify a payment request",
              angle="a checklist before paying", key_points=["call back", "check details", "second approver"],
              suggested_layout="vertical_list/number", layout_reason="steps")


class Script:
    """Answers per role; records every prompt so tests can check what the agents were told."""

    def __init__(self, critic=(True,)):
        self.critic = list(critic)
        self.prompts: list[tuple[str, str]] = []

    def __call__(self, role, system, prompt, output, images, tools):
        self.prompts.append((role, prompt))
        if role == "brief":
            return BRIEF
        if role == "layout":
            return Spec(**SPEC)
        if role == "critic":
            ok = self.critic.pop(0) if self.critic else True
            assert images and images[0].exists(), "the critic gets the preview"
            return CriticReport(passes=ok, fails=[] if ok else [Finding(rule="15", problem="text too long", fix="shorten")])
        raise AssertionError(role)


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("STUDIO_MODELS_DIR", str(Path.home() / ".cache" / "91studio" / "models"))
    shutil.copytree(FIX / "brands" / "demo", data / "clients" / "demo")
    m = RunManager(History(data / "history.db"), bridge_factory=lambda: None)
    yield m
    models.set_backend(None)


def go(mgr, script, **kw):
    models.set_backend(script)
    run = mgr.start("demo", content="How to check a payment request before paying it...", **kw)
    return mgr.wait(run.id)


def test_happy_path_with_both_approvals(mgr):
    script = Script(critic=[False, True])
    run = go(mgr, script)
    assert run.gate["kind"] == "outline" and run.gate["payload"]["spec"]["title"] == SPEC["title"]
    mgr.decide(run.id, "outline", "approve")
    run = mgr.wait(run.id)
    assert run.gate["kind"] == "final"
    assert [v["passes"] for v in run.versions] == [False, True]          # critic loop: fixed once
    assert run.best == 2 and run.placed is None                          # no editor open: files only
    layout_prompts = [p for r, p in script.prompts if r == "layout"]
    assert "Critic findings to fix" in layout_prompts[-1] and "text too long" in layout_prompts[-1]
    mgr.decide(run.id, "final", "approve")
    run = mgr.wait(run.id)
    assert run.status == "done"
    assert Path(run.final_files["png"]).exists() and "<svg" in Path(run.final_files["svg"]).read_text()
    assert mgr.history.get(run.history_id)["status"] == "approved"
    types = [e["type"] for e in run.events]
    assert types[0] == "stage" and "brief" in types and "check" in types and types[-1] == "done"
    assert (run.dir / "run.json").exists()


def test_an_existing_design_is_shown_and_can_be_generated_again(mgr):
    run = go(mgr, Script())
    mgr.decide(run.id, "outline", "approve")
    mgr.wait(run.id)
    mgr.decide(run.id, "final", "approve")
    first = mgr.wait(run.id)
    assert first.status == "done"
    again = go(mgr, Script())                                 # same content: a second version
    assert again.gate["kind"] == "similar_exists"
    match = again.gate["payload"]["matches"][0]
    assert match["status"] == "approved" and match["files"]["png"] == first.final_files["png"]
    mgr.decide(again.id, "similar_exists", "generate")
    assert mgr.wait(again.id).gate["kind"] == "outline"       # the designer chose to make it again
    third = go(mgr, Script())
    mgr.decide(third.id, "similar_exists", "abandon")
    assert mgr.wait(third.id).status == "abandoned"


def test_designer_notes_reach_the_layout_agent_and_abandon_releases(mgr):
    script = Script()
    run = go(mgr, script)
    mgr.decide(run.id, "outline", "change", notes="use icons instead of numbers")
    run = mgr.wait(run.id)
    assert run.gate["kind"] == "outline"
    assert "use icons instead of numbers" in [p for r, p in script.prompts if r == "layout"][-1]
    mgr.decide(run.id, "outline", "abandon")
    run = mgr.wait(run.id)
    assert run.status == "abandoned" and mgr.history.get(run.history_id)["status"] == "abandoned"


def test_critic_loop_stops_at_the_limit_and_keeps_the_best(mgr):
    run = go(mgr, Script(critic=[False, False, False, False]))
    mgr.decide(run.id, "outline", "approve")
    run = mgr.wait(run.id)
    assert len(run.versions) == 4 and run.gate["kind"] == "final"
    assert run.gate["payload"]["unresolved"]


def test_bad_input_and_wrong_decisions(mgr):
    models.set_backend(Script())
    with pytest.raises(Exception, match="not installed"):
        mgr.start("nobody", content="x")
    with pytest.raises(RunError, match="content"):
        mgr.start("demo", content="  ")
    run = go(mgr, Script())
    with pytest.raises(RunError, match="not waiting"):
        mgr.decide(run.id, "final", "approve")
    with pytest.raises(RunError, match="decide one of"):
        mgr.decide(run.id, "outline", "maybe")


def test_unusable_content_ends_politely(mgr):
    class Thin(Script):
        def __call__(self, role, *a):
            return Brief(usable=False, reason_if_not="only one sentence; nothing to visualise") if role == "brief" else super().__call__(role, *a)
    run = go(mgr, Thin())
    assert run.status == "not_usable" and "nothing to visualise" in run.events[-1]["data"]["reason"]


def test_a_model_failure_fails_the_run_and_releases(mgr):
    class Broken(Script):
        def __call__(self, role, *a):
            if role == "layout":
                raise models.ModelError("quota exceeded")
            return super().__call__(role, *a)
    run = go(mgr, Broken())
    assert run.status == "failed" and "quota exceeded" in run.events[-1]["data"]["error"]
    assert mgr.history.get(run.history_id)["status"] == "abandoned"


def test_the_critic_works_without_vision_when_no_vision_model_is_up(mgr):
    class NoVision(Script):
        def __call__(self, role, system, prompt, output, images, tools):
            if role == "critic" and images:
                raise models.ModelError("every key and fallback failed for the critic agent: 503")
            if role == "critic":
                assert "No image is available" in prompt
                return CriticReport(passes=False, fails=[Finding(rule="15", problem="title may be long", fix="shorten")])
            return super().__call__(role, system, prompt, output, images, tools)
    run = go(mgr, NoVision())
    mgr.decide(run.id, "outline", "approve")
    run = mgr.wait(run.id)
    assert run.gate["kind"] == "final"
    assert "checked without seeing the image" in run.versions[0]["fails"][0]["problem"]


# ── 5c: variants ────────────────────────────────────────────────────────────────────────────────────
FAMILIES = json.loads((FIX / "specs.json").read_text())


class VariantScript(Script):
    """Layout answers in the order given (fixture family names), so tests control what each version uses."""

    def __init__(self, families, critic=()):
        super().__init__(critic=critic)
        self.families = list(families)

    def __call__(self, role, system, prompt, output, images, tools):
        if role == "layout":
            self.prompts.append((role, prompt))
            return Spec(**FAMILIES[self.families.pop(0) if self.families else "vertical_list"])
        return super().__call__(role, system, prompt, output, images, tools)


class FakeBridge:
    """Records what the run asks the editor to do."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.next_id = 100

    def place_svg(self, svg, *, name, width, height, new_document=True, origin=(0.0, 0.0), document_name=None):
        from studio_agents.bridge import Placed
        self.next_id += 1
        self.calls.append(("place", name, new_document, origin))
        return Placed(self.next_id, {"title": self.next_id + 1}, [])

    def name_artboard(self, index, name):
        self.calls.append(("name", index, name))

    def add_artboard(self, x, y, width, height, name):
        self.calls.append(("artboard", x, y, name))
        return len([c for c in self.calls if c[0] == "artboard"])

    def remove(self, ids, artboards, label):
        self.calls.append(("remove", tuple(ids), tuple(artboards), label))


def test_three_versions_with_different_layouts_are_placed_side_by_side_and_one_is_kept(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("STUDIO_MODELS_DIR", str(Path.home() / ".cache" / "91studio" / "models"))
    shutil.copytree(FIX / "brands" / "demo", data / "clients" / "demo")
    bridge = FakeBridge()
    mgr = RunManager(History(data / "history.db"), bridge_factory=lambda: bridge)
    script = VariantScript(["vertical_list", "horizontal_timeline", "comparison_cards"])
    try:
        run = go(mgr, script, variants=3)
        payload = run.gate["payload"]
        assert run.gate["kind"] == "outline" and [v["letter"] for v in payload["variants"]] == ["A", "B", "C"]
        assert len({v["spec"]["layout"].split("/")[0] for v in payload["variants"]}) == 3
        layout_prompts = [p for r, p in script.prompts if r == "layout"]
        assert "other versions use: vertical_list" in layout_prompts[1]
        assert "vertical_list, horizontal_timeline" in layout_prompts[2]
        mgr.decide(run.id, "outline", "approve")
        run = mgr.wait(run.id)
        assert run.gate["kind"] == "pick" and len(run.options) == 3
        assert all(Path(o["preview"]).exists() and Path(o["svg"]).exists() for o in run.options)
        # Side by side: A opens a document, B and C get artboards to its right and land on them.
        places = [c for c in bridge.calls if c[0] == "place"]
        assert [p[2] for p in places] == [True, False, False]
        xs = [p[3][0] for p in places]
        assert xs[0] == 0 and xs[0] < xs[1] < xs[2]
        assert [c[3] for c in bridge.calls if c[0] == "artboard"] == ["Version B", "Version C"]
        assert ("name", 0, "Version A") in bridge.calls
        with pytest.raises(RunError):
            mgr.decide(run.id, "pick", "pick", choice=5)
        mgr.decide(run.id, "pick", "pick", choice=1)
        run = mgr.wait(run.id)
        assert run.gate["kind"] == "final"
        b = run.options[1]
        assert run.best == b["version"] and run.spec["layout"].startswith("horizontal_timeline")
        assert run.placed["group_id"] == b["group_id"]
        # A and C leave the document in one undo step.
        removes = [c for c in bridge.calls if c[0] == "remove"]
        assert removes == [("remove", (run.options[0]["group_id"], run.options[2]["group_id"]),
                            (run.options[0]["artboard"], run.options[2]["artboard"]), "Keep version B")]
        assert any(e["type"] == "picked" for e in run.events)
    finally:
        models.set_backend(None)


def test_a_version_repeating_a_layout_is_asked_again_once(mgr):
    script = VariantScript(["vertical_list", "vertical_list", "horizontal_timeline"])
    run = go(mgr, script, variants=2)
    layouts = [v["spec"]["layout"].split("/")[0] for v in run.gate["payload"]["variants"]]
    assert layouts == ["vertical_list", "horizontal_timeline"]
    assert "Same layout family as another version (vertical_list)" in [p for r, p in script.prompts if r == "layout"][-1]


def test_abandoning_at_the_pick_releases_and_no_editor_still_offers_the_versions(mgr):
    run = go(mgr, VariantScript(["vertical_list", "card_list"]), variants=2)
    mgr.decide(run.id, "outline", "approve")
    run = mgr.wait(run.id)
    assert run.gate["kind"] == "pick" and all("group_id" not in o for o in run.options)
    mgr.decide(run.id, "pick", "abandon")
    run = mgr.wait(run.id)
    assert run.status == "abandoned" and mgr.history.get(run.history_id)["status"] != "reserved"


def test_one_version_is_the_old_flow(mgr):
    run = go(mgr, Script(), variants=1)
    assert "variants" not in run.gate["payload"] and run.variants == 1
