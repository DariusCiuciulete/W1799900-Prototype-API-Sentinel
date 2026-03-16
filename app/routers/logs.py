"""
Logs Router - View and export system event logs
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import csv
import io
import logging
from app.database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/logs", tags=["logs"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def logs_page(request: Request, event_type: str = None):
    """Show system event logs"""
    if event_type:
        logs = db.get_logs(event_type=event_type, limit=500)
    else:
        logs = db.get_logs(limit=500)
    
    return templates.TemplateResponse("logs.html", {
        "request": request,
        "logs": logs,
        "event_type": event_type or "all",
        "count": len(logs)
    })


@router.get("/export")
async def export_logs(event_type: str = None):
    """Export logs to CSV"""
    try:
        if event_type:
            logs = db.get_logs(event_type=event_type, limit=10000)
        else:
            logs = db.get_logs(limit=10000)
        
        # Create CSV in memory
        output = io.StringIO()
        fieldnames = [
            'id', 'event_type', 'endpoint_id', 'message', 'details',
            'severity', 'created_at'
        ]
        
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(logs)
        
        logger.info(f"Exported {len(logs)} log entries")
        
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=api_sentinel_logs.csv"}
        )
    
    except Exception as e:
        logger.error(f"Error exporting logs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))