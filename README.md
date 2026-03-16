## API Sentinel - API Discovery, Inventory & Monitoring

A lightweight, self-contained system for small and medium enterprises to discover, inventory, and monitor REST APIs.

### Features

✓ **API Discovery** — Parse OpenAPI/Swagger specs and documentation to extract endpoints  
✓ **Inventory Management** — Store and manage discovered APIs in a central database  
✓ **Health Monitoring** — Periodically check API availability, response times, and error rates  
✓ **Alert Thresholds** — Automatic alerts when endpoints exceed latency or availability thresholds  
✓ **Event Logging** — Complete audit trail of discovery, inventory, monitoring, and alert events  
✓ **Web Dashboard** — Single-pane interface to view inventory, metrics, alerts, and logs  
✓ **CSV Export** — Export inventory and event logs  

### Quick Start

#### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

#### 2. Run the Application
```bash
python -m uvicorn app.main:app --reload --port 8000
```

Visit http://localhost:8000 in your browser.

#### 3. Discover APIs

Go to **Discovery** and upload an OpenAPI/Swagger spec file or API documentation to automatically extract endpoints.

#### 4. Monitor Endpoints

Go to **Monitoring** and click **Run Monitoring Now** to check all active endpoints for availability and response time.

#### 5. View Results

- **Dashboard** — Overview of all APIs and key metrics
- **Inventory** — Full list of discovered endpoints
- **Alerts** — Active threshold violations
- **Logs** — Complete event history (filterable by type)

---

### Event Logging

Every action is logged for traceability:

- **DISCOVERY** — OpenAPI imports, spec parsing
- **INVENTORY** — Endpoint add/update/delete/toggle
- **MONITORING** — Manual checks, automated monitoring runs
- **ALERT** — Threshold breaches, alert resolution

View logs at `/logs` or export to CSV.

---

### Alert Thresholds

Configure thresholds for each endpoint to trigger automatic alerts:

#### Available Threshold Types
- **latency** — Response time in milliseconds (ms)
- **availability** — Triggered when endpoint is down
- **error_rate** — Error rate as percentage (%)

#### Set a Threshold via API

```bash
POST /alerts/endpoint/{endpoint_id}/threshold

Form data:
- threshold_type: "latency"  (or "availability", "error_rate")
- threshold_value: 1000      (or for error_rate: 10 for 10%)
```

**Example:** Set a 1000ms latency threshold for endpoint #3:

```bash
curl -X POST http://localhost:8000/alerts/endpoint/3/threshold \
  -d "threshold_type=latency&threshold_value=1000"
```

#### Get Thresholds for an Endpoint

```bash
GET /alerts/endpoint/{endpoint_id}/thresholds
```

#### View Active Alerts

Visit `/alerts` to see all active threshold violations. Click **Resolve** to mark an alert as addressed.

---

### API Reference

#### Inventory
- `GET /inventory` — View all endpoints
- `POST /inventory/add` — Add endpoint manually
- `POST /inventory/update/{id}` — Update endpoint
- `POST /inventory/delete/{id}` — Delete endpoint
- `POST /inventory/toggle/{id}` — Enable/disable endpoint
- `GET /inventory/export` — Export to CSV

#### Discovery
- `POST /discovery/upload-spec` — Upload OpenAPI/Swagger file
- `POST /discovery/upload-docs` — Upload documentation
- `POST /discovery/parse-url` — Fetch spec from URL

#### Monitoring
- `GET /monitoring` — Monitoring dashboard
- `POST /monitoring/run` — Run monitoring for all endpoints
- `POST /monitoring/test/{endpoint_id}` — Test single endpoint
- `POST /monitoring/configure/{endpoint_id}` — Set monitoring interval/timeout

#### Alerts
- `GET /alerts` — View active alerts
- `POST /alerts/resolve/{alert_id}` — Resolve alert
- `POST /alerts/endpoint/{endpoint_id}/threshold` — Set threshold
- `GET /alerts/endpoint/{endpoint_id}/thresholds` — Get thresholds

#### Logs
- `GET /logs` — View event logs (filter: ?event_type=DISCOVERY)
- `GET /logs/export` — Export logs to CSV

---

### Database

API Sentinel uses SQLite (`api_sentinel.db`) with tables for:
- `api_endpoints` — Discovered/managed API endpoints
- `monitoring_results` — Historical check results
- `alerts` — Current and resolved alerts
- `alert_thresholds` — Threshold configurations
- `monitoring_config` — Per-endpoint monitoring settings
- `event_logs` — Complete system event trail

---

### Project Microsite

For more information: https://sites.google.com/view/api-sentinel/home
