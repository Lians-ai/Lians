from src.lians.query_planner import plan_query


def test_simple_fact_question_stays_on_fast_path():
    plan = plan_query("What is Jordan's favorite color?")
    assert plan.variants == ("What is Jordan's favorite color?",)
    assert plan.scopes == ("episodic",)
    assert plan.complex is False


def test_relative_time_in_simple_episode_does_not_force_broadening():
    plan = plan_query("How did Melanie feel after the accident?")
    assert plan.variants == ("How did Melanie feel after the accident?",)


def test_temporal_aggregate_question_gets_bounded_facets():
    plan = plan_query("What activities did Jordan do before moving?", max_variants=3)
    assert plan.variants[0] == "What activities did Jordan do before moving?"
    assert plan.scopes == ("episodic", "temporal", "history")
    assert "chronology" in plan.variants[1]
    assert "complete history" in plan.variants[2]
    assert plan.complex is True


def test_relational_question_adds_reasoning_facet_without_answer_terms():
    plan = plan_query("Why might Jordan feel supported by Casey?")
    assert plan.scopes == ("episodic", "relational")
    assert "relationships background reasons preferences" in plan.variants[1]


def test_variant_count_is_hard_bounded():
    plan = plan_query(
        "Why might all activities before last year influence their relationship?",
        max_variants=2,
    )
    assert len(plan.variants) == 2
