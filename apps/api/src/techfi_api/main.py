from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from techfi_shared.errors import ApplicationError
from techfi_shared.logging import Event, configure_logging

from techfi_api.config import APISettings


def create_app(settings: APISettings | None = None) -> FastAPI:
    app = FastAPI(title="TechFi Voice API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings if settings is not None else APISettings()
    logger = configure_logging()

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, exc: ApplicationError) -> JSONResponse:
        logger.error(Event.API_ERROR)
        return JSONResponse(status_code=exc.status_code, content={"error": exc.code})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "invalid_request"})

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error(Event.API_ERROR)
        return JSONResponse(status_code=500, content={"error": "internal_error"})

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    return app
