import time
from app.services.cache import get_cache_service


def test_cache_set_and_get():
    cache = get_cache_service()
    question = "How many sick leaves do we have?"
    user_role = "employee"
    dummy_response = {
        "answer": "You get 10 paid sick days per calendar year.",
        "source_used": "private_kb",
        "cost_usd": 0.0012,
    }

    cache.set_response(question, user_role, dummy_response, ttl=10)
    cached = cache.get_response(question, user_role)
    assert cached is not None
    assert cached["answer"] == dummy_response["answer"]
    assert cached["source_used"] == "private_kb"


def test_rate_limiting():
    cache = get_cache_service()
    user_id = "test_rate_user"

    # Should allow requests under the limit
    for _ in range(5):
        assert cache.check_rate_limit(user_id, limit=5) is True

    # 6th request should be blocked
    assert cache.check_rate_limit(user_id, limit=5) is False


def test_session_memory():
    cache = get_cache_service()
    session_id = f"test_session_{int(time.time())}"

    cache.add_session_message(session_id, "user", "What is remote policy?")
    cache.add_session_message(session_id, "assistant", "Up to 2 days per week.")

    history = cache.get_session_history(session_id, max_turns=5)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["content"] == "Up to 2 days per week."
