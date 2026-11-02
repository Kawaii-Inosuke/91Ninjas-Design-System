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
