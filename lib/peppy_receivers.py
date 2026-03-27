#!/usr/bin/env python3
"""
PeppyMeter Remote Client - Audio data receivers.

UDP receivers for audio level and spectrum data from the PeppyMeter server.
"""

import socket
import struct
import threading
import time

from peppy_common import (
    __version__,
    DISCOVERY_PORT,
    log_client,
)

class LevelReceiver:
    """
    Receives audio level data over UDP.
    
    Protocol v2 features:
    - Sends registration packet to server on startup
    - Sends periodic heartbeat packets to maintain connection
    - Sends unregister packet on clean shutdown
    
    This allows the server to track connected clients for diagnostics
    while remaining backward compatible (server still broadcasts to all).
    """
    
    CLIENT_VERSION = 2  # Protocol version
    HEARTBEAT_INTERVAL = 30  # seconds between heartbeats
    
    def __init__(self, server_ip, port=5580, client_id=None, subscriptions=None,
                 discovery_listen_port=None, spectrum_listen_port=None, spectrum_default_port=5581):
        self.server_ip = server_ip
        self.port = port
        self.sock = None
        self._running = False
        self._thread = None
        self._heartbeat_thread = None
        # Optional ports for registration when multiple clients share one host (ephemeral UDP binds)
        self.discovery_listen_port = discovery_listen_port
        self.spectrum_listen_port = spectrum_listen_port
        self.spectrum_default_port = spectrum_default_port
        self.actual_listen_port = None
        
        # Generate unique client_id if not provided
        if client_id:
            self.client_id = client_id
        else:
            # Use hostname + random suffix for uniqueness
            hostname = socket.gethostname()
            import uuid
            suffix = uuid.uuid4().hex[:6]
            self.client_id = f"{hostname}-{suffix}"
        
        # What data streams this client subscribes to
        self.subscriptions = subscriptions or ['meters']
        
        # Current level data (thread-safe via GIL for simple reads)
        self.left = 0.0
        self.right = 0.0
        self.mono = 0.0
        self.seq = 0
        self.last_update = 0
    
    def _send_registration(self):
        """Send registration packet to server."""
        if not self.sock or not self.server_ip:
            return
        try:
            body = {
                'type': 'register',
                'client_id': self.client_id,
                'version': self.CLIENT_VERSION,
                'client_version': __version__,
                'subscribe': self.subscriptions,
            }
            if self.discovery_listen_port is not None and self.discovery_listen_port != DISCOVERY_PORT:
                body['discovery_port'] = self.discovery_listen_port
            if self.spectrum_listen_port is not None and self.spectrum_listen_port != self.spectrum_default_port:
                body['spectrum_listen_port'] = self.spectrum_listen_port
            msg = json.dumps(body).encode('utf-8')
            self.sock.sendto(msg, (self.server_ip, self.port))
            print(f"  Registered with server as '{self.client_id}' (v{self.CLIENT_VERSION})")
        except Exception as e:
            print(f"  Registration failed: {e}")
    
    def _send_heartbeat(self):
        """Send heartbeat packet to server."""
        if not self.sock or not self.server_ip:
            return
        try:
            msg = json.dumps({
                'type': 'heartbeat',
                'client_id': self.client_id
            }).encode('utf-8')
            self.sock.sendto(msg, (self.server_ip, self.port))
        except Exception:
            pass  # Heartbeat failures are silent
    
    def _send_unregister(self):
        """Send unregister packet to server on clean shutdown."""
        if not self.sock or not self.server_ip:
            return
        try:
            msg = json.dumps({
                'type': 'unregister',
                'client_id': self.client_id
            }).encode('utf-8')
            self.sock.sendto(msg, (self.server_ip, self.port))
        except Exception:
            pass  # Unregister failures are silent
    
    def _heartbeat_loop(self):
        """Background thread to send periodic heartbeats."""
        while self._running:
            time.sleep(self.HEARTBEAT_INTERVAL)
            if self._running:
                self._send_heartbeat()
    
    def start(self):
        """Start receiving level data in background thread."""
        if self._running:
            return
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        self.sock.settimeout(1.0)
        try:
            self.sock.bind(('', self.port))
        except OSError:
            self.sock.bind(('', 0))
        self.actual_listen_port = self.sock.getsockname()[1]
        if self.actual_listen_port != self.port:
            log_client(f"Level receiver using UDP port {self.actual_listen_port} (default {self.port} in use)", "verbose", "network")

        self._running = True

        # Send registration to server
        self._send_registration()

        # Start heartbeat thread
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

        # Start receive thread
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()
        print(f"Level receiver started on UDP port {self.actual_listen_port}")
    
    def _receive_loop(self):
        """Background thread to receive level data."""
        while self._running:
            try:
                data, addr = self.sock.recvfrom(1024)
                if len(data) == 16:  # uint32 + 3 floats
                    seq, left, right, mono = struct.unpack('<Ifff', data)
                    self.seq = seq
                    self.left = left
                    self.right = right
                    self.mono = mono
                    self.last_update = time.time()
            except socket.timeout:
                continue
            except OSError as e:
                # Windows raises WinError 10054 (WSAECONNRESET) on UDP sockets
                # when an ICMP port-unreachable is received after a send
                # (heartbeat or registration). This is harmless - continue.
                if getattr(e, 'winerror', None) == 10054:
                    continue
                if self._running:
                    print(f"Level receiver error: {e}")
                break
            except Exception as e:
                if self._running:
                    print(f"Level receiver error: {e}")
                break
    
    def stop(self):
        """Stop receiving."""
        # Send unregister before stopping
        self._send_unregister()
        
        self._running = False
        if self.sock:
            self.sock.close()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2.0)
    
    def get_levels(self):
        """Get current level data as tuple (left, right, mono)."""
        return (self.left, self.right, self.mono)


