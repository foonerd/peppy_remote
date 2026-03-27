#!/usr/bin/env python3
"""
PeppyMeter Remote Client - Network services.

Server discovery, config version listening, and config fetching.
"""

import json
import socket
import struct
import threading
import time
import urllib.request
import urllib.error

from peppy_common import (
    DISCOVERY_PORT,
    DISCOVERY_TIMEOUT,
    _norm_str,
    log_client,
)

class ServerDiscovery:
    """Discovers PeppyMeter servers via UDP broadcast."""
    
    def __init__(self, port=DISCOVERY_PORT, timeout=DISCOVERY_TIMEOUT):
        self.port = port
        self.timeout = timeout
        self.servers = {}  # {ip: discovery_data}
        self._stop = False
    
    def discover(self):
        """Listen for server announcements, return dict of discovered servers."""
        print(f"Discovering PeppyMeter servers on UDP port {self.port}...")
        log_client(f"Discovery: listening on UDP port {self.port}, timeout={self.timeout}s", "verbose")
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)  # 1 second timeout for each recv
        sock.bind(('', self.port))
        
        start_time = time.time()
        
        while not self._stop and (time.time() - start_time) < self.timeout:
            try:
                data, addr = sock.recvfrom(1024)
                try:
                    info = json.loads(data.decode('utf-8', errors='replace'))
                    if info.get('service') == 'peppy_level_server':
                        ip = addr[0]
                        if ip not in self.servers:
                            hostname = info.get('hostname', ip)
                            print(f"  Found: {hostname} ({ip})")
                            log_client(f"Discovery: found server {hostname} ({ip})", "basic")
                            log_client(f"Discovery: server info: {info}", "verbose")
                            self.servers[ip] = {
                                'ip': ip,
                                'hostname': hostname,
                                'level_port': info.get('level_port', 5580),
                                'spectrum_port': info.get('spectrum_port', 5581),
                                'volumio_port': info.get('volumio_port', 3000),
                                'version': info.get('version', 1),
                                'config_version': info.get('config_version', ''),
                                'plugin_version': (info.get('plugin_version') or '').strip(),
                            }
                        else:
                            # Update config_version if changed
                            new_version = info.get('config_version', '')
                            if new_version and new_version != self.servers[ip].get('config_version'):
                                self.servers[ip]['config_version'] = new_version
                                log_client(f"Discovery: config version updated to {new_version}", "verbose")
                            pv = (info.get('plugin_version') or '').strip()
                            if pv and pv != self.servers[ip].get('plugin_version'):
                                self.servers[ip]['plugin_version'] = pv
                                log_client(f"Discovery: plugin_version updated to {pv}", "verbose")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    log_client(f"Discovery: invalid packet from {addr}", "trace", "network")
                    pass
            except socket.timeout:
                continue
            except Exception as e:
                print(f"  Discovery error: {e}")
                log_client(f"Discovery: error {e}", "verbose")
                break
        
        sock.close()
        log_client(f"Discovery: found {len(self.servers)} servers", "verbose")
        return self.servers
    
    def stop(self):
        self._stop = True


