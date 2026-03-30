"""
Monitoring Router - Check API health and save results
"""
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import time
import asyncio
import logging
import requests
from app.database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/monitoring", tags=["monitoring"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent.parent / "templates"))

AUTO_MONITOR_TASK = None
SAFE_MONITOR_METHODS = {"GET", "HEAD"}


async def auto_monitor_loop():
    """Background loop that checks due endpoints for configured services"""
    while True:
        try:
            checked = await asyncio.to_thread(run_auto_monitoring_cycle)
            if checked > 0:
                logger.info(f"Auto monitoring cycle complete: {checked} endpoint(s) checked")
        except Exception as e:
            logger.error(f"Auto monitoring loop error: {str(e)}")

        await asyncio.sleep(30)


def run_auto_monitoring_cycle() -> int:
    """Run one automatic monitoring cycle"""
    total_checked = 0
    checked_endpoint_ids = []
    service_configs = db.get_service_monitoring_configs(enabled_only=True)

    for config in service_configs:
        service_name = config['service_name']
        due_endpoints = db.get_due_endpoints_for_auto_monitoring(service_name)
        due_endpoint_ids = [str(endpoint['id']) for endpoint in due_endpoints]

        if due_endpoints:
            db.log_event(
                "MONITORING",
                None,
                f"Automatic monitoring run for {service_name}",
                f"service={service_name}; endpoints_checked={len(due_endpoints)}; interval={config['check_interval_seconds']}s; endpoint_ids={','.join(due_endpoint_ids)}",
                "INFO"
            )

        for endpoint in due_endpoints:
            check_single_endpoint(endpoint, run_mode="auto")
            total_checked += 1
            checked_endpoint_ids.append(str(endpoint['id']))

    if total_checked > 0:
        db.log_event(
            "MONITORING",
            None,
            f"Automatic monitoring cycle checked {total_checked} endpoint(s)",
            f"configured_services={len(service_configs)}; endpoint_ids={','.join(checked_endpoint_ids)}",
            "INFO"
        )

    return total_checked


async def start_auto_monitoring():
    """Start background automatic monitoring task"""
    global AUTO_MONITOR_TASK
    if AUTO_MONITOR_TASK is None or AUTO_MONITOR_TASK.done():
        AUTO_MONITOR_TASK = asyncio.create_task(auto_monitor_loop())
        logger.info("Automatic monitoring loop started")


async def stop_auto_monitoring():
    """Stop background automatic monitoring task"""
    global AUTO_MONITOR_TASK
    if AUTO_MONITOR_TASK and not AUTO_MONITOR_TASK.done():
        AUTO_MONITOR_TASK.cancel()
        try:
            await AUTO_MONITOR_TASK
        except asyncio.CancelledError:
            pass
        logger.info("Automatic monitoring loop stopped")


@router.get("/", response_class=HTMLResponse)
async def monitoring_page(request: Request):
    """Show monitoring dashboard"""
    endpoints = db.get_all_endpoints(active_only=True)
    monitoring_stats = db.get_monitoring_stats()
    active_alerts_count = len(db.get_active_alerts())
    services = db.get_services()
    service_configs = db.get_service_monitoring_configs(enabled_only=False)
    configs_by_service = {c['service_name']: c for c in service_configs}
    
    # Add the latest monitoring result for each endpoint
    for endpoint in endpoints:
        results = db.get_monitoring_results(endpoint_id=endpoint['id'], limit=1)
        endpoint['last_result'] = results[0] if results else None
        
        config = db.get_monitoring_config(endpoint['id'])
        endpoint['config'] = config

    for service in services:
        service['config'] = configs_by_service.get(service['service_name'])
    
    return templates.TemplateResponse("monitoring.html", {
        "request": request,
        "endpoints": endpoints,
        "monitoring_stats": monitoring_stats,
        "active_alerts_count": active_alerts_count,
        "services": services
    })


