import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from routers import devices, events, health
from routers.chat import router as chat_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [API] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(
    title="Telemetry Platform API",
    description="REST API for the IoT telemetry monitoring and anomaly detection platform.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(health.router)
app.include_router(devices.router, prefix="/api")
app.include_router(events.router,  prefix="/api")
app.include_router(chat_router,    prefix="/api")

_static = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="dashboard")
