"""Compatibility entry point for the Qwen Image 2.1 Studio."""
from studio_service import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("studio_app:app", host="127.0.0.1", port=7860, reload=False)
