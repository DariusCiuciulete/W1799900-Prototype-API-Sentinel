"""
Monitoring Router - Check API health and save results
"""
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import time
import logging
import requests
from app.database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/monitoring", tags=["monitoring"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def monitoring_page(request: Request):
    """Show monitoring dashboard"""
    endpoints = db.get_all_endpoints(active_only=True)
    monitoring_stats = db.get_monitoring_stats()
    
    # Add the latest monitoring result for each endpoint
    for endpoint in endpoints:
        results = db.get_monitoring_results(endpoint_id=endpoint['id'], limit=1)
        endpoint['last_result'] = results[0] if results else None
        
        config = db.get_monitoring_config(endpoint['id'])
        endpoint['config'] = config
    
    return templates.TemplateResponse("monitoring.html", {
        "request": request,
        "endpoints": endpoints,
        "monitoring_stats": monitoring_stats
    })


def check_single_endpoint(endpoint: dict) -> dict:
    """
    Check if an endpoint is available.
    Returns: dictionary with status_code, response_time_ms, success flag
    """
    endpoint_id = endpoint['id']
    full_url = endpoint['base_url'].rstrip('/') + endpoint['path']
    method = endpoint['method'].lower()
    
    try:
        # Get monitoring config for timeout setting
        config = db.get_monitoring_config(endpoint_id)
        timeout = config['timeout_seconds'] if config else 30
        
        start_time = time.time()
        response = requests.request(
            method=method,
            url=full_url,
            timeout=timeout,
            allow_redirects=True,
            verify=False  # Note: In production, should verify SSL certificates
        )
        response_time_ms = (time.time() - start_time) * 1000
        
        # Success if status is 2xx or 3xx
        success = 200 <= response.status_code < 400
        
        # Create result dict
        result = {
            "endpoint_id": endpoint_id,
            "success": success,
            "status_code": response.status_code,
            "response_time_ms": response_time_ms
        }
        
        # Save result to database
        db.add_monitoring_result(
            endpoint_id=endpoint_id,
            status_code=response.status_code,
            response_time_ms=response_time_ms,
            success=success,
            error_message=None
        )
        
        # Check if any thresholds are breached
        db.check_and_trigger_alerts(endpoint_id, result)
        
        logger.info(f"Check {method.upper()} {endpoint['path']}: {response.status_code} ({response_time_ms:.2f}ms)")
        
        return result
    
    except requests.exceptions.Timeout:
        error_msg = "Timeout"
        
        # Create result for alert checking
        result = {
            "endpoint_id": endpoint_id,
            "success": False,
            "error": error_msg
        }
        
        db.add_monitoring_result(
            endpoint_id=endpoint_id,
            status_code=None,
            response_time_ms=None,
            success=False,
            error_message=error_msg
        )
        
        # Check thresholds (will trigger availability alert)
        db.check_and_trigger_alerts(endpoint_id, result)
        
        logger.warning(f"Timeout checking {endpoint['path']}")
        
        return result
    
    except Exception as e:
        error_msg = str(e)
        
        # Create result for alert checking
        result = {
            "endpoint_id": endpoint_id,
            "success": False,
            "error": error_msg
        }
        
        db.add_monitoring_result(
            endpoint_id=endpoint_id,
            status_code=None,
            response_time_ms=None,
            success=False,
            error_message=error_msg
        )
        
        # Check thresholds (will trigger availability alert)
        db.check_and_trigger_alerts(endpoint_id, result)
        
        logger.error(f"Error checking {endpoint['path']}: {error_msg}")
        
        return result


@router.post("/run")
async def run_monitoring():
    """Check all active endpoints right now"""
    endpoints = db.get_all_endpoints(active_only=True)
    
    if not endpoints:
        return {"success": False, "message": "No active endpoints"}
    
    db.log_event("MONITORING", None, f"Started monitoring of {len(endpoints)} endpoints")
    
    # Check each endpoint
    results = []
    for endpoint in endpoints:
        result = check_single_endpoint(endpoint)
        results.append(result)
    
    # Count successes and failures
    successes = sum(1 for r in results if r.get('success'))
    failures = len(results) - successes
    
    db.log_event("MONITORING", None, 
                f"Completed: {successes} success, {failures} failed")
    
    logger.info(f"Monitoring done: {successes}/{len(results)} successful")
    
    return {
        "success": True,
        "total": len(results),
        "successful": successes,
        "failed": failures,
        "message": f"Checked {len(results)} endpoints: {successes} ok, {failures} failed"
    }


@router.post("/test/{endpoint_id}")
async def test_endpoint(endpoint_id: int):
    """Check a single endpoint right now"""
    endpoint = db.get_endpoint_by_id(endpoint_id)
    
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    
    result = check_single_endpoint(endpoint)
    db.log_event("MONITORING", endpoint_id, f"Manual endpoint test: {result.get('status_code', 'error')}")
    return result


@router.post("/configure/{endpoint_id}")
async def configure_monitoring(
    endpoint_id: int,
    check_interval_seconds: int = Form(300),
    timeout_seconds: int = Form(30),
    latency_threshold_ms: float = Form(1000),
    error_rate_threshold: float = Form(0.1)
):
    """Set monitoring settings for an endpoint"""
    endpoint = db.get_endpoint_by_id(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    
    try:
        config_id = db.set_monitoring_config(
            endpoint_id=endpoint_id,
            check_interval_seconds=check_interval_seconds,
            timeout_seconds=timeout_seconds,
            latency_threshold_ms=latency_threshold_ms,
            error_rate_threshold=error_rate_threshold
        )
        
        db.log_event("MONITORING", endpoint_id, 
                    "Monitoring config updated")
        
        logger.info(f"Monitoring configured for endpoint {endpoint_id}")
        
        return {
            "success": True,
            "message": "Configuration saved"
        }
    
    except Exception as e:
        logger.error(f"Error configuring: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/results/{endpoint_id}")
async def get_endpoint_results(endpoint_id: int, limit: int = 50):
    """Get monitoring results for an endpoint"""
    endpoint = db.get_endpoint_by_id(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    
    results = db.get_monitoring_results(endpoint_id=endpoint_id, limit=limit)
    
    return {
        "endpoint": endpoint,
        "results": results
    }


@router.get("/stats")
async def get_stats():
    """Get overall monitoring statistics"""
    stats = db.get_monitoring_stats()
    return stats
