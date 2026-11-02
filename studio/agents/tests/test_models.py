"""Key rotation and fallbacks (D-027): a failing key or provider hands over; a bad answer does not."""
import pytest

from studio_agents import models


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    for v in ("GEMINI_API_KEY", "GEMINI_API_KEYS", "GROQ_API_KEY", "GROQ_API_KEYS", *(f"GEMINI_API_KEY_{i}" for i in range(2, 10))):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g-one")
    monkeypatch.setenv("GEMINI_API_KEY_2", "g-two")
    monkeypatch.setenv("GROQ_API_KEYS", "q-one, q-two")
    monkeypatch.setattr(models, "build_candidate", lambda c: c)       # the "model" is the candidate itself
    models._cooling.clear(); models._preferred.clear(); models.rotation_log.clear()


def test_keys_come_from_numbered_and_listed_variables():
    assert models.keys("gemini") == ["g-one", "g-two"]
    assert models.keys("groq") == ["q-one", "q-two"]


def test_a_failing_key_hands_over_to_the_next_and_is_remembered():
    seen = []

    def call(model, c):
        seen.append(c.key)
        if c.key == "g-one":
            raise RuntimeError("400 API key not valid. Please pass a valid API key.")
        return "ok"
    assert models.with_rotation("brief", call) == "ok"
    assert seen == ["g-one", "g-two"]
    assert models.rotation_log[0]["failed"].endswith("(key 1)")
    seen.clear()
    assert models.with_rotation("brief", call) == "ok"
    assert seen == ["g-two"], "the key that worked is tried first next time"


def test_quota_rests_the_key_and_all_gemini_failing_falls_back():
    seen = []

    def call(model, c):
        seen.append((c.provider, c.model, c.key))
        if c.provider == "gemini":
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
        return c.provider
    assert models.with_rotation("layout", call) == "groq"
    gemini = ["gemini-pro-latest", "gemini-flash-latest", "gemini-2.5-flash", "gemini-flash-lite-latest"]
    assert [(p, m) for p, m, _ in seen] == [("gemini", g) for g in gemini for _ in (1, 2)] + [("groq", "openai/gpt-oss-120b")]
    assert all(models._cooling.get(("gemini", "gemini-pro-latest", k)) for k in ("g-one", "g-two")), "rate-limited keys rest"
    assert all(c.provider != "gemini" for c in models.candidates("layout")), "resting keys are skipped"


def test_the_same_model_is_not_tried_twice():
    flash = [(c.model, c.key) for c in models.candidates("brief") if c.model == "gemini-flash-latest"]
    assert flash == [("gemini-flash-latest", "g-one"), ("gemini-flash-latest", "g-two")]


def test_vision_steps_never_fall_back_to_text_only_models():
    def call(model, c):
        raise RuntimeError("503 UNAVAILABLE")
    with pytest.raises(models.ModelError, match="every key and fallback failed"):
        models.with_rotation("critic", call, vision=True)
    assert all(c.provider == "gemini" for c in models.candidates("critic", vision=True))


def test_a_bad_answer_is_not_a_key_problem():
    calls = []

    def call(model, c):
        calls.append(c.key)
        raise ValueError("structured output did not validate")
    with pytest.raises(ValueError):
        models.with_rotation("brief", call)
    assert calls == ["g-one"]


def test_no_keys_is_a_clear_message(monkeypatch):
    for v in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GROQ_API_KEYS"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(models.ModelError, match="no usable API key"):
        models.with_rotation("brief", lambda m, c: "x")


def test_quota_on_one_model_does_not_block_another(monkeypatch):
    monkeypatch.setattr(models.time, "sleep", lambda s: None)
    def call(model, c):
        if c.model == "gemini-pro-latest":
            raise RuntimeError("429 You exceeded your current quota")
        return c.model
    assert models.with_rotation("layout", call) == "gemini-flash-latest"
    assert [c.model for c in models.candidates("critic", vision=True)][:1] == ["gemini-flash-latest"], "pro rests, flash doesn't"


def test_a_busy_model_gets_one_quick_retry(monkeypatch):
    monkeypatch.setattr(models.time, "sleep", lambda s: None)
    attempts = []
    def call(model, c):
        attempts.append(c.key)
        if len(attempts) == 1:
            raise RuntimeError("503 UNAVAILABLE: high demand")
        return "ok"
    assert models.with_rotation("brief", call) == "ok" and attempts == ["g-one", "g-one"]
