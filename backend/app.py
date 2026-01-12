from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import config
from .polling import ha_poll_loop


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    data_dir = Path(config.data_path)
    (data_dir / "floorplans").mkdir(parents=True, exist_ok=True)
    (data_dir / "frames").mkdir(parents=True, exist_ok=True)
    Path(config.timelapse_output_path).mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=ha_poll_loop, daemon=True)
    thread.start()


app.include_router(router)
app.mount("/editor", StaticFiles(directory=Path(__file__).parents[1] / "frontend", html=True), name="frontend")
