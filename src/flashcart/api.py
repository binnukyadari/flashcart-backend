import logging
import secrets
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from flashcart.bootstrap import checkout
from flashcart.config import Settings
from flashcart.models import Conflict, NotFound, Order, OrderRequest, Product
from flashcart.service import Checkout

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, service: Checkout | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.checkout = service or checkout(settings)
        try:
            yield
        finally:
            app.state.checkout.inventory.close()
            app.state.checkout.orders.close()

    app = FastAPI(title="FlashCart API", version="0.1.0", lifespan=lifespan)

    def authorize(x_api_key: Annotated[str | None, Header()] = None):
        if settings.api_key and not secrets.compare_digest(
            x_api_key or "", settings.api_key.get_secret_value()
        ):
            raise HTTPException(401, "Invalid API key")

    protected = [Depends(authorize)]

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(NotFound)
    async def missing_handler(request, exc):
        return JSONResponse(status_code=404, content={"detail": "Order not found"})

    @app.exception_handler(Exception)
    async def unavailable_handler(request, exc):
        # Avoid exposing SDK error messages, credentials, headers or request bodies.
        logger.error("Dependency operation failed (%s)", type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable"})

    @app.get("/health/live")
    def live():
        return {"status": "ok", "mode": settings.mode}

    @app.get("/health/ready", dependencies=protected)
    def ready():
        app.state.checkout.inventory.ping()
        app.state.checkout.orders.ping()
        return {"status": "ready", "mode": settings.mode}

    @app.get("/v1/products", response_model=list[Product], dependencies=protected)
    def products(limit: Annotated[int, Query(ge=1, le=100)] = 50):
        return app.state.checkout.inventory.products(limit)

    @app.post(
        "/v1/orders",
        response_model=Order,
        status_code=201,
        dependencies=protected,
        responses={
            200: {"model": Order},
            202: {"model": Order},
            409: {"description": "Stock rejected (Order) or idempotency conflict (detail)"},
        },
    )
    def submit(
        body: OrderRequest,
        response: Response,
        idempotency_key: Annotated[
            str, Header(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
        ],
    ):
        svc = app.state.checkout
        order, created = svc.orders.create(idempotency_key, body)
        try:
            order = svc.reconcile(order)
        except Exception as exc:
            logger.warning("Order %s pending reconciliation (%s)", order.id, type(exc).__name__)
            # Firestore already holds intent. Retrying the same key or the worker resumes it.
            response.status_code = 202
            response.headers["Retry-After"] = "5"
            response.headers["Location"] = f"/v1/orders/{order.id}"
            return order
        response.status_code = 409 if order.status == "rejected" else (201 if created else 200)
        response.headers["Location"] = f"/v1/orders/{order.id}"
        return order

    @app.get("/v1/orders/{order_id}", response_model=Order, dependencies=protected)
    def get_order(order_id: UUID):
        return app.state.checkout.orders.get(str(order_id))

    return app
