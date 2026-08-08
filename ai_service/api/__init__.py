# -*- coding: utf-8 -*-
"""
ai_service API模块

提供基于FastAPI的Web服务接口。
"""

from ai_service.api.server import AIServiceServer, create_app

__all__ = [
    "AIServiceServer",
    "create_app",
]
