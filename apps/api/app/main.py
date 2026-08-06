from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.routes import router
from .database import Base, engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="PacePM API", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
