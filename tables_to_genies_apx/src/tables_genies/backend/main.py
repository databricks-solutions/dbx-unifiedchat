"""
FastAPI application entry point.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .router import api

app = FastAPI(
    title="Tables to Genies API",
    description="API for creating Genie rooms from UC tables",
    version="1.0.0"
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(api)

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}
