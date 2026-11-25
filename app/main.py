"""FastAPI application entrypoint for AI-Powered Multi-Platform Prospect Discovery API."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import get_settings
from app.core.exceptions import (
    ProspectDiscoveryException,
    QueryValidationError,
    SSRFSecurityViolation,
)
from app.core.security import verify_api_key
from app.platforms.search_engine import log_configured_providers
from app.schemas import ProspectResponse, QueryRequest
from app.services.prospect_service import ProspectDiscoveryService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("prospect.api")

settings = get_settings()

# Initialize Rate Limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager."""
    logger.info(f"Starting {settings.APP_NAME} in {settings.APP_ENV} mode...")
    log_configured_providers(settings)
    yield
    logger.info("Shutting down Prospect Discovery API gracefully...")


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=(
        "Production REST API for AI-powered prospect discovery across Google, LinkedIn, X, "
        "YouTube, Instagram, WhatsApp, Facebook, Email, and Threads."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Attach rate limiter to FastAPI state & exception handler
app.state.limiter = limiter
# slowapi's _rate_limit_exceeded_handler signature differs from Starlette's ExceptionHandler protocol
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

# Restrictive CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.DEBUG else ["http://localhost:4000", "http://127.0.0.1:4000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# Global Request Body Size Limiter Middleware
@app.middleware("http")
async def limit_request_size_middleware(request: Request, call_next: Any) -> Response:
    """Enforce request body size limits to prevent Denial-of-Service memory exhaustion."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.MAX_REQUEST_SIZE_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"detail": "Request payload exceeds maximum allowed size."},
                )
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Invalid Content-Length header provided."},
            )
    response: Response = await call_next(request)
    return response


# Exception Handlers
@app.exception_handler(QueryValidationError)
async def query_validation_exception_handler(
    request: Request, exc: QueryValidationError
) -> JSONResponse:
    """Handle domain query validation errors."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message},
    )


@app.exception_handler(SSRFSecurityViolation)
async def ssrf_security_exception_handler(
    request: Request, exc: SSRFSecurityViolation
) -> JSONResponse:
    """Handle blocked private or loopback IP SSRF attempts."""
    logger.warning(f"Blocked SSRF attempt: {exc.url}")
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"detail": "Access to internal or prohibited network target is forbidden."},
    )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle standard FastAPI/Pydantic request payload validation errors."""
    first_error = exc.errors()[0] if exc.errors() else {}
    msg = first_error.get("msg", "Invalid request body")
    return JSONResponse(
        status_code=422,
        content={"detail": f"Validation error: {msg}"},
    )


@app.exception_handler(ProspectDiscoveryException)
async def prospect_domain_exception_handler(
    request: Request, exc: ProspectDiscoveryException
) -> JSONResponse:
    """Handle generic domain exceptions cleanly without leaking internal traces."""
    logger.error(f"Prospect discovery domain error: {exc.message}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message},
    )


@app.exception_handler(Exception)
async def generic_unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all unhandled exception handler ensuring no internal stack traces leak."""
    logger.error(f"Unhandled server error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An internal server error occurred while processing the prospect query."
        },
    )


# Health Check & Root Endpoints
@app.get("/", tags=["Health"])
async def root_status() -> dict[str, str]:
    """Basic health check and service discovery metadata."""
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "docs_url": "/docs",
        "endpoint": "/prospect-discovery",
    }


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    """Standard health endpoint for load balancers and deployment monitors."""
    return {"status": "ok"}


# Prospect Discovery Primary Endpoint
@app.post(
    "/prospect-discovery",
    response_model=ProspectResponse,
    status_code=status.HTTP_200_OK,
    tags=["Prospect Discovery"],
    summary="Discover relevant prospects across 9 platforms",
    description=(
        "Accepts a natural language query, parses intent via LLM, discovers prospects concurrently "
        "across all supported platforms, consolidates multiple profiles into single records, "
        "and returns structured JSON."
    ),
)
@limiter.limit(f"{settings.RATE_LIMIT_PER_MINUTE}/minute")
async def discover_prospects(
    request: Request,
    payload: QueryRequest,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Response:
    """Primary endpoint implementing the assignment requirements."""
    # Optional API key authentication
    if settings.API_KEY and not verify_api_key(x_api_key, settings.API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header.",
        )

    service = ProspectDiscoveryService()
    try:
        response = await service.discover_prospects(payload.query)
        return Response(
            content=response.model_dump_json(),
            media_type="application/json; charset=utf-8",
        )
    except ProspectDiscoveryException:
        raise
    except Exception as e:
        logger.error(f"Error executing prospect discovery: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to discover prospects due to an internal processing error.",
        )
