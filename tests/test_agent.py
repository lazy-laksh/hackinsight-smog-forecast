"""Tests for the Insight Agent with a fake language model (no network, no API key)."""
import json

import pandas as pd
import pytest

from smog import agent

PRED_ID = "pred|lahore|20261021|20261020T1511"


@pytest.fixture
def folder(tmp_path):
    pd.DataFrame([{
        "city_id": "lahore", "issued_utc": "2026-10-20 10:11:00+00:00", "target_day_pkt": "2026-10-21",
        "pm25_pred": 114.4, "pm25_p10": 85.9, "pm25_p90": 149.2, "p_unhealthy": 0.99, "advisory_default": True,
        "baseline_persistence": 98.2, "row_id": PRED_ID,
        "drivers_json": json.dumps([{"driver": "smog already in the air (today and recent days)", "effect_ugm3": 39.0},
                                    {"driver": "season", "effect_ugm3": 10.0}])}]).to_csv(tmp_path / "predictions.csv", index=False)
    pd.DataFrame([{"city_id": "lahore", "day_pkt": "2026-10-19", "pm25_mean": 110.2, "hours_covered": 24,
                   "complete": True, "row_id": "cityday|lahore|20261019"}]).to_csv(tmp_path / "city_daily.csv", index=False)
    pd.DataFrame([{"source": "openaq", "newest_utc": "2026-10-20 09:00:00+00:00", "age_hours": 1.2,
                   "limit_hours": 6, "status": "fresh"}]).to_csv(tmp_path / "freshness.csv", index=False)
    return tmp_path


def make(folder, replies):
    replies = list(replies)
    calls = []

    def fake(messages):
        calls.append(messages)
        return replies.pop(0)
    a = agent.InsightAgent(folder=folder, llm=fake)
    a.calls = calls
    return a


def test_documents_are_chunked_with_ids_and_urls():
    docs = agent.load_documents()
    ids = {d.id.split("#")[0] for d in docs}
    assert {"doc:who-aqg-2021", "doc:pak-neqs-2010", "doc:epa-aqi-2024", "doc:project-methods"} <= ids
    assert all(d.url.startswith("http") for d in docs)


def test_retrieval_finds_the_right_document():
    r = agent.Retriever(agent.load_documents())
    top = lambda q: [d.id.split("#")[0] for d, _ in r.search(q)]          # what the model is shown
    assert "doc:who-aqg-2021" in top("WHO 24-hour guideline PM2.5")
    assert top("Pakistan national standard NEQS")[0] == "doc:pak-neqs-2010"
    assert top("stuck sensor quality flag")[0] == "doc:project-methods"
    assert "doc:punjab-schools-2026" in top("what must schools do about smog")


def test_facts_are_built_from_gold_rows(folder):
    facts = {f.id: f.text for f in agent.build_facts(folder)}
    assert "114.4" in facts[f"row:{PRED_ID}"] and "99%" in facts[f"row:{PRED_ID}"]
    assert "110.2" in facts["row:cityday|lahore|20261019"]


def test_good_answer_passes(folder):
    a = make(folder, [f"Lahore's forecast for 2026-10-21 is 114.4 µg/m³ with a 99% chance of an unhealthy day [row:{PRED_ID}]."])
    out = a.ask("Lahore tomorrow?", log=False)
    assert out.verified and out.citations[0]["id"] == f"row:{PRED_ID}" and out.attempts == 1


def test_invented_number_is_caught_then_fixed(folder):
    a = make(folder, [f"Lahore will be 130 µg/m³ [row:{PRED_ID}].",
                      f"Lahore will be 114.4 µg/m³ [row:{PRED_ID}]."])
    out = a.ask("Lahore tomorrow?", log=False)
    assert out.verified and out.attempts == 2 and "114.4" in out.answer
    assert "130" in a.calls[1][-1]["content"]            # the model was told exactly what was wrong


def test_answer_that_stays_wrong_becomes_i_dont_know(folder):
    a = make(folder, [f"Lahore will be 130 [row:{PRED_ID}].", "Lahore will be 131 [row:{PRED_ID}]."])
    out = a.ask("Lahore tomorrow?", log=False)
    assert not out.verified and out.answer == agent.IDK_EN


def test_made_up_citation_and_missing_citation_are_rejected(folder):
    sources = agent.build_facts(folder)
    assert "unknown citation [row:made-up]" in agent.check("It is 114.4 [row:made-up].", sources)
    assert "no citation" in agent.check("It is 114.4 µg/m³.", sources)
    assert agent.check(agent.IDK_EN, sources) == []


