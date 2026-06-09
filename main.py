"""
Application entrypoint.
Usage: python main.py
Starts the FastAPI server — reads PORT from env for cloud deployment.
"""
import os
import uvicorn
from config import settings

if __name__ == "__main__":
    port = int(os.environ.get("PORT", settings.api_port))
    uvicorn.run(
        "api.app:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )
