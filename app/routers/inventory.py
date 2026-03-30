"""
Inventory Router - Manage API endpoints inventory
"""
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import csv
import io
import logging
from app.database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inventory", tags=["inventory"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def inventory_page(request: Request, search: str = None):
    """Show inventory of all API endpoints"""
    endpoints = db.get_all_endpoints()
    
    # Filter endpoints if user searched for something
    if search:
        search_lower = search.lower()
        endpoints = [e for e in endpoints if 
                    search_lower in e['service_name'].lower() or 
                    search_lower in e['path'].lower() or
                    search_lower in e['method'].lower()]
    
    return templates.TemplateResponse("inventory.html", {
        "request": request,
        "endpoints": endpoints,
        "search": search or ""
    })


@router.post("/add")
async def add_endpoint(
    service_name: str = Form(...),
    base_url: str = Form(...),
    path: str = Form(...),
    method: str = Form(...),
    description: str = Form(None),
    is_internal: bool = Form(False)
):
    """Add a new endpoint to the inventory"""
    try:
        endpoint_id = db.add_endpoint(
            service_name=service_name,
            base_url=base_url,
            path=path,
            method=method,
            description=description,
            is_internal=is_internal,
            discovery_source="manual"
        )
        
        logger.info(f"Added endpoint: {service_name} {method} {path}")
        db.log_event("INVENTORY", endpoint_id, f"Endpoint added: {method} {path}")
        return {"success": True, "endpoint_id": endpoint_id}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error while adding endpoint")


@router.post("/update/{endpoint_id}")
async def update_endpoint(
    endpoint_id: int,
    service_name: str = Form(None),
    base_url: str = Form(None),
    path: str = Form(None),
    method: str = Form(None),
    description: str = Form(None),
    is_internal: bool = Form(None),
    is_active: bool = Form(None)
):
    """Update an existing endpoint"""
    try:
        # Build update dictionary with only provided fields
        updates = {}
        if service_name:
            updates['service_name'] = service_name
        if base_url:
            updates['base_url'] = base_url
        if path:
            updates['path'] = path
        if method:
            updates['method'] = method
        if description is not None:
            updates['description'] = description
        if is_internal is not None:
            updates['is_internal'] = is_internal
        if is_active is not None:
            updates['is_active'] = is_active
        
        success = db.update_endpoint(endpoint_id, **updates)
        
        if success:
            logger.info(f"Updated endpoint {endpoint_id}")
            db.log_event("INVENTORY", endpoint_id, "Endpoint updated")
            return {"success": True, "message": "Updated"}
        else:
            raise HTTPException(status_code=404, detail="Endpoint not found")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error while updating endpoint")


@router.post("/delete/{endpoint_id}")
async def delete_endpoint(endpoint_id: int):
    """Delete an endpoint"""
    try:
        success = db.delete_endpoint(endpoint_id)
        
        if success:
            logger.info(f"Deleted endpoint {endpoint_id}")
            return {"success": True}
        else:
            raise HTTPException(status_code=404, detail="Endpoint not found")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error while deleting endpoint")


@router.post("/toggle/{endpoint_id}")
async def toggle_endpoint(endpoint_id: int):
    """Activate or deactivate an endpoint"""
    try:
        endpoint = db.get_endpoint_by_id(endpoint_id)
        if not endpoint:
            raise HTTPException(status_code=404, detail="Endpoint not found")
        
        # Toggle the active status
        new_status = not endpoint['is_active']
        success = db.update_endpoint(endpoint_id, is_active=new_status)
        
        if success:
            status = "active" if new_status else "inactive"
            logger.info(f"Endpoint {endpoint_id} is now {status}")
            db.log_event("INVENTORY", endpoint_id, f"Endpoint toggled: {status}")
            return {"success": True, "is_active": new_status}
        else:
            raise HTTPException(status_code=500, detail="Could not update")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error while toggling endpoint")


@router.get("/export")
async def export_inventory():
    """Export all endpoints as CSV"""
    try:
        endpoints = db.get_all_endpoints()
        
        # Create CSV file in memory
        output = io.StringIO()
        fieldnames = [
            'id', 'service_name', 'base_url', 'path', 'method', 'description',
            'is_internal', 'is_active', 'discovery_source',
            'created_at', 'updated_at'
        ]
        
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(endpoints)
        
        logger.info("Exported inventory to CSV")
        
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=api_inventory.csv"}
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error while exporting inventory")


@router.get("/{endpoint_id}")
async def get_endpoint_details(request: Request, endpoint_id: int):
    """Show detailed info about one endpoint"""
    endpoint = db.get_endpoint_by_id(endpoint_id)
    
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    
    # Get recent monitoring results
    monitoring_results = db.get_monitoring_results(endpoint_id=endpoint_id, limit=50)
    
    return templates.TemplateResponse("endpoint_detail.html", {
        "request": request,
        "endpoint": endpoint,
        "monitoring_results": monitoring_results
    })
