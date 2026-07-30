import pytest
from pydantic import ValidationError

from src.lians.schemas import ContextRequest, RecallRequest


def test_recall_and_context_allow_benchmark_grade_candidate_depth():
    assert RecallRequest(agent_id="agent", query="question", k=200).k == 200
    assert ContextRequest(agent_id="agent", query="question", k=200).k == 200


def test_recall_candidate_depth_remains_bounded():
    with pytest.raises(ValidationError):
        RecallRequest(agent_id="agent", query="question", k=201)
