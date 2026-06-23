from __future__ import annotations

import os

import uvicorn

from api import app


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
