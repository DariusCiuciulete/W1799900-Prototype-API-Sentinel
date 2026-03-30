"""
API Sentinel Database Module
Handles SQLite database operations for inventory and monitoring
"""
import sqlite3
from typing import List, Dict, Optional
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Database file path
DB_PATH = Path(__file__).resolve().parent.parent / "api_sentinel.db"


class Database:
    """Simple database manager for API Sentinel"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_PATH)
        self.init_database()
    
    def get_connection(self):
        """Open a connection to the SQLite database"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row  # Return rows as dictionaries
        return conn
    
    def init_database(self):
        """Initialize database with all required tables"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # API Endpoints Inventory Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS api_endpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                path TEXT NOT NULL,
                method TEXT NOT NULL,
                description TEXT,
                auth_type TEXT,
                is_internal BOOLEAN DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                discovery_source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(base_url, path, method)
            )
        ''')
        
        # Monitoring Results Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS monitoring_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint_id INTEGER NOT NULL,
                status_code INTEGER,
                response_time_ms REAL,
                success BOOLEAN,
                error_message TEXT,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (endpoint_id) REFERENCES api_endpoints (id) ON DELETE CASCADE
            )
        ''')
        
        # Alerts Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint_id INTEGER NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                threshold_value REAL,
                actual_value REAL,
                is_resolved BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP,
                FOREIGN KEY (endpoint_id) REFERENCES api_endpoints (id) ON DELETE CASCADE
            )
        ''')
        
        # Logs/Events Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS event_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                endpoint_id INTEGER,
                message TEXT NOT NULL,
                details TEXT,
                severity TEXT DEFAULT 'INFO',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (endpoint_id) REFERENCES api_endpoints (id) ON DELETE SET NULL
            )
        ''')
        
        # Monitoring Configuration Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS monitoring_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint_id INTEGER NOT NULL UNIQUE,
                check_interval_seconds INTEGER DEFAULT 300,
                timeout_seconds INTEGER DEFAULT 30,
                latency_threshold_ms REAL DEFAULT 1000,
                error_rate_threshold REAL DEFAULT 0.1,
                auth_type TEXT DEFAULT 'none',
                auth_value TEXT,
                auth_header_name TEXT DEFAULT 'X-API-Key',
                enabled BOOLEAN DEFAULT 1,
                last_check TIMESTAMP,
                FOREIGN KEY (endpoint_id) REFERENCES api_endpoints (id) ON DELETE CASCADE
            )
        ''')

        # Keep existing databases compatible by adding new auth columns if missing.
        self._ensure_monitoring_config_auth_columns(cursor)
        
        # Alert Thresholds Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alert_thresholds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint_id INTEGER,
                threshold_type TEXT NOT NULL,
                threshold_value REAL NOT NULL,
                comparison TEXT DEFAULT 'greater_than',
                enabled BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (endpoint_id) REFERENCES api_endpoints (id) ON DELETE CASCADE
            )
        ''')

        # Service Monitoring Configuration Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS service_monitoring_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_name TEXT NOT NULL UNIQUE,
                check_interval_seconds INTEGER DEFAULT 300,
                timeout_seconds INTEGER DEFAULT 30,
                latency_threshold_ms REAL DEFAULT 1000,
                error_rate_threshold REAL DEFAULT 10,
                enabled BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")

    def _ensure_monitoring_config_auth_columns(self, cursor):
        """Add auth columns to monitoring_config for older database files."""
        cursor.execute("PRAGMA table_info(monitoring_config)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'auth_type' not in existing_columns:
            cursor.execute("ALTER TABLE monitoring_config ADD COLUMN auth_type TEXT DEFAULT 'none'")
        if 'auth_value' not in existing_columns:
            cursor.execute("ALTER TABLE monitoring_config ADD COLUMN auth_value TEXT")
        if 'auth_header_name' not in existing_columns:
            cursor.execute("ALTER TABLE monitoring_config ADD COLUMN auth_header_name TEXT DEFAULT 'X-API-Key'")
    
    # ==================== API Endpoints CRUD ====================
    
    def add_endpoint(self, service_name: str, base_url: str, path: str, 
                     method: str, description: str = None, auth_type: str = None,
                     is_internal: bool = False, discovery_source: str = None) -> int:
        """Add a new API endpoint to inventory"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO api_endpoints 
                (service_name, base_url, path, method, description, auth_type, is_internal, discovery_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (service_name, base_url, path, method, description, auth_type, is_internal, discovery_source))
            
            endpoint_id = cursor.lastrowid
            conn.commit()

            service_config = self.get_service_monitoring_config(service_name)
            if service_config:
                self.set_monitoring_config(
                    endpoint_id=endpoint_id,
                    check_interval_seconds=service_config['check_interval_seconds'],
                    timeout_seconds=service_config['timeout_seconds'],
                    latency_threshold_ms=service_config['latency_threshold_ms'],
                    error_rate_threshold=service_config['error_rate_threshold'],
                    enabled=bool(service_config['enabled'])
                )
                self.set_alert_threshold(endpoint_id, 'latency', service_config['latency_threshold_ms'])
                self.set_alert_threshold(endpoint_id, 'error_rate', service_config['error_rate_threshold'])
                self.set_alert_threshold(endpoint_id, 'availability', 1)
            
            logger.info(f"Added endpoint: {service_name} - {method} {path}")
            return endpoint_id
            
        except sqlite3.IntegrityError:
            # Endpoint already exists, update it instead
            cursor.execute('''
                UPDATE api_endpoints 
                SET service_name = ?, description = ?, auth_type = ?, 
                    is_internal = ?, discovery_source = ?, updated_at = CURRENT_TIMESTAMP
                WHERE base_url = ? AND path = ? AND method = ?
            ''', (service_name, description, auth_type, is_internal, discovery_source, base_url, path, method))
            
            cursor.execute('''
                SELECT id FROM api_endpoints 
                WHERE base_url = ? AND path = ? AND method = ?
            ''', (base_url, path, method))
            
            endpoint_id = cursor.fetchone()[0]
            conn.commit()

            service_config = self.get_service_monitoring_config(service_name)
            if service_config:
                self.set_monitoring_config(
                    endpoint_id=endpoint_id,
                    check_interval_seconds=service_config['check_interval_seconds'],
                    timeout_seconds=service_config['timeout_seconds'],
                    latency_threshold_ms=service_config['latency_threshold_ms'],
                    error_rate_threshold=service_config['error_rate_threshold'],
                    enabled=bool(service_config['enabled'])
                )
                self.set_alert_threshold(endpoint_id, 'latency', service_config['latency_threshold_ms'])
                self.set_alert_threshold(endpoint_id, 'error_rate', service_config['error_rate_threshold'])
                self.set_alert_threshold(endpoint_id, 'availability', 1)
            
            self.log_event("DISCOVERY", endpoint_id, 
                          f"Endpoint updated: {method} {path}", 
                          f"Source: {discovery_source}")
            
            logger.info(f"Updated existing endpoint: {service_name} - {method} {path}")
            return endpoint_id
        finally:
            conn.close()
    
    def get_all_endpoints(self, active_only: bool = False) -> List[Dict]:
        """Get all API endpoints"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query = "SELECT * FROM api_endpoints"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY service_name, path"
        
        cursor.execute(query)
        endpoints = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return endpoints
    
    def get_endpoint_by_id(self, endpoint_id: int) -> Optional[Dict]:
        """Get endpoint by ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM api_endpoints WHERE id = ?", (endpoint_id,))
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None
    
    def update_endpoint(self, endpoint_id: int, **kwargs) -> bool:
        """Update endpoint - only update fields that were provided"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Build update statement with only provided fields
        updates = []
        values = []
        
        if 'service_name' in kwargs:
            updates.append("service_name = ?")
            values.append(kwargs['service_name'])
        if 'base_url' in kwargs:
            updates.append("base_url = ?")
            values.append(kwargs['base_url'])
        if 'path' in kwargs:
            updates.append("path = ?")
            values.append(kwargs['path'])
        if 'method' in kwargs:
            updates.append("method = ?")
            values.append(kwargs['method'])
        if 'description' in kwargs:
            updates.append("description = ?")
            values.append(kwargs['description'])
        if 'auth_type' in kwargs:
            updates.append("auth_type = ?")
            values.append(kwargs['auth_type'])
        if 'is_internal' in kwargs:
            updates.append("is_internal = ?")
            values.append(kwargs['is_internal'])
        if 'is_active' in kwargs:
            updates.append("is_active = ?")
            values.append(kwargs['is_active'])
        
        # If nothing to update, return False
        if not updates:
            conn.close()
            return False
        
        # Always update the timestamp
        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.append(endpoint_id)
        
        # Execute the update
        query = f"UPDATE api_endpoints SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, values)
        
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        if success:
            self.log_event("INVENTORY", endpoint_id, "Endpoint updated")
        
        return success
    
    def delete_endpoint(self, endpoint_id: int) -> bool:
        """Delete an endpoint"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM api_endpoints WHERE id = ?", (endpoint_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        if success:
            self.log_event(
                "INVENTORY",
                None,
                f"Endpoint deleted (ID: {endpoint_id})",
                f"endpoint_id={endpoint_id}",
                "INFO"
            )
        
        return success
    
    # ==================== Monitoring Results ====================
    
    def add_monitoring_result(self, endpoint_id: int, status_code: int = None,
                             response_time_ms: float = None, success: bool = True,
                             error_message: str = None) -> int:
        """Add a monitoring result"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO monitoring_results 
            (endpoint_id, status_code, response_time_ms, success, error_message)
            VALUES (?, ?, ?, ?, ?)
        ''', (endpoint_id, status_code, response_time_ms, success, error_message))
        
        result_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return result_id
    
    def get_monitoring_results(self, endpoint_id: int = None, limit: int = 100) -> List[Dict]:
        """Get monitoring results"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        if endpoint_id:
            cursor.execute('''
                SELECT * FROM monitoring_results 
                WHERE endpoint_id = ? 
                ORDER BY checked_at DESC 
                LIMIT ?
            ''', (endpoint_id, limit))
        else:
            cursor.execute('''
                SELECT * FROM monitoring_results 
                ORDER BY checked_at DESC 
                LIMIT ?
            ''', (limit,))
        
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return results
    
    def get_monitoring_stats(self) -> Dict:
        """Get overall monitoring statistics"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                COUNT(*) as total_checks,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful_checks,
                SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failed_checks,
                AVG(response_time_ms) as avg_response_time,
                MAX(checked_at) as last_check
            FROM monitoring_results
            WHERE checked_at >= datetime('now', '-24 hours')
        ''')
        
        row = cursor.fetchone()
        conn.close()
        
        if row and row['total_checks'] > 0:
            return {
                'total_checks': row['total_checks'],
                'successful_checks': row['successful_checks'],
                'failed_checks': row['failed_checks'],
                'availability': round((row['successful_checks'] / row['total_checks']) * 100, 2),
                'avg_response_time': round(row['avg_response_time'], 2) if row['avg_response_time'] else 0,
                'last_check': row['last_check']
            }
        
        return {
            'total_checks': 0,
            'successful_checks': 0,
            'failed_checks': 0,
            'availability': 0,
            'avg_response_time': 0,
            'last_check': None
        }
    
    # ==================== Alerts ====================
    
    def create_alert(self, endpoint_id: int, alert_type: str, severity: str,
                    message: str, threshold_value: float = None, 
                    actual_value: float = None) -> int:
        """Create a new alert for an endpoint"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO alerts 
            (endpoint_id, alert_type, severity, message, threshold_value, actual_value)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (endpoint_id, alert_type, severity, message, threshold_value, actual_value))
        
        alert_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        logger.info(f"Alert created for endpoint {endpoint_id}: {alert_type}")
        return alert_id
    
    def get_active_alerts(self, endpoint_id: int = None) -> List[Dict]:
        """Get unresolved alerts"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        if endpoint_id:
            cursor.execute('''
                SELECT * FROM alerts 
                WHERE is_resolved = 0 AND endpoint_id = ?
                ORDER BY created_at DESC
            ''', (endpoint_id,))
        else:
            cursor.execute('''
                SELECT * FROM alerts 
                WHERE is_resolved = 0
                ORDER BY created_at DESC
            ''')
        
        alerts = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return alerts
    
    def resolve_alert(self, alert_id: int) -> bool:
        """Mark an alert as resolved"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE alerts 
            SET is_resolved = 1, resolved_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (alert_id,))
        
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        if success:
            logger.info(f"Alert {alert_id} resolved")
        
        return success
    
    # ==================== Alert Thresholds ====================
    
    def set_alert_threshold(self, endpoint_id: int, threshold_type: str, 
                           threshold_value: float) -> int:
        """Set or update an alert threshold for an endpoint"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Check if threshold already exists
        cursor.execute('''
            SELECT id FROM alert_thresholds 
            WHERE endpoint_id = ? AND threshold_type = ?
        ''', (endpoint_id, threshold_type))
        
        existing = cursor.fetchone()
        
        if existing:
            # Update existing
            cursor.execute('''
                UPDATE alert_thresholds 
                SET threshold_value = ?, enabled = 1
                WHERE endpoint_id = ? AND threshold_type = ?
            ''', (threshold_value, endpoint_id, threshold_type))
            threshold_id = existing[0]
        else:
            # Insert new
            cursor.execute('''
                INSERT INTO alert_thresholds 
                (endpoint_id, threshold_type, threshold_value, comparison, enabled)
                VALUES (?, ?, ?, ?, ?)
            ''', (endpoint_id, threshold_type, threshold_value, 'greater_than', 1))
            threshold_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        logger.info(f"Threshold set for endpoint {endpoint_id}: {threshold_type} = {threshold_value}")
        return threshold_id
    
    def get_alert_thresholds(self, endpoint_id: int) -> List[Dict]:
        """Get all thresholds for an endpoint"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM alert_thresholds 
            WHERE endpoint_id = ? AND enabled = 1
            ORDER BY threshold_type
        ''', (endpoint_id,))
        
        thresholds = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return thresholds
    
    def check_and_trigger_alerts(self, endpoint_id: int, result: dict) -> List[int]:
        """
        Check if monitoring result breaches any thresholds.
        Create alerts if thresholds are exceeded.
        Returns: list of alert IDs created
        """
        alerts_created = []
        thresholds = self.get_alert_thresholds(endpoint_id)
        
        if not thresholds:
            return alerts_created
        
        # Get the endpoint info for context
        endpoint = self.get_endpoint_by_id(endpoint_id)
        
        for threshold in thresholds:
            threshold_type = threshold['threshold_type']
            threshold_value = threshold['threshold_value']
            
            # Determine alert based on threshold type
            should_alert = False
            actual_value = None
            severity = "MEDIUM"
            message = ""
            
            if threshold_type == "latency" and result.get('response_time_ms'):
                actual_value = result['response_time_ms']
                if actual_value > threshold_value:
                    should_alert = True
                    message = f"Response time {actual_value:.0f}ms exceeds threshold {threshold_value}ms"
                    severity = "HIGH" if actual_value > threshold_value * 2 else "MEDIUM"
            
            elif threshold_type == "availability" and result.get('success') is not None:
                # For availability: if endpoint fails, trigger alert
                if result['success'] == False:
                    should_alert = True
                    actual_value = 0  # Down
                    message = f"Endpoint is down: {result.get('error', 'No response')}"
                    severity = "CRITICAL"
            
            elif threshold_type == "error_rate":
                # Check error rate from recent monitoring results
                recent_results = self.get_monitoring_results(endpoint_id=endpoint_id, limit=10)
                if recent_results:
                    failures = sum(1 for r in recent_results if r['success'] == 0)
                    error_rate = (failures / len(recent_results)) * 100
                    actual_value = error_rate
                    if error_rate > threshold_value:
                        should_alert = True
                        message = f"Error rate {error_rate:.1f}% exceeds threshold {threshold_value}%"
                        severity = "HIGH"
            
            # Create alert if threshold is breached
            if should_alert:
                # Check if an unresolved alert already exists for this endpoint/type
                existing = self.get_active_alerts(endpoint_id)
                alert_exists = any(a['alert_type'] == threshold_type for a in existing)
                
                if not alert_exists:
                    alert_id = self.create_alert(
                        endpoint_id=endpoint_id,
                        alert_type=threshold_type,
                        severity=severity,
                        message=message,
                        threshold_value=threshold_value,
                        actual_value=actual_value
                    )
                    alerts_created.append(alert_id)
                    self.log_event(
                        "ALERT",
                        endpoint_id,
                        f"Alert triggered: {threshold_type}",
                        f"service={endpoint['service_name'] if endpoint else 'unknown'}; "
                        f"endpoint_id={endpoint_id}; "
                        f"endpoint={endpoint['method'] if endpoint else ''} {endpoint['path'] if endpoint else ''}; "
                        f"threshold_type={threshold_type}; threshold_value={threshold_value}; "
                        f"actual_value={actual_value}; reason={message}",
                        severity
                    )
        
        return alerts_created
    
    # ==================== Logging ====================
    
    def log_event(self, event_type: str, endpoint_id: int = None, 
                  message: str = "", details: str = None, severity: str = "INFO") -> int:
        """Log an event to the event_logs table"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO event_logs 
            (event_type, endpoint_id, message, details, severity)
            VALUES (?, ?, ?, ?, ?)
        ''', (event_type, endpoint_id, message, details, severity))
        
        log_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return log_id
    
    def get_logs(self, event_type: str = None, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Get event logs, optionally filtered by type"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        if event_type:
            cursor.execute('''
                SELECT * FROM event_logs 
                WHERE event_type = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            ''', (event_type, limit, offset))
        else:
            cursor.execute('''
                SELECT * FROM event_logs 
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            ''', (limit, offset))
        
        logs = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return logs

    def get_logs_count(self, event_type: str = None) -> int:
        """Get total number of logs, optionally filtered by event type"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if event_type:
            cursor.execute('''
                SELECT COUNT(*) as total
                FROM event_logs
                WHERE event_type = ?
            ''', (event_type,))
        else:
            cursor.execute('''
                SELECT COUNT(*) as total
                FROM event_logs
            ''')

        total = cursor.fetchone()['total']
        conn.close()
        return total
    
    # ==================== Monitoring Configuration ====================

    def get_services(self) -> List[Dict]:
        """Get services with endpoint counts"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                service_name,
                COUNT(*) as endpoint_count,
                SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active_endpoint_count
            FROM api_endpoints
            GROUP BY service_name
            ORDER BY service_name
        ''')

        services = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return services

    def get_service_endpoints(self, service_name: str, active_only: bool = True) -> List[Dict]:
        """Get endpoints for one service"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if active_only:
            cursor.execute('''
                SELECT * FROM api_endpoints
                WHERE service_name = ? AND is_active = 1
                ORDER BY path, method
            ''', (service_name,))
        else:
            cursor.execute('''
                SELECT * FROM api_endpoints
                WHERE service_name = ?
                ORDER BY path, method
            ''', (service_name,))

        endpoints = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return endpoints

    def set_service_monitoring_config(self, service_name: str,
                                     check_interval_seconds: int = 300,
                                     timeout_seconds: int = 30,
                                     latency_threshold_ms: float = 1000,
                                     error_rate_threshold: float = 10,
                                     enabled: bool = True) -> int:
        """Set service-level monitoring config and apply it to service endpoints"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO service_monitoring_config
            (service_name, check_interval_seconds, timeout_seconds,
             latency_threshold_ms, error_rate_threshold, enabled, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(service_name) DO UPDATE SET
                check_interval_seconds = excluded.check_interval_seconds,
                timeout_seconds = excluded.timeout_seconds,
                latency_threshold_ms = excluded.latency_threshold_ms,
                error_rate_threshold = excluded.error_rate_threshold,
                enabled = excluded.enabled,
                updated_at = CURRENT_TIMESTAMP
        ''', (service_name, check_interval_seconds, timeout_seconds,
              latency_threshold_ms, error_rate_threshold, enabled))

        config_id = cursor.lastrowid
        conn.commit()
        conn.close()

        self.apply_service_config_to_endpoints(service_name)
        return config_id

    def get_service_monitoring_config(self, service_name: str) -> Optional[Dict]:
        """Get one service-level monitoring config"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM service_monitoring_config
            WHERE service_name = ?
        ''', (service_name,))

        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_service_monitoring_configs(self, enabled_only: bool = False) -> List[Dict]:
        """Get all service-level monitoring configs"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if enabled_only:
            cursor.execute('''
                SELECT * FROM service_monitoring_config
                WHERE enabled = 1
                ORDER BY service_name
            ''')
        else:
            cursor.execute('''
                SELECT * FROM service_monitoring_config
                ORDER BY service_name
            ''')

        configs = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return configs

    def apply_service_config_to_endpoints(self, service_name: str) -> int:
        """Apply one service config to all active endpoints in that service"""
        config = self.get_service_monitoring_config(service_name)
        if not config:
            return 0

        endpoints = self.get_service_endpoints(service_name, active_only=True)
        updated_count = 0

        for endpoint in endpoints:
            endpoint_id = endpoint['id']

            self.set_monitoring_config(
                endpoint_id=endpoint_id,
                check_interval_seconds=config['check_interval_seconds'],
                timeout_seconds=config['timeout_seconds'],
                latency_threshold_ms=config['latency_threshold_ms'],
                error_rate_threshold=config['error_rate_threshold'],
                enabled=bool(config['enabled'])
            )

            self.set_alert_threshold(endpoint_id, 'latency', config['latency_threshold_ms'])
            self.set_alert_threshold(endpoint_id, 'error_rate', config['error_rate_threshold'])
            self.set_alert_threshold(endpoint_id, 'availability', 1)

            updated_count += 1

        return updated_count

    def get_due_endpoints_for_auto_monitoring(self, service_name: str) -> List[Dict]:
        """Get active endpoints in a service that are due for monitoring"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT e.*
            FROM api_endpoints e
            INNER JOIN monitoring_config m ON m.endpoint_id = e.id
            INNER JOIN service_monitoring_config s ON s.service_name = e.service_name
            WHERE e.is_active = 1
              AND e.service_name = ?
              AND s.enabled = 1
              AND m.enabled = 1
              AND (
                    m.last_check IS NULL
                    OR m.last_check <= datetime('now', '-' || m.check_interval_seconds || ' seconds')
                  )
            ORDER BY e.id
        ''', (service_name,))

        endpoints = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return endpoints

    def update_last_check(self, endpoint_id: int) -> None:
        """Update last_check time for endpoint monitoring config"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE monitoring_config
            SET last_check = CURRENT_TIMESTAMP
            WHERE endpoint_id = ?
        ''', (endpoint_id,))

        conn.commit()
        conn.close()
    
    def set_monitoring_config(self, endpoint_id: int, check_interval_seconds: int = 300,
                             timeout_seconds: int = 30, latency_threshold_ms: float = 1000,
                             error_rate_threshold: float = 10, enabled: bool = True,
                             auth_type: str = 'none', auth_value: str = None,
                             auth_header_name: str = 'X-API-Key') -> int:
        """Set monitoring configuration for an endpoint"""
        conn = self.get_connection()
        cursor = conn.cursor()

        allowed_auth_types = ['none', 'bearer', 'api_key']
        if auth_type not in allowed_auth_types:
            auth_type = 'none'

        if auth_type == 'none':
            auth_value = None

        if auth_type != 'api_key':
            auth_header_name = 'X-API-Key'

        if not auth_header_name:
            auth_header_name = 'X-API-Key'
        
        cursor.execute('''
            INSERT INTO monitoring_config
            (endpoint_id, check_interval_seconds, timeout_seconds,
             latency_threshold_ms, error_rate_threshold, auth_type,
             auth_value, auth_header_name, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(endpoint_id) DO UPDATE SET
                check_interval_seconds = excluded.check_interval_seconds,
                timeout_seconds = excluded.timeout_seconds,
                latency_threshold_ms = excluded.latency_threshold_ms,
                error_rate_threshold = excluded.error_rate_threshold,
                auth_type = excluded.auth_type,
                auth_value = excluded.auth_value,
                auth_header_name = excluded.auth_header_name,
                enabled = excluded.enabled
        ''', (endpoint_id, check_interval_seconds, timeout_seconds,
              latency_threshold_ms, error_rate_threshold, auth_type,
              auth_value, auth_header_name, enabled))
        
        config_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return config_id
    
    def get_monitoring_config(self, endpoint_id: int) -> Optional[Dict]:
        """Get monitoring configuration for an endpoint"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM monitoring_config WHERE endpoint_id = ?
        ''', (endpoint_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None
    
    # ==================== Dashboard Statistics ====================
    
    def get_dashboard_stats(self) -> Dict:
        """Get statistics for dashboard"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Total APIs and endpoints
        cursor.execute('''
            SELECT 
                COUNT(DISTINCT service_name) as total_apis,
                COUNT(*) as total_endpoints,
                SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active_endpoints
            FROM api_endpoints
        ''')
        inventory_stats = dict(cursor.fetchone())
        
        # Get monitoring stats
        monitoring_stats = self.get_monitoring_stats()
        
        conn.close()
        
        return {
            'total_apis': inventory_stats['total_apis'],
            'total_endpoints': inventory_stats['total_endpoints'],
            'active_endpoints': inventory_stats['active_endpoints'],
            'avg_response_time': monitoring_stats['avg_response_time']
        }


# Global database instance
db = Database()
