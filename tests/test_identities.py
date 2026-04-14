from kogwistar.id_provider import stable_id


def test_stable_ids_are_deterministic():
    left = str(stable_id("llm_wiki.source", "demo", "file:///contracts/acme.txt"))
    right = str(stable_id("llm_wiki.source", "demo", "file:///contracts/acme.txt"))
    assert left == right
