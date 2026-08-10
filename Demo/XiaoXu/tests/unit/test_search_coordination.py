import pytest

from private_agent.search.coordinator import (
    SearchCoordinator,
    SearchPolicyCode,
    SearchPolicyError,
    SearchTurnState,
)
from private_agent.search.normalization import (
    canonicalize_web_url,
    normalize_search_query,
    search_query_fingerprint,
)


def test_query_normalization_preserves_meaningful_search_syntax() -> None:
    raw = '  Ｓｉｔｅ：example.com   "Exact Phrase"？  '

    assert normalize_search_query(raw) == 'Site:example.com "Exact Phrase"?'
    assert search_query_fingerprint(raw) == search_query_fingerprint(
        'site:example.com "Exact Phrase"'
    )


def test_url_canonicalization_removes_tracking_without_changing_page_identity() -> None:
    url = (
        "HTTPS://Example.COM:443/path?utm_source=search&b=2&a=1"
        "&gclid=ignored#section"
    )

    assert canonicalize_web_url(url) == "https://example.com/path?a=1&b=2"
    assert canonicalize_web_url("https://example.com") == "https://example.com/"
    assert canonicalize_web_url("ftp://example.com/file") is None


def test_knowledge_allows_one_rewrite_only_after_an_empty_result() -> None:
    coordinator = SearchCoordinator(SearchTurnState())

    first = coordinator.prepare_knowledge("memory architecture", ["work", "personal"], max_queries=2)
    with pytest.raises(SearchPolicyError) as pending:
        coordinator.prepare_knowledge("agent memory design", ["personal", "work"], max_queries=2)
    assert pending.value.code == SearchPolicyCode.QUERY_LIMIT_REACHED
    assert coordinator.state.knowledge_query_count == 1
    empty = coordinator.accept_knowledge(first, [])
    second = coordinator.prepare_knowledge("agent memory design", ["personal", "work"], max_queries=2)

    assert empty.progress.query_index == 1
    assert empty.progress.remaining_queries == 1
    assert second.query_index == 2
    with pytest.raises(SearchPolicyError) as caught:
        coordinator.prepare_knowledge("long-term memory schema", ["personal", "work"], max_queries=2)
    assert caught.value.code == SearchPolicyCode.QUERY_LIMIT_REACHED


def test_knowledge_closes_after_new_evidence_and_deduplicates_exact_chunks() -> None:
    coordinator = SearchCoordinator(SearchTurnState())
    prepared = coordinator.prepare_knowledge("agent memory", ["personal"], max_queries=2)
    hit = {"doc_id": "doc-1", "chunk_id": "chunk-1", "content": "first"}

    batch = coordinator.accept_knowledge(prepared, [hit, dict(hit)])

    assert batch.items == [hit]
    assert batch.progress.new_results == 1
    assert batch.progress.duplicate_results == 1
    with pytest.raises(SearchPolicyError) as caught:
        coordinator.prepare_knowledge("another angle", ["personal"], max_queries=2)
    assert caught.value.code == SearchPolicyCode.KNOWLEDGE_EVIDENCE_FOUND


def test_normalized_duplicate_query_is_blocked_without_consuming_another_slot() -> None:
    coordinator = SearchCoordinator(SearchTurnState())
    coordinator.prepare_web("  Python   3.13？", max_queries=3)

    with pytest.raises(SearchPolicyError) as caught:
        coordinator.prepare_web("python 3.13", max_queries=3)

    assert caught.value.code == SearchPolicyCode.DUPLICATE_QUERY
    assert coordinator.state.web_query_count == 1


def test_web_results_are_deduplicated_across_queries_with_stable_source_numbers() -> None:
    coordinator = SearchCoordinator(SearchTurnState())
    first = coordinator.prepare_web("Python release", max_queries=3)
    first_batch = coordinator.accept_web(
        first,
        [
            {
                "title": "Python",
                "url": "https://example.com/release?utm_source=search",
                "content": "short",
            },
            {
                "title": "Python release notes",
                "url": "https://EXAMPLE.com:443/release#details",
                "content": "a more complete release summary",
            },
        ],
    )
    second = coordinator.prepare_web("Python release compatibility", max_queries=3)
    second_batch = coordinator.accept_web(
        second,
        [
            {
                "title": "Repeated",
                "url": "https://example.com/release",
                "content": "duplicate",
            },
            {
                "title": "Compatibility",
                "url": "https://example.org/compat",
                "content": "new evidence",
            },
        ],
    )

    assert [item["source_index"] for item in first_batch.items] == [1]
    assert first_batch.updates[0]["source_index"] == 1
    assert first_batch.progress.total_unique_results == 1
    assert [item["source_index"] for item in second_batch.items] == [2]
    assert second_batch.progress.total_unique_results == 2
    assert second_batch.progress.duplicate_results == 1


def test_invalid_web_url_is_rejected_without_being_reported_as_duplicate() -> None:
    coordinator = SearchCoordinator(SearchTurnState())
    prepared = coordinator.prepare_web("safe sources", max_queries=3)

    batch = coordinator.accept_web(
        prepared,
        [
            {"title": "FTP", "url": "ftp://example.com/file", "content": "x"},
            {"title": "HTTP", "url": "https://example.com/", "content": "y"},
        ],
    )

    assert [item["title"] for item in batch.items] == ["HTTP"]
    assert batch.progress.duplicate_results == 0


def test_web_allows_three_unique_queries_and_then_blocks_the_fourth() -> None:
    coordinator = SearchCoordinator(SearchTurnState())
    for query in ("query one", "query two", "query three"):
        prepared = coordinator.prepare_web(query, max_queries=3)
        coordinator.accept_web(prepared, [])

    with pytest.raises(SearchPolicyError) as caught:
        coordinator.prepare_web("query four", max_queries=3)

    assert caught.value.code == SearchPolicyCode.QUERY_LIMIT_REACHED


def test_web_expansion_waits_for_previous_query_result() -> None:
    coordinator = SearchCoordinator(SearchTurnState())
    first = coordinator.prepare_web("primary query", max_queries=3)

    with pytest.raises(SearchPolicyError) as caught:
        coordinator.prepare_web("missing fact query", max_queries=3)

    assert caught.value.code == SearchPolicyCode.QUERY_LIMIT_REACHED
    assert coordinator.state.web_query_count == 1
    coordinator.accept_web(first, [])
    assert coordinator.prepare_web("missing fact query", max_queries=3).query_index == 2


def test_backend_failure_closes_only_the_failed_search_kind() -> None:
    coordinator = SearchCoordinator(SearchTurnState())
    prepared = coordinator.prepare_knowledge("private facts", max_queries=2)
    coordinator.fail(prepared, "KNOWLEDGE_UNAVAILABLE")

    with pytest.raises(SearchPolicyError) as caught:
        coordinator.prepare_knowledge("retry private facts", max_queries=2)
    assert caught.value.code == SearchPolicyCode.BACKEND_UNAVAILABLE
    assert coordinator.prepare_web("public facts", max_queries=3).query_index == 1
