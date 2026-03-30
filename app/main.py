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
import socket
import time
from app.database import db

try:
    import psutil
except Exception:
    psutil = None

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

# Track application start time for uptime calculation
APP_START_TIME = time.time()

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


@app.on_event("startup")
async def on_startup():
    """Start background tasks on application startup"""
    await monitoring.start_auto_monitoring()


@app.on_event("shutdown")
async def on_shutdown():
    """Stop background tasks on application shutdown"""
    await monitoring.stop_auto_monitoring()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Main dashboard page"""
    logger.info("Dashboard accessed")
    
    stats = db.get_dashboard_stats()
    endpoints = db.get_all_endpoints(active_only=False)
    monitoring_stats = db.get_monitoring_stats()

    # Fill the dashboard "Last Checked" column from recent monitoring data.
    for endpoint in endpoints:
        config = db.get_monitoring_config(endpoint['id'])
        endpoint['last_checked'] = config['last_check'] if config and config.get('last_check') else None

        if not endpoint['last_checked']:
            latest_result = db.get_monitoring_results(endpoint_id=endpoint['id'], limit=1)
            endpoint['last_checked'] = latest_result[0]['checked_at'] if latest_result else None

    # Build service-level inventory rows for dashboard table.
    service_map = {}
    for endpoint in endpoints:
        service_name = endpoint['service_name']
        if service_name not in service_map:
            service_map[service_name] = {
                'service_name': service_name,
                'total_endpoints': 0,
                'active_endpoints': 0,
                'last_checked': None
            }

        service_map[service_name]['total_endpoints'] += 1
        if endpoint['is_active']:
            service_map[service_name]['active_endpoints'] += 1

        current_last_checked = service_map[service_name]['last_checked']
        endpoint_last_checked = endpoint.get('last_checked')
        if endpoint_last_checked and (not current_last_checked or endpoint_last_checked > current_last_checked):
            service_map[service_name]['last_checked'] = endpoint_last_checked

    service_inventory = sorted(service_map.values(), key=lambda s: s['service_name'].lower())
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "title": "API Sentinel Dashboard",
        "stats": stats,
        "service_inventory": service_inventory,
        "monitoring_stats": monitoring_stats
    })


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "API Sentinel"}


@app.get("/system/resources")
async def system_resources():
    """Return lightweight host resource metrics for UI widget polling."""
    uptime_seconds = int(time.time() - APP_START_TIME)
    hostname = socket.gethostname()

    if psutil is None:
        return {
            "available": False,
            "hostname": hostname,
            "uptime_seconds": uptime_seconds,
            "message": "psutil not installed"
        }

    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    cpu_percent = psutil.cpu_percent(interval=0.2)

    return {
        "available": True,
        "hostname": hostname,
        "uptime_seconds": uptime_seconds,
        "cpu_percent": round(cpu_percent, 1),
        "ram_percent": round(vm.percent, 1),
        "ram_used_gb": round(vm.used / (1024 ** 3), 2),
        "ram_total_gb": round(vm.total / (1024 ** 3), 2),
        "disk_percent": round(disk.percent, 1),
        "disk_used_gb": round(disk.used / (1024 ** 3), 2),
        "disk_total_gb": round(disk.total / (1024 ** 3), 2)
    }


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting API Sentinel...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
