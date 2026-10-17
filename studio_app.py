import os
import uvicorn
from studio_service import app

if __name__ == "__main__":
    host = os.getenv("STUDIO_HOST", "0.0.0.0")
    port = int(os.getenv("STUDIO_PORT", "7860"))
    uvicorn.run("studio_app:app", host=host, port=port, reload=False)