# =============================================================================
# Config Version Listener (UDP) - detect server config/template changes
# =============================================================================
class ConfigVersionListener(threading.Thread):
    """
    Listens for UDP discovery packets from the PeppyMeter server and sets
    reload_requested when config_version or active_meter in a packet differs
    from the client's current values (so the client can reload config/templates).
    
    Protocol version 3+ includes active_meter for random meter sync.
    """
    def __init__(self, port, current_version_holder, server_ip=None):
        super().__init__(daemon=True)
        self.port = port
        self.current_version_holder = current_version_holder  # dict with 'version' and 'active_meter' keys
        self.server_ip = server_ip  # if set, only accept packets from this IP
        self.reload_requested = False
        self.new_active_meter = None  # Set when active_meter changes (for config update)
        self.first_announcement_received = False  # True after first valid UDP packet (for sync screen)
        self._stop = False
        self._sock = None
        self.bound_port = None  # Actual local UDP port (may differ if multiple clients on one host)
        self._bound_event = threading.Event()
        self.ignore_active_meter = False  # True in kiosk mode: skip server active_meter changes

    def wait_until_bound(self, timeout=5.0):
        """Wait for bind attempt to finish (success or failure). Returns True if bound to a port."""
        self._bound_event.wait(timeout)
        return self.bound_port is not None

    def run(self):
        self._bound_event.clear()
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, 'SO_REUSEPORT'):
                try:
                    self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            self._sock.settimeout(1.0)
            try:
                self._sock.bind(('', self.port))
            except OSError:
                self._sock.bind(('', 0))
            self.bound_port = self._sock.getsockname()[1]
            if self.bound_port != self.port:
                log_client(f"Discovery listener using UDP port {self.bound_port} (default {self.port} in use)", "verbose", "network")
        except OSError as e:
            print(f"  ConfigVersionListener: could not bind to port {self.port}: {e}")
            self.bound_port = None
        finally:
            self._bound_event.set()
        if self.bound_port is None:
            return
        while not self._stop:
            try:
                data, addr = self._sock.recvfrom(1024)
                if self.server_ip is not None and addr[0] != self.server_ip:
                    continue
                try:
                    info = json.loads(data.decode('utf-8', errors='replace'))
                    if info.get('service') != 'peppy_level_server':
                        continue
                    
                    # Mark that we've received at least one announcement (for "waiting for server" screen)
                    self.first_announcement_received = True
                    if info.get('active_meter'):
                        self.new_active_meter = _norm_str(info.get('active_meter'))
                    
                    # Check config_version change (file-based config changes)
                    new_version = info.get('config_version', '')
                    if new_version:
                        current = self.current_version_holder.get('version', '')
                        if _norm_str(new_version) != _norm_str(current):
                            self.reload_requested = True
                    
                    # Check active_meter change (random meter sync, protocol v3+)
                    # In kiosk mode (ignore_active_meter=True), server active_meter
                    # broadcasts are irrelevant - the client picks its own random meter.
                    if not self.ignore_active_meter:
                        new_meter = info.get('active_meter', '')
                        if new_meter:
                            current_meter = self.current_version_holder.get('active_meter', '')
                            if _norm_str(new_meter) != _norm_str(current_meter):
                                # Active meter changed - trigger reload with new meter name
                                self.new_active_meter = _norm_str(new_meter)
                                self.reload_requested = True
                            
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            except socket.timeout:
                continue
            except OSError:
                if self._stop:
                    break
                raise
        try:
            self._sock.close()
        except Exception:
            pass
        self._sock = None

    def stop_listener(self):
        self._stop = True
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass


# =============================================================================
# Config Fetcher (HTTP)
# =============================================================================
class ConfigFetcher:
    """
    Fetches config.txt from server via HTTP.
    
    Uses Volumio plugin API endpoint to get config without SMB symlink issues.
    The server IP address from discovery is used for robust connectivity.
    """
    
    def __init__(self, server_ip, volumio_port=3000):
        self.server_ip = server_ip
        self.volumio_port = volumio_port
        self.cached_config = None
        self.cached_version = None
        self.cached_plugin_version = None  # PeppyMeter Screensaver release (package.json)
        # Persist countdown settings from server
        self.persist_duration = 0  # 0 = disabled
        self.persist_display = "freeze"
    
    def fetch(self):
        """
        Fetch config from server via HTTP.
        
        Returns (success, config_content, version) tuple.
        """
        # Use direct IP address for reliable connectivity
        url = f"http://{self.server_ip}:{self.volumio_port}/api/v1/pluginEndpoint?endpoint=peppy_screensaver&method=getRemoteConfig"
        
        try:
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8', errors='replace'))
                
                # Volumio REST API wraps plugin response in 'data' field
                # Response format: {"success": true, "data": {"success": true, "version": "...", "config": "..."}}
                if data.get('success'):
                    inner = data.get('data', {})
                    if inner.get('success'):
                        self.cached_config = inner.get('config', '')
                        self.cached_version = inner.get('version', '')
                        self.cached_plugin_version = inner.get('plugin_version')
                        # Extract persist settings from server
                        self.persist_duration = int(inner.get('persist_duration', 0) or 0)
                        self.persist_display = inner.get('persist_display', 'freeze') or 'freeze'
                        return True, self.cached_config, self.cached_version
                    else:
                        error = inner.get('error', 'Unknown error')
                        print(f"  Plugin error fetching config: {error}")
                        return False, None, None
                else:
                    error = data.get('error', 'Unknown error')
                    print(f"  Server error fetching config: {error}")
                    return False, None, None
                    
        except urllib.error.HTTPError as e:
            print(f"  HTTP error fetching config: {e.code} {e.reason}")
            return False, None, None
        except urllib.error.URLError as e:
            print(f"  URL error fetching config: {e.reason}")
            return False, None, None
        except json.JSONDecodeError as e:
            print(f"  JSON error parsing config response: {e}")
            return False, None, None
        except Exception as e:
            print(f"  Error fetching config: {e}")
            return False, None, None
    
    def has_changed(self, new_version):
        """Check if config version has changed."""
        return new_version and new_version != self.cached_version
