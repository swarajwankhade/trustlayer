from fastapi import FastAPI

from app.api.routes import router, v1_router
from app.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name)
app.include_router(router)
app.include_router(v1_router)