# =============================================================================
# Spectrum Data Receiver
# =============================================================================
class SpectrumReceiver:
    """
    Receives spectrum analyzer (FFT) data over UDP.
    
    Packet format (variable size, little-endian):
        - seq (uint32): Sequence number for loss detection
        - size (uint16): Number of frequency bins
        - bins (float32 * size): Frequency bin values (0-100)
    """
    
    def __init__(self, server_ip, port=5581):
        self.server_ip = server_ip
        self.port = port
        self.sock = None
        self.bound_port = None
        self._running = False
        self._thread = None
        
        # Current spectrum data (thread-safe via GIL for simple reads)
        self.seq = 0
        self.size = 0
        self.bins = []  # List of frequency bin values
        self.last_update = 0
        self._first_packet_logged = False

    def bind_socket(self):
        """Bind local UDP socket (call before LevelReceiver registration when ports must be coordinated)."""
        if self.sock is not None:
            return self.bound_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        self.sock.settimeout(1.0)
        try:
            self.sock.bind(('', self.port))
        except OSError:
            self.sock.bind(('', 0))
        self.bound_port = self.sock.getsockname()[1]
        if self.bound_port != self.port:
            log_client(f"Spectrum receiver using UDP port {self.bound_port} (default {self.port} in use)", "verbose", "network")
        return self.bound_port
    
    def start(self):
        """Start receiving spectrum data in background thread."""
        if self._running:
            return
        if self.sock is None:
            self.bind_socket()

        self._running = True
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()
        print(f"Spectrum receiver started on UDP port {self.bound_port}")
    
    def _receive_loop(self):
        """Background thread to receive spectrum data."""
        while self._running:
            try:
                data, addr = self.sock.recvfrom(1024)
                source_ip = addr[0]
                
                # FILTER: Only accept packets from our configured server
                # This prevents interference from other Volumio instances on the network
                if source_ip != self.server_ip:
                    continue
                
                if len(data) >= 6:  # Minimum: uint32 + uint16
                    # Unpack header
                    seq, size = struct.unpack('<IH', data[:6])
                    expected_len = 6 + (size * 4)  # header + bins (float32 each)
                    
                    if len(data) >= expected_len:
                        # Unpack bins
                        fmt = '<' + str(size) + 'f'
                        bins = list(struct.unpack(fmt, data[6:expected_len]))
                        
                        self.seq = seq
                        self.size = size
                        self.bins = bins
                        self.last_update = time.time()
                        
                        # Log first successful packet
                        if not self._first_packet_logged:
                            print(f"Spectrum receiver: first packet from {source_ip} - {size} bins")
                            log_client(f"Spectrum: first packet from {source_ip}, {size} bins", "basic")
                            self._first_packet_logged = True
                        
                        # Trace log each packet (high volume - only when trace_spectrum enabled)
                        log_client(f"Spectrum: seq={seq}, bins={size}, max={max(bins):.1f}", 
                                   "trace", "spectrum")
                            
            except socket.timeout:
                continue
            except OSError as e:
                # Windows raises WinError 10054 (WSAECONNRESET) on UDP sockets
                # when an ICMP port-unreachable is received after a send.
                # This is harmless on UDP - continue receiving.
                if getattr(e, 'winerror', None) == 10054:
                    continue
                if self._running:
                    print(f"Spectrum receiver error: {e}")
                break
            except Exception as e:
                if self._running:
                    print(f"Spectrum receiver error: {e}")
                break
    
    def stop(self):
        """Stop receiving."""
        self._running = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        self.bound_port = None
        if self._thread:
            self._thread.join(timeout=2.0)
    
    def get_data(self):
        """
        Get current spectrum data in the format PeppySpectrum expects.
        
        Returns raw bytes matching the pipe format (int32 per bin, little-endian).
        """
        if not self.bins:
            return None
        
        # Convert float bins back to int32 bytes (same format as pipe)
        result = bytearray()
        for val in self.bins:
            int_val = int(val) & 0xFFFFFFFF
            result.extend([
                int_val & 0xFF,
                (int_val >> 8) & 0xFF,
                (int_val >> 16) & 0xFF,
                (int_val >> 24) & 0xFF
            ])
        return bytes(result)
    
    def get_bins(self):
        """Get current spectrum bins as list of floats."""
        return self.bins.copy() if self.bins else []
    
    def has_data(self):
        """Check if we've received any spectrum data."""
        return self.last_update > 0


# =============================================================================
# Remote Data Source (for PeppyMeter integration)
# =============================================================================
class RemoteDataSource:
    """
    A DataSource implementation that gets data from the LevelReceiver.
    This mimics PeppyMeter's DataSource interface for seamless integration.
    """
    
    def __init__(self, level_receiver):
        self.level_receiver = level_receiver
        self.volume = 100  # Used by some meters
        self.data = (0.0, 0.0, 0.0)  # (left, right, mono)
    
    def start_data_source(self):
        """Start the data source (already running via LevelReceiver)."""
        pass
    
    def stop_data_source(self):
        """Stop the data source."""
        pass
    
    def get_current_data(self):
        """Return current data as tuple (left, right, mono)."""
        return (self.level_receiver.left, 
                self.level_receiver.right, 
                self.level_receiver.mono)
    
    def get_current_left_channel_data(self):
        return self.level_receiver.left
    
    def get_current_right_channel_data(self):
        return self.level_receiver.right
    
    def get_current_mono_channel_data(self):
        return self.level_receiver.mono
