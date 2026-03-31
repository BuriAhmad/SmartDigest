"""SmartDigest — FastAPI application factory."""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import PlainTextResponse, RedirectResponse

from app.config import get_settings
from app.database import get_db
from app.middleware.auth import SessionAuthMiddleware
from app.middleware.rate_limit import limiter


# Templates directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def configure_logging() -> None:
    """Set up structlog — JSON in prod, pretty console in dev."""
    settings = get_settings()
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if settings.is_production:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle."""
    logger = structlog.get_logger()
    settings = get_settings()

    configure_logging()
    logger.info("app.starting", env=settings.ENV)

    yield

    logger.info("app.shutdown")


def create_app() -> FastAPI:
    """Application factory — creates and configures the FastAPI app."""
    configure_logging()

    application = FastAPI(
        title="SmartDigest",
        description="Async content pipeline with delivery tracking",
        version="0.1.0",
        lifespan=lifespan,
    )

    # --- Middleware ---
    application.add_middleware(SessionAuthMiddleware)

    # --- Rate Limiting ---
    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # --- API Routers ---
    from app.api.auth import router as auth_router
    from app.api.sources import router as sources_router
    from app.api.briefings import router as briefings_router
    from app.api.digests import router as digests_router
    from app.api.jobs import router as jobs_router
    from app.api.metrics import router as metrics_router

    application.include_router(auth_router)
    application.include_router(sources_router, prefix="/api/v1")
    application.include_router(briefings_router, prefix="/api/v1")
    application.include_router(digests_router, prefix="/api/v1")
    application.include_router(jobs_router, prefix="/api/v1")
    application.include_router(metrics_router, prefix="/api/v1")

    # --- HTML Routes ---

    @application.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        """Login / register page."""
        # If already logged in, redirect to dashboard
        if hasattr(request.state, "user_id"):
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("login.html", {
            "request": request,
            "active_page": "login",
        })

    @application.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
        """Main dashboard — shows briefings, digests, pipeline health."""
        from app.models.briefing import Briefing
        from app.models.digest import Digest
        from app.models.curated_source import CuratedSource

        user_id = request.state.user_id
        user_email = request.state.user_email

        # Load user's active briefings
        briefings_result = await db.execute(
            select(Briefing)
            .where(
                Briefing.user_id == user_id,
                Briefing.active.is_(True),
            )
            .order_by(Briefing.created_at.desc())
        )
        briefings = briefings_result.scalars().all()

        # Load recent digests (last 10) with topic and item count
        from app.models.digest_item import DigestItem

        digests_result = await db.execute(
            select(
                Digest.id,
                Digest.briefing_id,
                Digest.status,
                Digest.created_at,
                Digest.delivered_at,
                Briefing.topic,
                sqlfunc.count(DigestItem.id).label("item_count"),
            )
            .join(Briefing, Digest.briefing_id == Briefing.id)
            .outerjoin(DigestItem, DigestItem.digest_id == Digest.id)
            .where(Briefing.user_id == user_id)
            .group_by(
                Digest.id,
                Digest.briefing_id,
                Digest.status,
                Digest.created_at,
                Digest.delivered_at,
                Briefing.topic,
            )
            .order_by(Digest.created_at.desc())
            .limit(10)
        )
        digests_raw = digests_result.all()

        # Convert to dicts for template
        digests = []
        for row in digests_raw:
            digests.append({
                "id": row[0],
                "briefing_id": row[1],
                "status": row[2],
                "created_at": row[3],
                "delivered_at": row[4],
                "topic": row[5],
                "item_count": row[6],
            })

        # Load curated sources for the modal
        sources_result = await db.execute(
            select(CuratedSource)
            .where(CuratedSource.active.is_(True))
            .order_by(CuratedSource.name)
        )
        sources = sources_result.scalars().all()

        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "user_email": user_email,
            "briefings": briefings,
            "digests": digests,
            "sources": sources,
            "active_page": "dashboard",
        })

    @application.get("/dashboard/metrics", response_class=HTMLResponse)
    async def dashboard_metrics(request: Request):
        """HTMX-polled pipeline health metrics partial."""
        from app.services.metrics import get_pipeline_metrics as get_pm
        from app.database import async_session

        metrics = {
            "total_jobs": 0,
            "by_status": {"done": 0, "failed": 0},
            "stage_avg_ms": {"fetch": 0, "summarise": 0, "deliver": 0},
            "last_error": None,
        }

        try:
            async with async_session() as session:
                metrics = await get_pm(session, period_hours=24)
        except Exception:
            pass

        return templates.TemplateResponse("partials/metrics_panel.html", {
            "request": request,
            "metrics": metrics,
        })

    @application.get("/digests", response_class=HTMLResponse)
    async def digests_list_page(request: Request, db: AsyncSession = Depends(get_db)):
        """Digests list page — shows all user digests."""
        from app.models.digest import Digest
        from app.models.digest_item import DigestItem
        from app.models.briefing import Briefing

        user_id = request.state.user_id
        user_email = request.state.user_email

        digests_result = await db.execute(
            select(
                Digest.id,
                Digest.briefing_id,
                Digest.status,
                Digest.created_at,
                Digest.delivered_at,
                Briefing.topic,
                sqlfunc.count(DigestItem.id).label("item_count"),
            )
            .join(Briefing, Digest.briefing_id == Briefing.id)
            .outerjoin(DigestItem, DigestItem.digest_id == Digest.id)
            .where(Briefing.user_id == user_id)
            .group_by(
                Digest.id,
                Digest.briefing_id,
                Digest.status,
                Digest.created_at,
                Digest.delivered_at,
                Briefing.topic,
            )
            .order_by(Digest.created_at.desc())
        )
        digests_raw = digests_result.all()

        digests = []
        for row in digests_raw:
            digests.append({
                "id": row[0],
                "briefing_id": row[1],
                "status": row[2],
                "created_at": row[3],
                "delivered_at": row[4],
                "topic": row[5],
                "item_count": row[6],
            })

        return templates.TemplateResponse("digests.html", {
            "request": request,
            "user_email": user_email,
            "digests": digests,
            "active_page": "digests",
        })

    @application.get("/digests/{digest_id}", response_class=HTMLResponse)
    async def digest_detail_page(
        digest_id: int,
        request: Request,
        db: AsyncSession = Depends(get_db),
    ):
        """Digest detail page — only accessible to the owning user."""
        from app.models.digest import Digest
        from app.models.digest_item import DigestItem
        from app.models.briefing import Briefing

        user_id = request.state.user_id

        # Enforce ownership: join through briefing
        result = await db.execute(
            select(Digest)
            .join(Briefing, Digest.briefing_id == Briefing.id)
            .where(
                Digest.id == digest_id,
                Briefing.user_id == user_id,
            )
        )
        digest = result.scalar_one_or_none()
        if digest is None:
            return HTMLResponse(
                """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Not Found — SmartDigest</title>
                <style>body{font-family:'Inter',system-ui,sans-serif;background:hsl(220 20% 6%);color:hsl(210 20% 92%);min-height:100vh;display:flex;align-items:center;justify-content:center;margin:0;}
                .c{text-align:center;} .e{font-size:48px;margin-bottom:16px;} h1{font-size:20px;font-weight:700;margin-bottom:8px;}
                p{color:hsl(215 12% 50%);font-size:14px;margin-bottom:24px;} a{color:hsl(172 66% 50%);font-size:14px;font-weight:500;text-decoration:none;}a:hover{text-decoration:underline;}</style></head>
                <body><div class="c"><div class="e">🔍</div>
                <h1>Digest not found</h1>
                <p>This digest doesn't exist or you don't have access to it.</p>
                <a href="/">← Back to Dashboard</a></div></body></html>""",
                status_code=404,
            )

        # Get briefing topic
        briefing_result = await db.execute(
            select(Briefing.topic).where(Briefing.id == digest.briefing_id)
        )
        topic_row = briefing_result.first()
        digest.topic = topic_row[0] if topic_row else "Unknown"

        # Get items
        items_result = await db.execute(
            select(DigestItem).where(DigestItem.digest_id == digest_id)
        )
        items = items_result.scalars().all()

        return templates.TemplateResponse("digest_detail.html", {
            "request": request,
            "user_email": getattr(request.state, "user_email", ""),
            "digest": digest,
            "items": items,
            "active_page": "digests",
        })

    @application.get("/metrics", response_class=HTMLResponse)
    async def metrics_page(request: Request):
        """Full pipeline metrics page."""
        return templates.TemplateResponse("metrics.html", {
            "request": request,
            "user_email": request.state.user_email,
            "active_page": "metrics",
        })

    @application.get("/metrics/content", response_class=HTMLResponse)
    async def metrics_content(request: Request):
        """HTMX-polled full metrics content partial."""
        from app.services.metrics import get_pipeline_metrics as get_pm
        from app.services.metrics import get_usage_metrics as get_um
        from app.database import async_session

        metrics = {
            "total_jobs": 0,
            "by_status": {"done": 0, "failed": 0},
            "stage_avg_ms": {"fetch": 0, "summarise": 0, "deliver": 0},
            "last_error": None,
        }
        usage = {
            "user_email": getattr(request.state, "user_email", "—"),
            "briefing_count": 0,
            "digest_count": 0,
        }

        try:
            async with async_session() as session:
                metrics = await get_pm(session, period_hours=24)
                usage = await get_um(session, user_id=request.state.user_id)
        except Exception:
            pass

        return templates.TemplateResponse("partials/metrics_full.html", {
            "request": request,
            "metrics": metrics,
            "usage": usage,
        })

    return application


app = create_app()
