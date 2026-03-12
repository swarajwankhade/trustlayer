from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.routes import router, v1_router
from app.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name)


@app.middleware("http")
async def add_version_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-TrustLayer-Version"] = settings.service_version
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, str):
        content = {"detail": exc.detail}
    elif isinstance(exc.detail, dict):
        content = exc.detail
    else:
        content = {"detail": exc.detail}
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)


app.include_router(router)
app.include_router(v1_router)
