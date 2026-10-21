import asyncio
import time
from collections import OrderedDict
from contextlib import asynccontextmanager, suppress
from importlib.resources import files
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from techfi_shared.contracts import CallOutcome
from techfi_shared.errors import ApplicationError, ProviderError
from techfi_shared.logging import Event, configure_logging

from techfi_voice.collection import DemoCallingPolicy, DemoCase
from techfi_voice.config import VoiceSettings
from techfi_voice.integrations.providers import (
    create_services,
    playground_settings,
    validate_provider_settings,
)
from techfi_voice.runtime import run_session
from techfi_voice.session import build_session


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sdp: str = Field(min_length=1, max_length=65536)
    type: Literal["offer"]
    voice_provider: Literal["sarvam", "google", "sarvam-v4-flash"] = "sarvam"
    llm_provider: Literal["openai", "sarvam"] = "openai"
    persona: Literal["male", "female"] = "male"
    agent_type: Literal["general", "soft-collection"] = "general"
    collection: DemoCase | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def agent_case(self):
        if (self.agent_type == "soft-collection") != (self.collection is not None):
            raise ValueError("collection demo fields required only for collection agents")
        return self


def create_app(
    settings: VoiceSettings | None = None,
    *,
    service_factory=create_services,
    connection_factory=None,
    session_builder=build_session,
    session_runner=run_session,
    collection_policy=None,
) -> FastAPI:
    # Suppress dependency logs before any Pipecat or SDK import.
    from loguru import logger

    logger.remove()
    safe_logger = configure_logging()
    settings = settings if settings is not None else VoiceSettings()
    validate_provider_settings(settings)
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    connection_factory = connection_factory or SmallWebRTCConnection
    collection_policy = collection_policy or DemoCallingPolicy(
        settings.collection_demo_enabled,
        settings.collection_start_hour,
        settings.collection_end_hour,
        settings.collection_max_attempts_per_day,
        settings.collection_retry_gap_seconds,
    )
    results = OrderedDict()  # Bounded, short-lived local demo outcomes; no transcripts or names.
    connections = {}
    sessions = {}
    jobs: set[asyncio.Task] = set()
    reservation = asyncio.Lock()
    active = 0

    async def close_connection(connection):
        with suppress(Exception):
            async with asyncio.timeout(settings.cleanup_timeout_seconds):
                await connection.disconnect()

    @asynccontextmanager
    async def lifespan(app):
        yield
        for session in list(sessions.values()):
            session.outcome = CallOutcome.CANCELLED
            session.stop.set()
        await asyncio.gather(*(close_connection(c) for c in list(connections.values())))
        if jobs:
            _, pending = await asyncio.wait(jobs, timeout=settings.cleanup_timeout_seconds)
            for job in pending:
                job.cancel()
            if pending:
                await asyncio.wait(pending, timeout=settings.cleanup_timeout_seconds)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def local_request(request: Request, call_next):
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if request.method != "GET" and (
            origin != expected or request.headers.get("x-techfi-client") != "local-browser"
        ):
            return JSONResponse(status_code=403, content={"error": "local_origin_required"})
        # SDP and errors can reveal network details; never cache responses or log requests.
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; connect-src 'self'; "
            "media-src 'self' blob:; style-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(ApplicationError)
    async def known_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.code})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse(status_code=422, content={"error": "invalid_request"})

    @app.exception_handler(Exception)
    async def unknown_error(request, exc):
        return JSONResponse(status_code=503, content={"error": "session_unavailable"})

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(files("techfi_voice").joinpath("static/index.html").read_text())

    @app.get("/client.js")
    async def client_js():
        return Response(
            files("techfi_voice").joinpath("static/client.js").read_text(),
            media_type="text/javascript",
        )

    @app.get("/styles.css")
    async def styles():
        return Response(
            files("techfi_voice").joinpath("static/styles.css").read_text(),
            media_type="text/css",
        )

    @app.get("/health/live")
    async def health():
        return {"status": "ok"}

    @app.post("/api/offer")
    async def offer(body: Offer):
        nonlocal active
        # Resolve before opening a peer or reserving a slot; no provider clients here.
        try:
            values = settings.model_dump()
            values.update(agent_type=body.agent_type, collection_case=body.collection)
            base = VoiceSettings(
                _env_file=None,
                **values,
                SARVAM_API_KEY=settings.sarvam_api_key,
                OPENAI_API_KEY=settings.openai_api_key,
                google_application_credentials=settings.google_application_credentials,
            )
            session_settings = playground_settings(
                base, body.voice_provider, body.llm_provider, body.persona
            )
        except Exception:
            raise ProviderError() from None
        async with reservation:
            if active >= settings.max_sessions:
                return JSONResponse(status_code=429, content={"error": "session_limit"})
            if body.agent_type == "soft-collection":
                collection_policy.admit(settings.tenant_id)
                session_settings = session_settings.model_copy(
                    update={
                        "max_session_seconds": min(
                            settings.max_session_seconds, collection_policy.remaining_seconds()
                        )
                    }
                )
            active += 1
        connection = None
        transferred = False
        try:
            connection = connection_factory(ice_servers=[])
            async with asyncio.timeout(settings.connection_timeout_seconds):
                await connection.initialize(sdp=body.sdp, type=body.type)
            transport = SmallWebRTCTransport(
                connection,
                TransportParams(audio_in_enabled=True, audio_out_enabled=True),
            )
            session = session_builder(
                session_settings,
                transport,
                service_factory,
                **(
                    {"collection_policy": collection_policy}
                    if body.agent_type == "soft-collection"
                    else {}
                ),
            )
            connections[connection.pc_id] = connection
            sessions[connection.pc_id] = session
            greeted = False

            @transport.event_handler("on_client_connected")
            async def connected(transport, client):
                nonlocal greeted
                if not greeted:
                    if (
                        body.agent_type == "soft-collection"
                        and not collection_policy.within_hours()
                    ):
                        session.stop.set()
                        return
                    from pipecat.frames.frames import TTSSpeakFrame

                    greeted = True
                    await session.task.queue_frame(
                        TTSSpeakFrame(
                            text=getattr(session, "greeting", None) or session_settings.greeting
                        )
                    )

            @transport.event_handler("on_client_disconnected")
            async def disconnected(transport, client):
                if session.outcome == CallOutcome.COMPLETED:
                    session.outcome = CallOutcome.CANCELLED
                session.stop.set()

            async def own_session():
                nonlocal active
                try:
                    await session_runner(session, session_settings)
                except Exception:
                    safe_logger.error(Event.WORKER_FAILED)
                finally:
                    if getattr(session, "collection", None):
                        result = session.result().model_dump(mode="json")
                        results[connection.pc_id] = (time.monotonic(), result)
                        while len(results) > 16:
                            results.popitem(last=False)
                    if body.agent_type == "soft-collection":
                        collection_policy.release(
                            settings.tenant_id,
                            suppress=bool(getattr(session, "collection", None))
                            and (
                                session.collection.result().do_not_call_request
                                or session.collection.result().call_outcome == "wrong_recipient"
                            ),
                        )
                    await close_connection(connection)
                    connections.pop(connection.pc_id, None)
                    sessions.pop(connection.pc_id, None)
                    async with reservation:
                        active -= 1

            answer = connection.get_answer()
            if answer is None:
                raise ProviderError()
            job = asyncio.create_task(own_session())
            jobs.add(job)
            job.add_done_callback(jobs.discard)
            transferred = True
            return answer
        except Exception:
            raise ProviderError() from None
        finally:
            if not transferred:
                if body.agent_type == "soft-collection":
                    collection_policy.release(settings.tenant_id)
                if connection:
                    connections.pop(connection.pc_id, None)
                    sessions.pop(connection.pc_id, None)
                    await close_connection(connection)
                async with reservation:
                    active -= 1

    @app.post("/api/sessions/{pc_id}/result")
    async def result(pc_id: str):
        # Same-origin POST guard applies. Only opaque IDs, not names, select a result.
        for key, (created, _) in list(results.items()):
            if time.monotonic() - created > 600:
                results.pop(key, None)
        value = results.get(pc_id)
        if value:
            return value[1]
        return JSONResponse(
            status_code=202 if pc_id in sessions else 404,
            content={"status": "pending" if pc_id in sessions else "unavailable"},
        )

    @app.post("/api/sessions/{pc_id}/stop", status_code=204)
    async def stop(pc_id: str):
        session = sessions.get(pc_id)
        if session:
            if session.outcome == CallOutcome.COMPLETED:
                session.outcome = CallOutcome.CANCELLED
            session.stop.set()
        return Response(status_code=204)

    return app
