"""PostgreSQL 知识库相关数据模型"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from yuxi.storage.postgres.models_business import Base
from yuxi.utils.datetime_utils import utc_now_naive

JSON_VALUE = JSON().with_variant(JSONB, "postgresql")


class KnowledgeBase(Base):
    """知识库模型"""

    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("kb_id", name="uq_knowledge_bases_kb_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_id = Column(String(80), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    kb_type = Column(String(32), nullable=False, index=True)
    embedding_model_spec = Column(String(512))
    llm_model_spec = Column(String(512))
    query_params = Column(JSON_VALUE)
    additional_params = Column(JSON_VALUE)
    share_config = Column(JSON_VALUE)
    mindmap = Column(JSON_VALUE)
    mindmap_file_ids = Column(JSON_VALUE)
    mindmap_metadata = Column(JSON_VALUE)
    sample_questions = Column(JSON_VALUE)
    created_by = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeFile(Base):
    """知识文件模型"""

    __tablename__ = "knowledge_files"
    __table_args__ = (UniqueConstraint("file_id", name="uq_knowledge_files_file_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(String(64), unique=True, nullable=False, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="SET NULL"), index=True)
    filename = Column(String(512), nullable=False)
    original_filename = Column(String(512))
    file_type = Column(String(64))
    path = Column(String(1024))
    minio_url = Column(String(1024))
    markdown_file = Column(String(1024))
    status = Column(String(32), default="uploaded", index=True)
    content_hash = Column(String(128), index=True)
    file_size = Column(BigInteger)
    chunk_count = Column(Integer, default=0)
    token_count = Column(BigInteger, default=0)
    content_type = Column(String(64))
    processing_params = Column(JSON_VALUE)
    is_folder = Column(Boolean, default=False)
    error_message = Column(Text)
    created_by = Column(String(64))
    updated_by = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class IncomingDocument(Base):
    """一份来文一行，附件由 ``incoming_document_files`` 保存。"""

    __tablename__ = "incoming_documents"
    __table_args__ = (
        UniqueConstraint("incoming_id", name="uq_incoming_documents_incoming_id"),
        UniqueConstraint(
            "source_system",
            "source_document_id",
            name="uq_incoming_documents_source_document_identity",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    incoming_id = Column(String(64), unique=True, nullable=False, index=True)
    source_system = Column(String(64), nullable=False, index=True)
    source_document_id = Column(String(256), nullable=False)
    document_metadata = Column(JSON_VALUE, nullable=False, default=dict)
    status = Column(String(32), default="uploaded", index=True)
    ai_classification = Column(String(128))
    classification_confidence = Column(Float)
    classification_evidence = Column(Text)
    additional_classifications = Column(JSON_VALUE)
    confirmed_classification = Column(String(128))
    review_status = Column(String(32), default="draft", index=True)
    confirmed_by = Column(String(64))
    confirmed_at = Column(DateTime(timezone=True))
    summary = Column(Text)
    processing_error = Column(Text)
    linked_kb_id = Column(String(80))
    knowledge_import_status = Column(String(32), default="none", index=True)
    knowledge_import_task_id = Column(String(64))
    knowledge_import_error = Column(Text)
    archived_at = Column(DateTime(timezone=True))
    archived_by = Column(String(64))
    created_by = Column(String(64))
    updated_by = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class IncomingDocumentFile(Base):
    """来文主文件或附件；不保存独立分类和正式业务结论。"""

    __tablename__ = "incoming_document_files"
    __table_args__ = (
        UniqueConstraint("incoming_file_id", name="uq_incoming_document_files_incoming_file_id"),
        UniqueConstraint("incoming_id", "source_file_id", name="uq_incoming_document_files_source_file_identity"),
        Index(
            "uq_incoming_document_files_main_file",
            "incoming_id",
            unique=True,
            postgresql_where=text("is_main_file"),
        ),
        Index("ix_incoming_document_files_source_file_id", "source_file_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    incoming_file_id = Column(String(64), unique=True, nullable=False, index=True)
    incoming_id = Column(
        String(64), ForeignKey("incoming_documents.incoming_id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_file_id = Column(String(512), nullable=False)
    source_url = Column(String(2048))
    filename = Column(String(512), nullable=False)
    is_main_file = Column(Boolean, default=False, nullable=False)
    content_hash = Column(String(128), index=True)
    file_size = Column(BigInteger)
    mime_type = Column(String(255))
    original_file_url = Column(String(1024), nullable=False)
    markdown_file_url = Column(String(1024))
    status = Column(String(32), default="uploaded", index=True)
    processing_error = Column(Text)
    linked_file_id = Column(String(64))
    knowledge_import_status = Column(String(32), default="none", index=True)
    knowledge_import_error = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeChunk(Base):
    """知识库 Chunk 模型"""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("chunk_id", name="uq_knowledge_chunks_chunk_id"),
        Index("ix_knowledge_chunks_file_id", "file_id"),
        Index("ix_knowledge_chunks_kb_id", "kb_id"),
        Index("ix_knowledge_chunks_graph_indexed", "graph_indexed"),
        Index("ix_knowledge_chunks_graph_structure_indexed", "graph_structure_indexed"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chunk_id = Column(String(128), nullable=False)
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    start_char_pos = Column(Integer)
    end_char_pos = Column(Integer)
    start_token_pos = Column(Integer)
    end_token_pos = Column(Integer)
    graph_structure_indexed = Column(Boolean, default=False, nullable=False)
    graph_indexed = Column(Boolean, default=False)
    graph_extraction_details = Column(
        JSON_VALUE,
        default=lambda: {"status": "pending", "attempt_count": 0},
        nullable=False,
    )
    ent_ids = Column(JSON_VALUE)
    tags = Column(JSON_VALUE)
    extraction_result = Column(JSON_VALUE)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class DocumentBusinessExtractionRun(Base):
    """文档业务结构化抽取运行记录，独立于来文和知识库核心流程。"""

    __tablename__ = "document_business_extraction_runs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_document_business_extraction_runs_run_id"),
        Index("ix_document_business_extraction_runs_scope", "document_scope"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), unique=True, nullable=False, index=True)
    document_scope = Column(String(32), nullable=False, index=True)
    incoming_id = Column(String(64), index=True)
    kb_id = Column(String(80), index=True)
    file_id = Column(String(64), index=True)
    status = Column(String(32), default="running", index=True)
    model_spec = Column(String(512))
    run_metadata = Column(JSON_VALUE)
    error = Column(Text)
    created_by = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class DocumentBusinessExtractionResult(Base):
    """文档级业务分类结果，可挂载来文或后续关联知识库文件。"""

    __tablename__ = "document_business_extraction_results"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_document_business_extraction_results_run_id"),
        Index("ix_document_business_extraction_results_scope", "document_scope"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        String(64),
        ForeignKey("document_business_extraction_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    document_scope = Column(String(32), nullable=False, index=True)
    incoming_id = Column(String(64), index=True)
    kb_id = Column(String(80), index=True)
    file_id = Column(String(64), index=True)
    categories = Column(JSON_VALUE)
    schema_ids = Column(JSON_VALUE)
    status = Column(String(32), default="draft", index=True)
    confirmed_categories = Column(JSON_VALUE)
    created_by = Column(String(64))
    confirmed_by = Column(String(64))
    confirmed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class DocumentBusinessExtractionItem(Base):
    """业务结构化条目；chunk 关联仅在后续有知识库文件时可选补齐。"""

    __tablename__ = "document_business_extraction_items"
    __table_args__ = (
        UniqueConstraint("item_id", name="uq_document_business_extraction_items_item_id"),
        Index("ix_document_business_extraction_items_result_id", "result_id"),
        Index("ix_document_business_extraction_items_chunk_id", "chunk_id"),
        Index("ix_document_business_extraction_items_type", "item_type"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(String(64), unique=True, nullable=False, index=True)
    result_id = Column(
        Integer,
        ForeignKey("document_business_extraction_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_scope = Column(String(32), nullable=False, index=True)
    incoming_id = Column(String(64), index=True)
    kb_id = Column(String(80), index=True)
    file_id = Column(String(64), index=True)
    chunk_id = Column(String(128), ForeignKey("knowledge_chunks.chunk_id", ondelete="SET NULL"))
    item_type = Column(String(64), nullable=False)
    data = Column(JSON_VALUE)
    evidence = Column(JSON_VALUE)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeGraphEntity(Base):
    """知识图谱实体"""

    __tablename__ = "knowledge_graph_entities"
    __table_args__ = (
        UniqueConstraint("entity_id", name="uq_knowledge_graph_entities_entity_id"),
        UniqueConstraint("kb_id", "normalized_name", "label", name="uq_knowledge_graph_entities_identity"),
        Index("ix_knowledge_graph_entities_kb_id", "kb_id"),
        Index("ix_knowledge_graph_entities_vector_pending", "kb_id", "vector_status", "vector_next_retry_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String(64), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    normalized_name = Column(String(512), nullable=False)
    label = Column(String(128), nullable=False)
    name = Column(String(512), nullable=False)
    attributes = Column(JSON_VALUE)
    vector_status = Column(String(16), nullable=False, default="pending")
    vector_attempt_count = Column(Integer, nullable=False, default=0)
    vector_last_error = Column(Text)
    vector_next_retry_at = Column(DateTime(timezone=True))
    vector_locked_until = Column(DateTime(timezone=True))
    vector_lock_token = Column(String(32))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeGraphEntityMention(Base):
    """知识图谱实体在 chunk 中的引用"""

    __tablename__ = "knowledge_graph_entity_mentions"
    __table_args__ = (
        UniqueConstraint("entity_id", "chunk_id", name="uq_knowledge_graph_entity_mentions_entity_chunk"),
        Index("ix_knowledge_graph_entity_mentions_kb_id", "kb_id"),
        Index("ix_knowledge_graph_entity_mentions_file_id", "file_id"),
        Index("ix_knowledge_graph_entity_mentions_chunk_id", "chunk_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String(64), ForeignKey("knowledge_graph_entities.entity_id", ondelete="CASCADE"), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    chunk_id = Column(String(128), ForeignKey("knowledge_chunks.chunk_id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)


class KnowledgeGraphTriple(Base):
    """知识图谱三元组"""

    __tablename__ = "knowledge_graph_triples"
    __table_args__ = (
        UniqueConstraint("triple_id", name="uq_knowledge_graph_triples_triple_id"),
        Index("ix_knowledge_graph_triples_kb_id", "kb_id"),
        Index("ix_knowledge_graph_triples_vector_pending", "kb_id", "vector_status", "vector_next_retry_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    triple_id = Column(String(64), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    source_entity_id = Column(
        String(64), ForeignKey("knowledge_graph_entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    target_entity_id = Column(
        String(64), ForeignKey("knowledge_graph_entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    relation_type = Column(String(256), nullable=False)
    content = Column(Text, nullable=False)
    vector_status = Column(String(16), nullable=False, default="pending")
    vector_attempt_count = Column(Integer, nullable=False, default=0)
    vector_last_error = Column(Text)
    vector_next_retry_at = Column(DateTime(timezone=True))
    vector_locked_until = Column(DateTime(timezone=True))
    vector_lock_token = Column(String(32))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeGraphTripleMention(Base):
    """知识图谱三元组在 chunk 中的引用"""

    __tablename__ = "knowledge_graph_triple_mentions"
    __table_args__ = (
        UniqueConstraint("triple_id", "chunk_id", name="uq_knowledge_graph_triple_mentions_triple_chunk"),
        Index("ix_knowledge_graph_triple_mentions_kb_id", "kb_id"),
        Index("ix_knowledge_graph_triple_mentions_file_id", "file_id"),
        Index("ix_knowledge_graph_triple_mentions_chunk_id", "chunk_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    triple_id = Column(String(64), ForeignKey("knowledge_graph_triples.triple_id", ondelete="CASCADE"), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    chunk_id = Column(String(128), ForeignKey("knowledge_chunks.chunk_id", ondelete="CASCADE"), nullable=False)
    text = Column(Text)
    extractor_type = Column(String(128))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)


class EvaluationDataset(Base):
    """评估数据集模型"""

    __tablename__ = "evaluation_datasets"
    __table_args__ = (UniqueConstraint("dataset_id", name="uq_evaluation_datasets_dataset_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(String(64), unique=True, nullable=False, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    item_count = Column(Integer, default=0)
    has_gold_chunks = Column(Boolean, default=False)
    has_gold_answers = Column(Boolean, default=False)
    build_metadata = Column(JSON_VALUE)
    created_by = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class EvaluationDatasetItem(Base):
    """评估数据集题目模型"""

    __tablename__ = "evaluation_dataset_items"
    __table_args__ = (
        UniqueConstraint("item_id", name="uq_evaluation_dataset_items_item_id"),
        UniqueConstraint("dataset_id", "item_index", name="uq_evaluation_dataset_items_dataset_index"),
        Index("ix_evaluation_dataset_items_dataset_index", "dataset_id", "item_index"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(String(64), unique=True, nullable=False, index=True)
    dataset_id = Column(
        String(64),
        ForeignKey("evaluation_datasets.dataset_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    item_index = Column(Integer, nullable=False)
    query_text = Column(Text, nullable=False)
    gold_chunk_ids = Column(JSON_VALUE)
    gold_answer = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)


class EvaluationRun(Base):
    """评估运行模型"""

    __tablename__ = "evaluation_runs"
    __table_args__ = (UniqueConstraint("run_id", name="uq_evaluation_runs_run_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    dataset_id = Column(
        String(64),
        ForeignKey("evaluation_datasets.dataset_id", ondelete="SET NULL"),
        index=True,
    )
    status = Column(String(32), default="running", index=True)
    retrieval_config = Column(JSON_VALUE)
    metrics = Column(JSON_VALUE)
    overall_score = Column(Float)
    total_items = Column(Integer, default=0)
    completed_items = Column(Integer, default=0)
    started_at = Column(DateTime(timezone=True), default=utc_now_naive, index=True)
    completed_at = Column(DateTime(timezone=True))
    created_by = Column(String(64))


class EvaluationRunItem(Base):
    """评估逐题结果模型"""

    __tablename__ = "evaluation_run_items"
    __table_args__ = (
        UniqueConstraint("run_id", "item_index", name="uq_evaluation_run_items_run_index"),
        Index("ix_evaluation_run_items_run_index", "run_id", "item_index"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        String(64),
        ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset_item_id = Column(
        String(64), ForeignKey("evaluation_dataset_items.item_id", ondelete="SET NULL"), index=True
    )
    item_index = Column(Integer, nullable=False)
    query_text = Column(Text, nullable=False)
    gold_chunk_ids = Column(JSON_VALUE)
    gold_answer = Column(Text)
    generated_answer = Column(Text)
    retrieved_chunks = Column(JSON_VALUE)
    metrics = Column(JSON_VALUE)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
