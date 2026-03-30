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
import re
from app.database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/logs", tags=["logs"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent.parent / "templates"))


def format_endpoint_id_list(endpoint_ids_text: str) -> str:
    """Format comma-separated endpoint IDs for display"""
    endpoint_ids = [endpoint_id.strip() for endpoint_id in endpoint_ids_text.split(",") if endpoint_id.strip()]
    return ", ".join(endpoint_ids)


def infer_auto_cycle_endpoint_ids(log_id: int, expected_count: int) -> str:
    """Infer endpoint IDs for older automatic cycle summary logs"""
    if expected_count <= 0:
        return ""

    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute(
        '''
        SELECT endpoint_id
        FROM event_logs
        WHERE event_type = 'MONITORING'
          AND endpoint_id IS NOT NULL
          AND id < ?
          AND details LIKE 'mode=auto%'
        ORDER BY id DESC
        LIMIT ?
        ''',
        (log_id, expected_count)
    )

    endpoint_ids = [str(row['endpoint_id']) for row in cursor.fetchall()]
    conn.close()

    if len(endpoint_ids) != expected_count:
        return ""

    endpoint_ids.reverse()
    return ", ".join(endpoint_ids)


def get_log_target_display(log: dict) -> str:
    """Show endpoint ID information for any log entry that refers to endpoints"""
    if log.get("endpoint_id") is not None:
        return str(log["endpoint_id"])

    details = log.get("details") or ""

    endpoint_id_match = re.search(r"endpoint_id=([^;]+)", details)
    if endpoint_id_match:
        return endpoint_id_match.group(1).strip()

    endpoint_ids_match = re.search(r"endpoint_ids=([^;]+)", details)
    if endpoint_ids_match:
        return format_endpoint_id_list(endpoint_ids_match.group(1).strip())

    auto_cycle_match = re.search(r"Automatic monitoring cycle checked (\d+) endpoint\(s\)", log.get("message") or "")
    if auto_cycle_match:
        return infer_auto_cycle_endpoint_ids(log["id"], int(auto_cycle_match.group(1)))

    deleted_endpoint_match = re.search(r"Endpoint deleted \(ID: (\d+)\)", log.get("message") or "")
    if deleted_endpoint_match:
        return deleted_endpoint_match.group(1)

    return ""


@router.get("/", response_class=HTMLResponse)
async def logs_page(request: Request, event_type: str = None, limit: int = 100):
    """Show system event logs"""
    limit = max(100, min(limit, 5000))

    if event_type:
        logs = db.get_logs(event_type=event_type, limit=limit)
    else:
        logs = db.get_logs(limit=limit)

    total_count = db.get_logs_count(event_type=event_type) if event_type else db.get_logs_count()
    has_more = limit < total_count

    for log in logs:
        log["target_display"] = get_log_target_display(log)
    
    return templates.TemplateResponse("logs.html", {
        "request": request,
        "logs": logs,
        "event_type": event_type or "all",
        "count": len(logs),
        "total_count": total_count,
        "limit": limit,
        "next_limit": limit + 100,
        "has_more": has_more
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