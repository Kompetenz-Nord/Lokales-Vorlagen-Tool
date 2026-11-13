"""Extraktion: Normalisierung, Label↔Wert über Wortpositionen, Feldregeln, Zusammenführung."""

from .extractor import ExtractionResult, SourceDocInfo, extract_candidates
from .merge import MergeOutcome, group_documents, merge_candidates

__all__ = [
    "ExtractionResult",
    "MergeOutcome",
    "SourceDocInfo",
    "extract_candidates",
    "group_documents",
    "merge_candidates",
]
