#!/usr/bin/env python3
"""
PeppyMeter Remote Client - Persist manager.

Manages the persist countdown display file for pause/stop behavior.
"""

import os
import threading
import time

from peppy_common import (
    PERSIST_FILE,
    log_client,
)

class PersistManager:
    """
    Manages the persist countdown file for remote clients.
    
    On the server, Node.js creates /tmp/peppy_persist when playback stops
    with persist mode enabled. The Python render code (volumio_turntable.py)
    reads this file to show countdown.
    
    On the client, there is no Node.js - so we must manage the file ourselves,
    mirroring the server's behavior based on socket.io playback status events.
    
    Usage: Call check_metadata_status() on each render frame to monitor changes.
    """
    
    def __init__(self, persist_duration=0, persist_display="freeze"):
        """
        :param persist_duration: Countdown duration in seconds (0 = disabled)
        :param persist_display: Display mode ("freeze" or "countdown")
        """
        self.persist_duration = persist_duration
        self.persist_display = persist_display
        self._persist_timer = None
        self._last_status = None
    
    def update_settings(self, persist_duration, persist_display):
        """Update persist settings (e.g., when server config changes)."""
        new_duration = int(persist_duration or 0)
        new_display = persist_display or "freeze"
        
        # Only log if settings actually changed
        if new_duration != self.persist_duration or new_display != self.persist_display:
            self.persist_duration = new_duration
            self.persist_display = new_display
            log_client(f"Persist settings updated: duration={self.persist_duration}s, mode={self.persist_display}", "verbose")
    
    def check_metadata_status(self, metadata_dict):
        """
        Check metadata dict for status changes and handle persist file accordingly.
        
        Call this on each render frame to detect status changes.
        
        :param metadata_dict: Shared metadata dict with 'status' and 'volatile' keys
        """
        status = (metadata_dict.get("status", "") or "").lower()
        volatile = metadata_dict.get("volatile", False) or False
        
        if status != self._last_status:
            self._on_status_change(status, volatile)
    
    def _on_status_change(self, status, volatile=False):
        """
        Handle playback status changes.
        
        Mirrors the server's Node.js logic:
        - On 'play': Remove persist file and cancel timer
        - On 'stop'/'pause' (non-volatile): Start persist countdown if enabled
        
        :param status: Playback status ("play", "pause", "stop")
        :param volatile: If True, status change is transitional (skip processing)
        """
        # Ignore volatile state changes (track transitions)
        if volatile and status in ("stop", "pause"):
            return
        
        if status == "play":
            # Playback resumed - remove persist file and cancel timer
            self._cancel_timer()
            self._remove_persist_file()
            log_client("Persist: playback resumed, file removed", "trace", "persist")
        
        elif status in ("stop", "pause") and self._last_status == "play":
            # Playback stopped/paused - start persist countdown if enabled
            if self.persist_duration > 0:
                self._start_persist_countdown()
            else:
                self._remove_persist_file()
        
        self._last_status = status
    
    def _start_persist_countdown(self):
        """Create persist file and start expiration timer."""
        import time as time_module
        import threading
        
        # Cancel any existing timer
        self._cancel_timer()
        
        # Write persist file (format: duration:timestamp_ms:display_mode)
        timestamp_ms = int(time_module.time() * 1000)
        content = f"{self.persist_duration}:{timestamp_ms}:{self.persist_display}"
        
        try:
            with open(PERSIST_FILE, 'w') as f:
                f.write(content)
            log_client(f"Persist: started countdown ({self.persist_duration}s, mode={self.persist_display})", "verbose")
        except Exception as e:
            log_client(f"Persist: failed to write file: {e}", "debug")
            return
        
        # Start timer to remove file after persist_duration
        def expire_persist():
            self._remove_persist_file()
            log_client("Persist: timer expired, file removed", "verbose")
            self._persist_timer = None
        
        self._persist_timer = threading.Timer(self.persist_duration, expire_persist)
        self._persist_timer.daemon = True
        self._persist_timer.start()
    
    def _cancel_timer(self):
        """Cancel persist expiration timer if running."""
        if self._persist_timer:
            self._persist_timer.cancel()
            self._persist_timer = None
    
    def _remove_persist_file(self):
        """Remove persist file if it exists."""
        try:
            if os.path.exists(PERSIST_FILE):
                os.remove(PERSIST_FILE)
        except Exception:
            pass
    
    def cleanup(self):
        """Cleanup on shutdown - cancel timer and remove file."""
        self._cancel_timer()
        self._remove_persist_file()