def check_single_endpoint(endpoint: dict, run_mode: str = "manual") -> dict:
    """
    Check if an endpoint is available.
    Returns: dictionary with status_code, response_time_ms, success flag
    """
    endpoint_id = endpoint['id']
    full_url = endpoint['base_url'].rstrip('/') + endpoint['path']
    original_method = endpoint['method'].upper()

    if original_method not in SAFE_MONITOR_METHODS:
        result = {
            "endpoint_id": endpoint_id,
            "service_name": endpoint['service_name'],
            "success": None,
            "status": "Skipped",
            "status_code": None,
            "response_time_ms": None,
            "skipped": True,
            "reason": f"Skipped non-safe method: {original_method}"
        }

        db.update_last_check(endpoint_id)
        db.log_event(
            "MONITORING",
            endpoint_id,
            "Endpoint check skipped (non-safe method)",
            f"mode={run_mode}; service={endpoint['service_name']}; method={original_method}; url={full_url}",
            "INFO"
        )

        logger.info(f"Skipped {original_method} {endpoint['path']} (non-safe method)")
        return result

    method = original_method.lower()
    
    try:
        # Get monitoring config for timeout setting
        config = db.get_monitoring_config(endpoint_id)
        timeout = config['timeout_seconds'] if config else 30
        latency_threshold = config['latency_threshold_ms'] if config else 1000

        headers = {}
        auth_type = config['auth_type'] if config and config.get('auth_type') else 'none'
        auth_value = config['auth_value'] if config else None
        auth_header_name = config['auth_header_name'] if config and config.get('auth_header_name') else 'X-API-Key'

        if auth_type == 'bearer' and auth_value:
            headers['Authorization'] = f"Bearer {auth_value}"
        elif auth_type == 'api_key' and auth_value:
            headers[auth_header_name] = auth_value
        
        start_time = time.time()
        response = requests.request(
            method=method,
            url=full_url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
            verify=True
        )
        response_time_ms = (time.time() - start_time) * 1000
        
        # Success if status is 2xx or 3xx
        success = 200 <= response.status_code < 400
        auth_failed = response.status_code in (401, 403)
        result_status = "Auth Failed" if auth_failed else ("Healthy" if success else "Failed")
        error_message = "Auth Failed" if auth_failed else None
        
        # Create result dict
        result = {
            "endpoint_id": endpoint_id,
            "service_name": endpoint['service_name'],
            "success": success,
            "status": result_status,
            "status_code": response.status_code,
            "response_time_ms": response_time_ms
        }
        
        # Save result to database
        db.add_monitoring_result(
            endpoint_id=endpoint_id,
            status_code=response.status_code,
            response_time_ms=response_time_ms,
            success=success,
            error_message=error_message
        )
        
        # Check if any thresholds are breached
        alert_ids = db.check_and_trigger_alerts(endpoint_id, result)
        db.update_last_check(endpoint_id)

        if auth_failed:
            db.log_event(
                "MONITORING",
                endpoint_id,
                "Auth Failed (HTTP 401/403)",
                f"mode={run_mode}; service={endpoint['service_name']}; method={method.upper()}; url={full_url}; "
                f"status_code={response.status_code}; auth_type={auth_type}",
                "ERROR"
            )
        elif not success:
            db.log_event(
                "MONITORING",
                endpoint_id,
                f"Endpoint check failed with HTTP {response.status_code}",
                f"mode={run_mode}; service={endpoint['service_name']}; method={method.upper()}; url={full_url}; "
                f"status_code={response.status_code}; response_time_ms={response_time_ms:.2f}",
                "WARNING"
            )
        elif response_time_ms > latency_threshold:
            db.log_event(
                "MONITORING",
                endpoint_id,
                "Endpoint latency exceeded configured threshold",
                f"mode={run_mode}; service={endpoint['service_name']}; method={method.upper()}; url={full_url}; "
                f"response_time_ms={response_time_ms:.2f}; latency_threshold_ms={latency_threshold}",
                "WARNING"
            )

        if alert_ids:
            logger.warning(f"Endpoint {endpoint_id} triggered {len(alert_ids)} alert(s)")
        
        logger.info(f"Check {method.upper()} {endpoint['path']}: {response.status_code} ({response_time_ms:.2f}ms)")
        
        return result
    
    except requests.exceptions.Timeout:
        error_msg = "Timeout"
        
        # Create result for alert checking
        result = {
            "endpoint_id": endpoint_id,
            "service_name": endpoint['service_name'],
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
        db.update_last_check(endpoint_id)

        db.log_event(
            "MONITORING",
            endpoint_id,
            "Endpoint check failed: timeout",
            f"mode={run_mode}; service={endpoint['service_name']}; method={method.upper()}; url={full_url}; timeout_seconds={timeout}",
            "ERROR"
        )
        
        logger.warning(f"Timeout checking {endpoint['path']}")
        
        return result
    
    except Exception as e:
        error_msg = str(e)
        
        # Create result for alert checking
        result = {
            "endpoint_id": endpoint_id,
            "service_name": endpoint['service_name'],
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
        db.update_last_check(endpoint_id)

        db.log_event(
            "MONITORING",
            endpoint_id,
            "Endpoint check failed: exception",
            f"mode={run_mode}; service={endpoint['service_name']}; method={method.upper()}; url={full_url}; error={error_msg}",
            "ERROR"
        )
        
        logger.error(f"Error checking {endpoint['path']}: {error_msg}")
        
        return result


@router.post("/run")
async def run_monitoring():
    """Check all active endpoints right now"""
    endpoints = db.get_all_endpoints(active_only=True)
    enabled_endpoints = []

    for endpoint in endpoints:
        service_config = db.get_service_monitoring_config(endpoint['service_name'])
        if service_config and not service_config['enabled']:
            continue
        enabled_endpoints.append(endpoint)
    endpoints = enabled_endpoints
    
    if not endpoints:
        return {"success": False, "message": "No active endpoints"}
    
    db.log_event("MONITORING", None, f"Started monitoring of {len(endpoints)} endpoints")
    
    # Check each endpoint
    results = []
    endpoint_ids = [str(endpoint['id']) for endpoint in endpoints]
    for endpoint in endpoints:
        result = check_single_endpoint(endpoint, run_mode="manual")
        results.append(result)
    
    # Count successes and failures
    successes = sum(1 for r in results if r.get('success') is True)
    skipped = sum(1 for r in results if r.get('status') == 'Skipped' or r.get('skipped'))
    failures = len(results) - successes - skipped
    
    db.log_event(
        "MONITORING",
        None,
        f"Completed: {successes} success, {failures} failed, {skipped} skipped",
        f"mode=manual; total={len(results)}; successful={successes}; failed={failures}; skipped={skipped}; endpoint_ids={','.join(endpoint_ids)}",
        "INFO" if failures == 0 else "WARNING"
    )
    
    logger.info(f"Monitoring done: {successes}/{len(results)} successful")
    
    return {
        "success": True,
        "total": len(results),
        "successful": successes,
        "failed": failures,
        "skipped": skipped,
        "message": f"Checked {len(results)} endpoints: {successes} ok, {failures} failed, {skipped} skipped"
    }


@router.post("/test/{endpoint_id}")
async def test_endpoint(endpoint_id: int):
    """Check a single endpoint right now"""
    endpoint = db.get_endpoint_by_id(endpoint_id)
    
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    
    result = check_single_endpoint(endpoint, run_mode="manual-test")
    severity = "INFO" if result.get('success') or result.get('status') == 'Skipped' else "WARNING"
    db.log_event(
        "MONITORING",
        endpoint_id,
        f"Manual endpoint test: {result.get('status', result.get('status_code', 'error'))}",
        f"mode=manual-test; service={endpoint['service_name']}; endpoint={endpoint['method']} {endpoint['path']}; "
        f"success={result.get('success')}; skipped={result.get('skipped', False)}; response_time_ms={result.get('response_time_ms')}",
        severity
    )
    return result


@router.post("/service/setup")
async def setup_service_monitoring(
    service_name: str = Form(...),
    check_interval_seconds: int = Form(300),
    timeout_seconds: int = Form(30),
    latency_threshold_ms: float = Form(1000),
    error_rate_threshold: float = Form(10),
    enabled: bool = Form(True)
):
    """Configure monitoring once for a service and apply to all active endpoints"""
    service_names = [s['service_name'] for s in db.get_services()]
    if service_name not in service_names:
        raise HTTPException(status_code=404, detail="Service not found")

    try:
        db.set_service_monitoring_config(
            service_name=service_name,
            check_interval_seconds=check_interval_seconds,
            timeout_seconds=timeout_seconds,
            latency_threshold_ms=latency_threshold_ms,
            error_rate_threshold=error_rate_threshold,
            enabled=enabled
        )

        updated_endpoints = db.apply_service_config_to_endpoints(service_name)
        service_endpoint_ids = [str(endpoint['id']) for endpoint in db.get_service_endpoints(service_name, active_only=True)]

        db.log_event(
            "MONITORING",
            None,
            f"Service monitoring configured: {service_name}",
            f"service={service_name}; interval={check_interval_seconds}s; timeout={timeout_seconds}s; "
            f"latency_threshold_ms={latency_threshold_ms}; error_rate_threshold={error_rate_threshold}%; "
            f"enabled={enabled}; applied_endpoints={updated_endpoints}; endpoint_ids={','.join(service_endpoint_ids)}",
            "INFO"
        )

        return {
            "success": True,
            "message": f"Monitoring configured for service '{service_name}'",
            "applied_endpoints": updated_endpoints
        }

    except Exception as e:
        logger.error(f"Error configuring service monitoring: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/service/disable/{service_name}")
async def disable_service_monitoring(service_name: str):
    """Disable automatic monitoring for a service"""
    config = db.get_service_monitoring_config(service_name)
    if not config:
        raise HTTPException(status_code=404, detail="Service monitoring config not found")

    db.set_service_monitoring_config(
        service_name=service_name,
        check_interval_seconds=config['check_interval_seconds'],
        timeout_seconds=config['timeout_seconds'],
        latency_threshold_ms=config['latency_threshold_ms'],
        error_rate_threshold=config['error_rate_threshold'],
        enabled=False
    )

    service_endpoint_ids = [str(endpoint['id']) for endpoint in db.get_service_endpoints(service_name, active_only=True)]

    db.log_event(
        "MONITORING",
        None,
        f"Service monitoring disabled: {service_name}",
        f"service={service_name}; endpoint_ids={','.join(service_endpoint_ids)}",
        "WARNING"
    )

    return {"success": True, "message": f"Disabled monitoring for {service_name}"}


@router.post("/service/enable/{service_name}")
async def enable_service_monitoring(service_name: str):
    """Enable automatic monitoring for a service"""
    config = db.get_service_monitoring_config(service_name)
    if not config:
        raise HTTPException(status_code=404, detail="Service monitoring config not found")

    db.set_service_monitoring_config(
        service_name=service_name,
        check_interval_seconds=config['check_interval_seconds'],
        timeout_seconds=config['timeout_seconds'],
        latency_threshold_ms=config['latency_threshold_ms'],
        error_rate_threshold=config['error_rate_threshold'],
        enabled=True
    )

    service_endpoint_ids = [str(endpoint['id']) for endpoint in db.get_service_endpoints(service_name, active_only=True)]

    db.log_event(
        "MONITORING",
        None,
        f"Service monitoring enabled: {service_name}",
        f"service={service_name}; endpoint_ids={','.join(service_endpoint_ids)}",
        "INFO"
    )

    return {"success": True, "message": f"Enabled monitoring for {service_name}"}


@router.get("/service/configs")
async def get_service_configs():
    """Get all service-level monitoring configurations"""
    return {
        "configs": db.get_service_monitoring_configs(enabled_only=False)
    }


@router.post("/configure/{endpoint_id}")
async def configure_monitoring(
    endpoint_id: int,
    check_interval_seconds: int = Form(300),
    timeout_seconds: int = Form(30),
    latency_threshold_ms: float = Form(1000),
    error_rate_threshold: float = Form(10),
    auth_type: str = Form("none"),
    auth_value: str = Form(""),
    auth_header_name: str = Form("X-API-Key")
):
    """Set monitoring settings for an endpoint"""
    endpoint = db.get_endpoint_by_id(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    
    try:
        db.set_monitoring_config(
            endpoint_id=endpoint_id,
            check_interval_seconds=check_interval_seconds,
            timeout_seconds=timeout_seconds,
            latency_threshold_ms=latency_threshold_ms,
            error_rate_threshold=error_rate_threshold,
            auth_type=auth_type,
            auth_value=auth_value,
            auth_header_name=auth_header_name
        )

        db.set_alert_threshold(endpoint_id, 'latency', latency_threshold_ms)
        db.set_alert_threshold(endpoint_id, 'error_rate', error_rate_threshold)
        db.set_alert_threshold(endpoint_id, 'availability', 1)
        
        db.log_event(
            "MONITORING",
            endpoint_id,
            "Endpoint-level monitoring config updated",
            f"interval={check_interval_seconds}s; timeout={timeout_seconds}s; "
            f"latency_threshold_ms={latency_threshold_ms}; error_rate_threshold={error_rate_threshold}%; "
            f"auth_type={auth_type}; auth_header_name={auth_header_name}",
            "INFO"
        )
        
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
