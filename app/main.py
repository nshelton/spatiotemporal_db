from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import close_pool, init_pool
from app.routes import entity, query


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize and cleanup resources."""
    # Startup
    await init_pool()
    yield
    # Shutdown
    await close_pool()


app = FastAPI(
    title="Daruma - Personal Timeline API",
    description="Store and query entities with time spans and locations",
    version="0.1.0",
    lifespan=lifespan,
)

# Include routers
app.include_router(entity.router)
app.include_router(query.router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    from app.config import settings

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
