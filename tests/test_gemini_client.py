import pytest

from conftest import FakeBackend, cand
from darkcnn.analyze import Selection
from darkcnn.gemini import BudgetExceeded, GeminiClient, GeminiError, InvalidResponse, TransientError


def client(tmp_path, backend, budget=10, retries=3, sleeps=None):
    return GeminiClient(backend, tmp_path / "usage.json", budget, retries=retries, backoff_s=2,
                        sleep=(sleeps.append if sleeps is not None else (lambda s: None)), today=lambda: "2026-10-07")


def test_success_records_usage(tmp_path):
    c = client(tmp_path, FakeBackend([cand(0, 1)]))
    out = c.generate_json("p", Selection)
    assert len(out.candidates) == 1 and c.requests_today() == 1


def test_transient_errors_retry_with_exponential_backoff(tmp_path):
    sleeps = []
    b = FakeBackend([cand(0, 1)], script=[TransientError("429"), TransientError("503")])
    c = client(tmp_path, b, sleeps=sleeps)
    c.generate_json("p", Selection)
    assert b.calls == 3 and sleeps == [2, 4] and c.requests_today() == 3  # tentativas contam na cota


def test_gives_up_after_retries(tmp_path):
    b = FakeBackend(script=[TransientError("429")] * 5)
    with pytest.raises(GeminiError, match="4 tentativas"):
        client(tmp_path, b, retries=3).generate_json("p", Selection)
    assert b.calls == 4


def test_invalid_response_retries_with_hint_in_prompt(tmp_path):
    b = FakeBackend([cand(0, 1)], script=[InvalidResponse("nota fora de 1..10")])
    client(tmp_path, b).generate_json("PROMPT", Selection)
    assert b.calls == 2 and "nota fora de 1..10" in b.prompts[1] and b.prompts[1].startswith("PROMPT")


def test_budget_blocks_before_calling(tmp_path):
    b = FakeBackend([cand(0, 1)])
    c = client(tmp_path, b, budget=1)
    c.generate_json("p", Selection)
    with pytest.raises(BudgetExceeded):
        c.generate_json("p", Selection)
    assert b.calls == 1


def test_non_transient_error_propagates_immediately(tmp_path):
    b = FakeBackend(script=[GeminiError("400 INVALID_ARGUMENT")])
    with pytest.raises(GeminiError, match="400"):
        client(tmp_path, b).generate_json("p", Selection)
    assert b.calls == 1
