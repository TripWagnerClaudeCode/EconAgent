import pipeline


def test_extract_json_fenced_and_raw():
    assert pipeline.extract_json('text\n```json\n{"a": 1}\n```\nmore') == {"a": 1}
    assert pipeline.extract_json('{"b": 2}') == {"b": 2}


def test_prompts_carry_date_and_limits():
    assert "Present at most 2 ideas" in pipeline.analyst_prompt(2)
    assert "Shortlist at most 3 candidates" in pipeline.collector_prompt(3)