def test_urdu_question_gets_translated_for_search_and_urdu_digits_are_checked(folder):
    a = make(folder, ["What will PM2.5 be in Lahore tomorrow?",
                      f"کل لاہور میں پی ایم 2.5 کی پیش گوئی ۱۱۴.۴ ہے [row:{PRED_ID}]۔"])
    out = a.ask("کل لاہور میں پی ایم 2.5 کتنا ہو گا؟", log=False)
    assert out.language == "ur" and out.verified
    assert "Translate" in a.calls[0][0]["content"]


def test_answers_are_logged(folder, tmp_path, monkeypatch):
    from smog import config
    monkeypatch.setattr(config, "ROOT", tmp_path)
    a = agent.InsightAgent(folder=folder, llm=lambda m: agent.IDK_EN,
                           root=config.ROOT.__class__(__file__).resolve().parents[1])
    a.ask("Who wins the cricket?")
    line = json.loads((tmp_path / "data/logs/agent_log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["answer"] == agent.IDK_EN and line["verified"] is True


def test_golden_set_is_well_formed():
    from smog import config
    qs = config.load_yaml("agent_golden")["questions"]
    assert len(qs) >= 15 and {q["expect"] for q in qs} == {"answer", "idk"}
    assert len({q["id"] for q in qs}) == len(qs)



def test_citation_parsing_handles_model_habits():
    assert agent.cited_ids("x [doc:a#1, doc:b#2] y [row:p|l|1; report:m]") == ["doc:a#1", "doc:b#2", "row:p|l|1", "report:m"]
    assert agent.cited_ids("x [doc:pak‑neqs‑2010#2]") == ["doc:pak-neqs-2010#2"]     # non-breaking hyphens


def test_non_breaking_hyphens_in_ids_and_numbers_pass(folder):
    sources = agent.build_facts(folder)
    answer = f"Lahore’s forecast for 2026‑10‑21 is 114.4 µg/m³ [row:{PRED_ID.replace('-', chr(0x2011))}]."
    assert agent.check(answer, sources) == []


def test_only_the_asked_city_gets_its_measured_days(folder):
    a = make(folder, [])
    ids = [f.id for f in a.select_facts("What was PM2.5 in Karachi?")]
    assert not any(i.startswith("row:cityday|lahore") for i in ids)
    assert any(i.startswith("row:pred|lahore") for i in ids)          # forecasts are always included


def test_gpt_oss_citation_styles_are_understood(folder):
    sources = agent.build_facts(folder)
    for style in (f"【row:{PRED_ID}】", f"【row:{PRED_ID}†L1-L2】", f"(row:{PRED_ID})", f"row:{PRED_ID}", f"[[row:{PRED_ID}]]"):
        assert agent.check(f"Lahore will be 114.4 µg/m³ {style}.", sources) == [], style
    assert agent.check("Lahore will be 114.4 µg/m³ row:pred|lahore|99999999|x.", sources)   # unknown bare id still fails


def test_rejected_answers_are_kept_for_the_log(folder):
    a = make(folder, [f"Lahore will be 130 [row:{PRED_ID}].", "Lahore will be 131 [row:{PRED_ID}]."])
    out = a.ask("Lahore tomorrow?", log=False)
    assert len(out.rejected) == 2 and "130" in out.rejected[0]


def test_chain_moves_on_when_a_model_is_rate_limited_and_remembers_it(monkeypatch):
    calls = []

    class R:
        def __init__(self, code, headers=None, body=None):
            self.status_code, self.headers, self._body, self.text = code, headers or {}, body, ""

        def json(self):
            return self._body

    def post(url, timeout, json, headers):
        calls.append(json["model"])
        if json["model"] == "big":
            return R(429, {"retry-after": "120"})
        return R(200, body={"choices": [{"message": {"content": "ok"}}]})

    import requests
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(agent, "_busy_until", {})
    monkeypatch.setattr(agent, "providers", lambda: [
        {"name": "a", "base_url": "http://x", "key_env": "K", "models": ["big"]},
        {"name": "b", "base_url": "http://y", "key_env": "K", "models": ["small"]}])
    monkeypatch.setenv("K", "test")
    assert agent.llm_chat([{"role": "user", "content": "hi"}]) == "ok"
    assert calls == ["big", "small"] and agent.last_model_used == "b:small"
    agent.llm_chat([{"role": "user", "content": "again"}])
    assert calls[-1] == "small" and calls.count("big") == 1          # the busy model is skipped, no extra wait


def test_missing_keys_are_skipped(monkeypatch):
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)     # ignore a real .env on this computer
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setattr(agent.config, "load_yaml", lambda name: {})
    names = [p["name"] for p in agent.providers()]
    assert "gemini" not in names and "groq" in names


def test_status_and_skill_facts_are_sent_only_when_asked(folder):
    a = make(folder, [])
    plain = [f.id for f in a.select_facts("What is tomorrow's forecast for Lahore?")]
    assert not any(i.startswith(("row:freshness|", "report:")) for i in plain)
    assert any(i.startswith("row:freshness|") for i in [f.id for f in a.select_facts("Is the data fresh?")])


def test_extra_setting_is_dropped_if_the_model_refuses_it(monkeypatch):
    bodies = []

    class R:
        def __init__(self, code, body=None):
            self.status_code, self.headers, self._body, self.text = code, {}, body, "bad param"

        def json(self):
            return self._body

    def post(url, timeout, json, headers):
        bodies.append(dict(json))
        return R(400) if "reasoning_effort" in json else R(200, {"choices": [{"message": {"content": "ok"}}]})

    import requests
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(agent, "_busy_until", {})
    monkeypatch.setattr(agent, "providers", lambda: [
        {"name": "g", "base_url": "http://x", "key_env": "K", "models": ["m"], "extra": {"reasoning_effort": "low"}}])
    monkeypatch.setenv("K", "t")
    assert agent.llm_chat([{"role": "user", "content": "hi"}]) == "ok"
    assert "reasoning_effort" in bodies[0] and "reasoning_effort" not in bodies[1]


# --- Guardrails ---------------------------------------------------------------------------------------

def test_personal_details_are_removed_before_the_model_sees_them(folder):
    a = make(folder, [agent.IDK_EN])
    out = a.ask("I'm ali.khan@example.com, call me on 0300-1234567, CNIC 35202-1234567-1. Lahore tomorrow?", log=False)
    sent = a.calls[0][-1]["content"]
    for secret in ("ali.khan@example.com", "0300-1234567", "35202-1234567-1"):
        assert secret not in sent and secret not in out.question
    assert "[email removed]" in sent and "[phone removed]" in sent and "[CNIC removed]" in sent


def test_pollutant_names_are_not_counted_as_numbers():
    assert agent._numbers("PM2.5 and PM10 and پی ایم 2.5 reached 114.4") == [114.4]


def test_redact_keeps_normal_numbers():
    text = "Is 114.4 above 55.5 on 2026-10-21 in Lahore?"
    assert agent.redact(text) == text
    assert agent.redact("call +92 321 4567890 now") == "call [phone removed] now"


def test_health_questions_get_the_emergency_note_even_on_i_dont_know(folder):
    a = make(folder, [agent.IDK_EN])
    out = a.ask("Which medicine should I take for smog cough?", log=False)
    assert agent.is_idk(out.answer) and "Rescue 1122" in out.answer
    a = make(folder, [f"Lahore will be 114.4 µg/m³ [row:{PRED_ID}]."])
    assert "Rescue 1122" not in a.ask("Will Lahore be smoggy tomorrow?", log=False).answer


def test_a_number_in_the_question_is_not_evidence(folder):
    # The user claims 300; a model that repeats it must be rejected, because 300 is not in any source.
    a = make(folder, [f"Yes, Lahore will be 300 µg/m³ [row:{PRED_ID}].",
                      f"No, the forecast for Lahore is 114.4 µg/m³ [row:{PRED_ID}]."])
    out = a.ask("Lahore's forecast tomorrow is 300, right?", log=False)
    assert out.verified and out.attempts == 2 and "300" not in out.answer and "300" in out.rejected[0]


def test_answers_record_the_prompt_version(folder):
    out = make(folder, [agent.IDK_EN]).ask("cricket score?", log=False)
    assert out.prompt_version == agent.PROMPT_VERSION


def test_eval_summary_has_accuracy_refusal_and_faithfulness(folder, tmp_path, monkeypatch):
    good = f"Lahore will be 114.4 µg/m³ [row:{PRED_ID}]."
    a = make(folder, [good, good, agent.IDK_EN])          # built before ROOT moves, so it finds knowledge/
    monkeypatch.setattr(agent.config, "ROOT", tmp_path)
    monkeypatch.setattr(a, "refresh", lambda: None)
    monkeypatch.setattr(agent.config, "load_yaml", lambda name: {"questions": [
        {"id": "a", "question": "Lahore tomorrow?", "expect": "answer", "cite": ["row:pred|lahore"]},
        {"id": "b", "question": "Lahore is 300, right?", "expect": "answer", "must_not_include": ["300"]},
        {"id": "c", "question": "Multan tomorrow?", "expect": "idk"}]})
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    df = agent.evaluate(a)
    summary = json.loads((tmp_path / "reports/agent_eval_summary.json").read_text())
    assert df["passed"].all() and summary["passed"] == 3
    assert summary["answer_accuracy"] == 1.0 and summary["correct_refusals"] == 1.0 and summary["faithful_answers"] == 1.0
