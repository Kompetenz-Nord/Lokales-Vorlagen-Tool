"""Optionale lokale KI (Ollama, nur Loopback): Client, Status, VLM-Extraktion, Abgleich, Benchmark.

Die App funktioniert vollständig ohne dieses Modul. Es ist standardmäßig
ausgeschaltet (``Settings.ai_enabled = False``).
"""

from __future__ import annotations

from .benchmark import FieldScore, compare_methods, score_extraction
from .ollama_client import (
    ConfirmationRequiredError,
    ConsentRequiredError,
    InvalidModelNameError,
    InvalidModelResponseError,
    ModelInfo,
    ModelNotFoundError,
    OllamaClient,
    OllamaError,
    OllamaRequestError,
    OllamaTimeoutError,
    OllamaUnavailableError,
    PullCancelledError,
    validate_model_name,
)
from .reconcile import reconcile
from .status import (
    MODEL_CATALOG,
    HardwareInfo,
    LocalAiState,
    LocalAiStatus,
    ModelCatalogEntry,
    check_model_requirements,
    detect_status,
    hardware_info,
)
from .vlm_extractor import SYSTEM_PROMPT, VlmExtractor, VlmResult, build_extraction_schema

__all__ = [
    "MODEL_CATALOG",
    "SYSTEM_PROMPT",
    "ConfirmationRequiredError",
    "ConsentRequiredError",
    "FieldScore",
    "HardwareInfo",
    "InvalidModelNameError",
    "InvalidModelResponseError",
    "LocalAiState",
    "LocalAiStatus",
    "ModelCatalogEntry",
    "ModelInfo",
    "ModelNotFoundError",
    "OllamaClient",
    "OllamaError",
    "OllamaRequestError",
    "OllamaTimeoutError",
    "OllamaUnavailableError",
    "PullCancelledError",
    "VlmExtractor",
    "VlmResult",
    "build_extraction_schema",
    "check_model_requirements",
    "compare_methods",
    "detect_status",
    "hardware_info",
    "reconcile",
    "score_extraction",
    "validate_model_name",
]
