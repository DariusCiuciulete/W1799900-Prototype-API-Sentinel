"""
Discovery Router - API discovery from OpenAPI/Swagger specs and documentation
"""
from fastapi import APIRouter, Request, File, UploadFile, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import yaml
import json
import re
import logging
import requests
from bs4 import BeautifulSoup
from app.database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/discovery", tags=["discovery"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent.parent / "templates"))

# HTTP methods we recognize
VALID_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS']


def is_internal_api(base_url: str) -> bool:
    """Check if URL looks like an internal API"""
    patterns = ['localhost', '127.0.0.1', '192.168.', '10.', 'internal', 'local']
    return any(pattern in base_url.lower() for pattern in patterns)


def parse_openapi_spec(spec_dict: dict) -> tuple:
    """
    Extract service info and endpoints from an OpenAPI spec dictionary.
    Returns: (service_name, base_url, is_internal, auth_type, endpoints_list)
    """
    # Get service name
    service_name = spec_dict.get('info', {}).get('title', 'Unknown API')
    
    # Get base URL
    base_url = ""
    if 'servers' in spec_dict and spec_dict['servers']:
        base_url = spec_dict['servers'][0]['url']
    elif 'host' in spec_dict:  # Swagger 2.0 format
        scheme = spec_dict.get('schemes', ['https'])[0]
        base_path = spec_dict.get('basePath', '')
        base_url = f"{scheme}://{spec_dict['host']}{base_path}"
    
    # Determine if internal
    is_internal = is_internal_api(base_url)
    
    # Get auth types
    auth_types = []
    if 'securitySchemes' in spec_dict.get('components', {}):
        auth_types = list(spec_dict['components']['securitySchemes'].keys())
    elif 'securityDefinitions' in spec_dict:  # Swagger 2.0
        auth_types = list(spec_dict['securityDefinitions'].keys())
    
    auth_type = ', '.join(auth_types) if auth_types else None
    
    # Extract endpoints
    endpoints = []
    paths = spec_dict.get('paths', {})
    
    for path, methods in paths.items():
        for method, details in methods.items():
            if method.upper() not in VALID_METHODS:
                continue
            
            description = details.get('summary') or details.get('description', '')
            endpoints.append({
                'path': path,
                'method': method.upper(),
                'description': description
            })
    
    return service_name, base_url, is_internal, auth_type, endpoints


@router.get("/", response_class=HTMLResponse)
async def discovery_page(request: Request):
    """Show the discovery page"""
    return templates.TemplateResponse("discovery.html", {
        "request": request
    })


