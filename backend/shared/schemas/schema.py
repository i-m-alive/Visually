from pydantic import BaseModel
from typing import Optional, List, Any


class ColumnStats(BaseModel):
    min: Optional[Any] = None
    max: Optional[Any] = None
    avg: Optional[float] = None
    distinct_count: Optional[int] = None
    top_values: Optional[List[Any]] = None
    null_count: Optional[int] = None


class ColumnMeta(BaseModel):
    name: str
    type: str
    is_nullable: bool
    is_primary_key: bool
    description: str
    stats: Optional[ColumnStats] = None


class RelationshipMeta(BaseModel):
    column: str
    references: str  # "table.column"
    cardinality: str  # "many-to-one" | "one-to-many" | "one-to-one"
    inferred: bool


class TableMeta(BaseModel):
    name: str
    schema: str
    row_count: int
    importance_rank: int
    description: str
    columns: List[ColumnMeta]
    relationships: List[RelationshipMeta] = []


class SemanticSchemaDocument(BaseModel):
    connection_id: str
    crawled_at: str
    tables: List[TableMeta]
    important_tables: List[str]
    total_tables: int
    version: int
