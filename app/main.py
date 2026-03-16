"""
API Sentinel - Main Application
A system for API discovery, inventory, and monitoring for small/medium enterprises
"""
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import logging
from pathlib import Path
from app.database import db

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('api_sentinel.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="API Sentinel",
    description="API Discovery, Inventory and Monitoring System",
    version="1.0.0"
)

# Set up static files and templates
BASE_DIR = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Import and include routers
from app.routers import inventory, discovery, monitoring, alerts, logs
app.include_router(inventory.router)
app.include_router(discovery.router)
app.include_router(monitoring.router)
app.include_router(alerts.router)
app.include_router(logs.router)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Main dashboard page"""
    logger.info("Dashboard accessed")
    
    stats = db.get_dashboard_stats()
    endpoints = db.get_all_endpoints(active_only=False)
    monitoring_stats = db.get_monitoring_stats()
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "title": "API Sentinel Dashboard",
        "stats": stats,
        "endpoints": endpoints,
        "monitoring_stats": monitoring_stats
    })


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "API Sentinel"}


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting API Sentinel...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
