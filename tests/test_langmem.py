import uuid
from app.services.memory import get_langmem_service


def test_langmem_save_and_retrieve():
    service = get_langmem_service()
    user_id = f"test_emp_{uuid.uuid4().hex[:8]}"

    fact1 = "Employee works in the Bangalore Technology Center as a Senior Developer"
    fact2 = "Employee reported having 15 days of annual leave remaining"

    service.save_memory(user_id, fact1, category="profile")
    service.save_memory(user_id, fact2, category="leave_status")

    memories = service.get_user_memories(user_id)
    assert len(memories) == 2
    assert fact1 in memories
    assert fact2 in memories


def test_langmem_deduplication():
    service = get_langmem_service()
    user_id = f"test_emp_{uuid.uuid4().hex[:8]}"

    fact = "Employee works remotely on Tuesdays and Thursdays"
    service.save_memory(user_id, fact)
    service.save_memory(user_id, fact)

    memories = service.get_user_memories(user_id)
    assert len(memories) == 1


def test_langmem_clear_user():
    service = get_langmem_service()
    user_id = f"test_emp_{uuid.uuid4().hex[:8]}"

    service.save_memory(user_id, "Employee is on probation until June 2025")
    assert len(service.get_user_memories(user_id)) == 1

    service.clear_user_memories(user_id)
    assert len(service.get_user_memories(user_id)) == 0
