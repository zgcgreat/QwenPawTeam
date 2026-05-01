import os
from dotenv import load_dotenv
import uvicorn

# Load environment variables from .env file
load_dotenv()

if __name__ == "__main__":
    uvicorn.run(
        "qwenpaw.app._app:app",
        host="127.0.0.1",
        port=8088,
        reload=False,
        log_level="info",
    )
