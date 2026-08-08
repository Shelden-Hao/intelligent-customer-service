# -*- coding: utf-8 -*-
"""
训练模块

提供模型训练和打包功能。
"""

from ai_service.training.trainer import Trainer, TrainerConfig
from ai_service.training.model_storage import (
    ModelMetadata,
    create_model_package,
    extract_model_archive,
    get_latest_model,
    get_model_path,
    load_metadata_from_archive,
)
from ai_service.training.finetune import (
    FinetuneDataGenerator,
    FinetuneConfig,
    FinetuneExample,
    Paraphraser,
    ParaphraserConfig,
)

__all__ = [
    "Trainer",
    "TrainerConfig",
    # Model Storage
    "ModelMetadata",
    "create_model_package",
    "extract_model_archive",
    "get_latest_model",
    "get_model_path",
    "load_metadata_from_archive",
    # Finetune
    "FinetuneDataGenerator",
    "FinetuneConfig",
    "FinetuneExample",
    "Paraphraser",
    "ParaphraserConfig",
]
