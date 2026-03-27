#!/usr/bin/env python3
"""
PeppyMeter Remote Client - SMB mount management.

Mounts Volumio's template share for remote template access.
"""

import os
import subprocess

from peppy_common import (
    SMB_MOUNT_BASE,
    SMB_SHARE_PATH,
    _is_ip_address,
)

class SMBMount:
    """Manages SMB mount for remote templates."""
    
    def __init__(self, hostname, mount_point=None):
        self.hostname = hostname
        self.mount_point = Path(mount_point if mount_point else SMB_MOUNT_BASE)
        # .local is for mDNS hostnames only; use host as-is for IP addresses
        if _is_ip_address(hostname):
            self.share_path = f"//{hostname}/{SMB_SHARE_PATH}"
        else:
            self.share_path = f"//{hostname}.local/{SMB_SHARE_PATH}"
        self._mounted = False
    
    def mount(self):
        """Mount the SMB share. Returns True on success."""
        # Handle stale mount points - unmount first if stale
        self._cleanup_stale_mount()
        
        # Create mount point (handle stale file handles gracefully)
        try:
            self.mount_point.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # Stale file handle (errno 116) or other issues - try to recover
            if e.errno == 116:  # ESTALE - Stale file handle
                print(f"Stale mount detected at {self.mount_point}, cleaning up...")
                self._force_unmount()
                # Remove stale directory and recreate
                try:
                    subprocess.run(['sudo', 'rm', '-rf', str(self.mount_point)],
                                 capture_output=True, timeout=5)
                except Exception:
                    pass
                self.mount_point.mkdir(parents=True, exist_ok=True)
            else:
                raise
        
        # Check if already mounted
        if self._is_mounted():
            print(f"SMB share already mounted at {self.mount_point}")
            self._mounted = True
            return True
        
        # Try SMB versions from oldest/fastest to newest (Linux cifs 3.x can be slow)
        # Order: 2.0, 2.1, 3.0, 3.1.1
        vers_list = ['2.0', '2.1', '3.0', '3.1.1']
        for vers in vers_list:
            opts_guest = f'guest,ro,nofail,vers={vers}'
            opts_creds = f'user=volumio,password=volumio,ro,nofail,vers={vers}'
            print(f"Mounting {self.share_path} at {self.mount_point} (SMB {vers})...")
            result = subprocess.run(
                ['sudo', 'mount', '-t', 'cifs', self.share_path, str(self.mount_point),
                 '-o', opts_guest],
                capture_output=True, text=True,
                encoding='utf-8', errors='replace'
            )
            if result.returncode == 0:
                print(f"  Mounted as guest (SMB {vers})")
                self._mounted = True
                return True
            result = subprocess.run(
                ['sudo', 'mount', '-t', 'cifs', self.share_path, str(self.mount_point),
                 '-o', opts_creds],
                capture_output=True, text=True,
                encoding='utf-8', errors='replace'
            )
            if result.returncode == 0:
                print(f"  Mounted with volumio credentials (SMB {vers})")
                self._mounted = True
                return True
        print(f"  Failed to mount (tried SMB {', '.join(vers_list)}): {result.stderr}")
        return False
    
    def unmount(self):
        """Unmount the SMB share."""
        if self._mounted and self._is_mounted():
            subprocess.run(['sudo', 'umount', str(self.mount_point)], 
                         capture_output=True)
            self._mounted = False
    
    def _is_mounted(self):
        """Check if the mount point is currently mounted."""
        try:
            result = subprocess.run(['mountpoint', '-q', str(self.mount_point)], timeout=5)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            # Timeout usually means stale mount
            return False
        except Exception:
            return False
    
    def _cleanup_stale_mount(self):
        """Clean up any stale mounts at the mount point."""
        try:
            # Check if mount point exists and might be stale
            if self.mount_point.exists():
                # Try to access it - stale mounts will fail
                try:
                    list(self.mount_point.iterdir())
                except OSError as e:
                    if e.errno == 116:  # ESTALE
                        print(f"Cleaning up stale mount at {self.mount_point}...")
                        self._force_unmount()
        except Exception:
            pass
    
    def _force_unmount(self):
        """Force unmount the mount point."""
        try:
            # Try lazy unmount first
            subprocess.run(['sudo', 'umount', '-l', str(self.mount_point)],
                         capture_output=True, timeout=10)
        except Exception:
            pass
        try:
            # Force unmount as fallback
            subprocess.run(['sudo', 'umount', '-f', str(self.mount_point)],
                         capture_output=True, timeout=10)
        except Exception:
            pass
    
    @property
    def templates_path(self):
        """Path to templates directory."""
        return self.mount_point / 'templates'
