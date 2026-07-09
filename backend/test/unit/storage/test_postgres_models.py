from yuxi.storage.postgres.models_knowledge import IncomingDocument


def test_knowledge_models_indexes_have_unique_names():
    for table in IncomingDocument.metadata.tables.values():
        index_names = [index.name for index in table.indexes]

        assert len(index_names) == len(set(index_names)), table.name
