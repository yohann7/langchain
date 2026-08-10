import json

from knowledge_service.storage.milvus import (
    MilvusKnowledgeStore,
    analyzer_name,
    build_filter_expression,
)


def test_filter_expression_is_owner_and_active_version_scoped() -> None:
    owner = 'alice" or true or owner_id == "bob'
    expression = build_filter_expression(
        owner_id=owner,
        kb_ids=["kb-1"],
        version_ids=["ver-1", "ver-2"],
    )

    assert f"owner_id == {json.dumps(owner)}" in expression
    assert 'kb_id in ["kb-1"]' in expression
    assert 'version_id in ["ver-1", "ver-2"]' in expression
    assert "is_active == true" in expression
    assert analyzer_name("chinese") == "cn"
    assert analyzer_name("english") == "english"


class _ExistingClient:
    def __init__(self) -> None:
        self.loaded = False
        self.deleted_filter = ""

    def has_collection(self, _name):
        return True

    def describe_collection(self, _name):
        names = [
            "chunk_id", "owner_id", "kb_id", "doc_id", "version_id",
            "content", "dense_vector", "sparse_vector",
        ]
        fields = [{"name": name} for name in names]
        next(item for item in fields if item["name"] == "dense_vector")["params"] = {"dim": 3}
        return {"fields": fields}

    def load_collection(self, _name):
        self.loaded = True

    def upsert(self, *, collection_name, data):
        assert collection_name == "chunks"
        return {"upsert_count": len(data)}

    def query(self, **_values):
        return [{"chunk_id": "1"}, {"chunk_id": "2"}]

    def delete(self, *, collection_name, filter):
        assert collection_name == "chunks"
        self.deleted_filter = filter
        return {"delete_count": 2}

    def list_collections(self):
        return ["chunks"]

    def close(self):
        self.closed = True


def test_existing_collection_is_validated_before_crud() -> None:
    client = _ExistingClient()
    store = MilvusKnowledgeStore(
        uri="http://milvus:19530", database="knowledge", collection="chunks",
        dimension=3, client=client,
    )
    row = {
        "chunk_id": "1", "owner_id": "alice", "kb_id": "kb-1",
        "doc_id": "doc-1", "version_id": "ver-1", "content": "text",
        "dense_vector": [1.0, 0.0, 0.0],
    }

    assert store.upsert_chunks([row]) == 1
    assert client.loaded is True
    assert store.count_version(owner_id="alice", kb_id="kb-1", version_id="ver-1") == 2
    assert store.delete_version(owner_id="alice", kb_id="kb-1", version_id="ver-1") == 2
    assert 'version_id == "ver-1"' in client.deleted_filter
    assert store.status()["ready"] is True
