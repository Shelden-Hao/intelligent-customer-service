# -*- coding: utf-8 -*-
"""
LLM微调模块

提供微调数据生成和句子改写功能。
"""

from ai_service.training.finetune.data_generator import (
    FinetuneDataGenerator,
    FinetuneConfig,
    FinetuneExample,
)
from ai_service.training.finetune.paraphraser import (
    Paraphraser,
    ParaphraserConfig,
)

__all__ = [
    "FinetuneDataGenerator",
    "FinetuneConfig",
    "FinetuneExample",
    "Paraphraser",
    "ParaphraserConfig",
]
