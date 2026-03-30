"""
Alerts Router - Manage API alerts and thresholds
"""
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import logging
from app.database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def alerts_page(request: Request):
    """Show active alerts"""
    alerts = db.get_active_alerts()
    
    return templates.TemplateResponse("alerts.html", {
        "request": request,
        "alerts": alerts,
        "count": len(alerts)
    })


@router.post("/resolve/{alert_id}")
async def resolve_alert(alert_id: int):
    """Mark an alert as resolved"""
    try:
        matching_alerts = [alert for alert in db.get_active_alerts() if alert['id'] == alert_id]
        alert = matching_alerts[0] if matching_alerts else None
        success = db.resolve_alert(alert_id)
        
        if success:
            logger.info(f"Resolved alert {alert_id}")
            db.log_event(
                "ALERT",
                alert['endpoint_id'] if alert else None,
                f"Alert {alert_id} resolved",
                f"endpoint_id={alert['endpoint_id']}" if alert else None,
                "INFO"
            )
            return {"success": True}
        else:
            raise HTTPException(status_code=404, detail="Alert not found")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resolving alert: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error while resolving alert")


@router.post("/endpoint/{endpoint_id}/threshold")
async def set_endpoint_threshold(
    endpoint_id: int,
    threshold_type: str = Form(...),
    threshold_value: float = Form(...)
):
    """Set a threshold for an endpoint"""
    try:
        # Verify endpoint exists
        endpoint = db.get_endpoint_by_id(endpoint_id)
        if not endpoint:
            raise HTTPException(status_code=404, detail="Endpoint not found")
        
        db.set_alert_threshold(endpoint_id, threshold_type, threshold_value)
        
        logger.info(f"Set threshold for endpoint {endpoint_id}: {threshold_type} = {threshold_value}")
        db.log_event("ALERT", endpoint_id, f"Threshold set: {threshold_type} = {threshold_value}")
        
        return {"success": True, "message": f"Threshold set: {threshold_type}"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting threshold: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error while setting threshold")


@router.get("/endpoint/{endpoint_id}/thresholds")
async def get_endpoint_thresholds(endpoint_id: int):
    """Get all thresholds for an endpoint"""
    endpoint = db.get_endpoint_by_id(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    
    thresholds = db.get_alert_thresholds(endpoint_id)
    return {"thresholds": thresholds}


@router.get("/endpoint/{endpoint_id}")
async def get_endpoint_alerts(endpoint_id: int):
    """Get alerts for a specific endpoint"""
    alerts = db.get_active_alerts(endpoint_id)
    return {"alerts": alerts}