@router.post("/upload-spec")
async def upload_openapi_spec(
    file: UploadFile = File(...),
    service_name: str = Form(None)
):
    """Parse an OpenAPI/Swagger spec file and add endpoints to inventory"""
    try:
        content = await file.read()
        
        # Try JSON first, then YAML
        try:
            if file.filename.endswith('.json'):
                spec = json.loads(content.decode('utf-8'))
            else:
                spec = yaml.safe_load(content.decode('utf-8'))
        except Exception as e:
            logger.error(f"Could not parse spec file: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid spec file (not valid JSON or YAML)")
        
        # Extract info from spec
        parsed_service_name, base_url, is_internal, auth_type, endpoints = parse_openapi_spec(spec)
        final_service_name = service_name or parsed_service_name
        
        # Add each endpoint to the database
        endpoints_added = 0
        endpoint_ids = []
        for ep in endpoints:
            endpoint_id = db.add_endpoint(
                service_name=final_service_name,
                base_url=base_url,
                path=ep['path'],
                method=ep['method'],
                description=ep['description'],
                auth_type=auth_type,
                is_internal=is_internal,
                discovery_source=f"openapi:{file.filename}"
            )
            endpoints_added += 1
            endpoint_ids.append(str(endpoint_id))
        
        logger.info(f"Discovered {endpoints_added} endpoints from {file.filename}")
        db.log_event("DISCOVERY", None, 
                    f"OpenAPI spec parsed: {final_service_name}",
                    f"file={file.filename}; endpoints={endpoints_added}; endpoint_ids={','.join(endpoint_ids)}")
        
        return {
            "success": True,
            "service_name": final_service_name,
            "endpoints_added": endpoints_added,
            "message": f"Found {endpoints_added} endpoints"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing spec: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.post("/upload-docs")
async def upload_documentation(
    file: UploadFile = File(...),
    service_name: str = Form(...),
    base_url: str = Form(...)
):
    """Parse API documentation and extract endpoints"""
    try:
        content = await file.read()
        text_content = content.decode('utf-8')
        
        # Determine if internal
        is_internal = is_internal_api(base_url)
        
        # Parse as HTML if it looks like HTML, otherwise use as text
        if file.filename.endswith('.html') or '<html' in text_content.lower():
            soup = BeautifulSoup(text_content, 'html.parser')
            code_blocks = soup.find_all(['code', 'pre', 'div'])
            text_to_search = '\n'.join([block.get_text() for block in code_blocks])
        else:
            text_to_search = text_content
        
        # Find endpoint patterns in the text
        found_endpoints = set()
        
        # Pattern 1: "GET /api/users"
        pattern1 = r'\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/[\w\-/{}:]*)'
        for match in re.finditer(pattern1, text_to_search, re.IGNORECASE):
            method, path = match.groups()
            found_endpoints.add((method.upper(), path))
        
        # Pattern 2: Markdown style "`GET /api/users`"
        pattern2 = r'`(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/[\w\-/{}:]*)`'
        for match in re.finditer(pattern2, text_to_search, re.IGNORECASE):
            method, path = match.groups()
            found_endpoints.add((method.upper(), path))
        
        # Add discovered endpoints to database
        endpoints_added = 0
        endpoint_ids = []
        for method, path in found_endpoints:
            endpoint_id = db.add_endpoint(
                service_name=service_name,
                base_url=base_url,
                path=path,
                method=method,
                description=f"From {file.filename}",
                auth_type=None,
                is_internal=is_internal,
                discovery_source=f"docs:{file.filename}"
            )
            endpoints_added += 1
            endpoint_ids.append(str(endpoint_id))
        
        logger.info(f"Discovered {endpoints_added} endpoints from {file.filename}")
        db.log_event("DISCOVERY", None,
                    f"Documentation parsed: {service_name}",
                    f"file={file.filename}; endpoints={endpoints_added}; endpoint_ids={','.join(endpoint_ids)}")
        
        return {
            "success": True,
            "service_name": service_name,
            "endpoints_added": endpoints_added,
            "message": f"Found {endpoints_added} endpoints"
        }
    
    except Exception as e:
        logger.error(f"Error processing documentation: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.post("/parse-url")
async def parse_url(
    url: str = Form(...),
    service_name: str = Form(None)
):
    """Fetch and parse OpenAPI spec from a URL"""
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        # Try to parse as JSON or YAML
        try:
            spec = response.json()
        except:
            spec = yaml.safe_load(response.text)
        
        # Extract info from spec
        parsed_service_name, base_url, is_internal, auth_type, endpoints = parse_openapi_spec(spec)
        final_service_name = service_name or parsed_service_name
        
        # Add endpoints to database
        endpoints_added = 0
        endpoint_ids = []
        for ep in endpoints:
            endpoint_id = db.add_endpoint(
                service_name=final_service_name,
                base_url=base_url,
                path=ep['path'],
                method=ep['method'],
                description=ep['description'],
                auth_type=auth_type,
                is_internal=is_internal,
                discovery_source=f"url:{url}"
            )
            endpoints_added += 1
            endpoint_ids.append(str(endpoint_id))
        
        logger.info(f"Discovered {endpoints_added} endpoints from URL")
        db.log_event("DISCOVERY", None,
                    f"OpenAPI spec from URL: {final_service_name}",
                    f"url={url}; endpoints={endpoints_added}; endpoint_ids={','.join(endpoint_ids)}")
        
        return {
            "success": True,
            "service_name": final_service_name,
            "endpoints_added": endpoints_added,
            "message": f"Found {endpoints_added} endpoints"
        }
    
    except requests.exceptions.RequestException as e:
        logger.error(f"Could not fetch URL: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Could not fetch URL: {str(e)}")
    except Exception as e:
        logger.error(f"Error parsing spec: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
