#!/usr/bin/env python3
"""
PeppyMeter Remote Client - Remote spectrum output.

Replacement for SpectrumOutput that uses network data instead of pipe.
"""

import os
import sys
import time
import threading

from peppy_common import log_client

class RemoteSpectrumOutput:
    """
    A SpectrumOutput replacement that uses network data instead of pipe.
    
    This class initializes the Spectrum visual components normally but
    bypasses the pipe-reading data source, instead receiving bar heights
    from the SpectrumReceiver and injecting them directly.
    """
    
    def __init__(self, util, meter_config_volumio, screensaver_path, spectrum_receiver, 
                 decay_rate=0.95, spectrum_templates_path=None):
        """Initialize remote spectrum output.
        
        :param util: PeppyMeter utility class
        :param meter_config_volumio: Volumio meter configuration
        :param screensaver_path: Path to screensaver directory (contains 'spectrum' subfolder)
        :param spectrum_receiver: SpectrumReceiver instance for network data
        :param decay_rate: Per-frame decay multiplier (0.85=fast, 0.98=slow)
        :param spectrum_templates_path: Override path for spectrum templates (None = use SMB mount)
        """
        self.util = util
        self.meter_config_volumio = meter_config_volumio
        self.screensaver_path = screensaver_path
        self.spectrum_receiver = spectrum_receiver
        self.sp = None
        self._initialized = False
        self._last_packet_seq = -1  # Track last processed packet
        self._local_bins = None  # Local copy for decay between packets
        # Validate and clamp decay rate to sensible range
        self._decay_rate = max(0.5, min(0.99, decay_rate))
        self._spectrum_templates_path = spectrum_templates_path  # Override for local templates
        
        log_client(f"Spectrum decay rate: {self._decay_rate}", "verbose")
        
        # Get spectrum config from meter section
        from volumio_configfileparser import SPECTRUM, SPECTRUM_SIZE, SPECTRUM_POS
        from configfileparser import METER
        
        meter_config = util.meter_config
        meter_section = meter_config_volumio[meter_config[METER]]
        
        self.w = meter_section[SPECTRUM_SIZE][0]
        self.h = meter_section[SPECTRUM_SIZE][1]
        self.s = meter_section[SPECTRUM]
        # Get spectrum position within the meter layout (from meters.txt spectrum.pos)
        self.pos = meter_section.get(SPECTRUM_POS, (0, 0)) or (0, 0)
        # screensaver_path is ~/peppy_remote/screensaver, spectrum is directly under it
        self.SpectrumPath = os.path.join(screensaver_path, 'spectrum')
        
    
    def start(self):
        """Initialize spectrum visual components (but not data source)."""
        try:
            import pygame as pg
            import configparser
            from spectrumutil import SpectrumUtil
            from spectrum.spectrum import Spectrum
            from spectrumconfigparser import SCREEN_WIDTH, SCREEN_HEIGHT, AVAILABLE_SPECTRUM_NAMES
            
            # Set up util for spectrum
            self.util.spectrum_size = (self.w, self.h, self.s)
            self.util.pygame_screen = self.util.PYGAME_SCREEN
            self.util.image_util = SpectrumUtil()
            
            # Save original screen_rect (full meter display area)
            original_screen_rect = getattr(self.util, 'screen_rect', None)
            
            # Get templates_spectrum path - use override if provided, else SMB mount
            if self._spectrum_templates_path:
                templates_spectrum_path = self._spectrum_templates_path
                log_client(f"Using local spectrum templates: {templates_spectrum_path}", "verbose")
            else:
                # screensaver_path is ~/peppy_remote/screensaver
                # SMB mount is at ~/peppy_remote/mnt (contains templates/ and templates_spectrum/)
                install_dir = os.path.dirname(self.screensaver_path)  # ~/peppy_remote
                templates_spectrum_path = os.path.join(install_dir, 'mnt', 'templates_spectrum')
                log_client(f"Using SMB spectrum templates: {templates_spectrum_path}", "verbose")
            
            # Get the meter folder name (e.g., "1280x720_custom_3") from config
            from configfileparser import SCREEN_INFO, METER_FOLDER
            meter_folder = self.util.meter_config[SCREEN_INFO][METER_FOLDER]  # e.g., "1280x720_custom_3"
            
            # Set up spectrum config.txt to point to the right template folder
            spectrum_config_path = os.path.join(self.SpectrumPath, 'config.txt')
            if os.path.exists(spectrum_config_path):
                sp_config = configparser.ConfigParser()
                sp_config.read(spectrum_config_path)
                
                # Update paths for remote client
                if 'current' not in sp_config:
                    sp_config['current'] = {}
                sp_config['current']['base.folder'] = templates_spectrum_path
                sp_config['current']['spectrum.folder'] = meter_folder
                # Ensure Spectrum.__init__ loads only the active section.
                sp_config['current']['spectrum'] = self.s
                # Update pipe name to avoid error (won't be used since we don't start data source)
                sp_config['current']['pipe.name'] = '/tmp/myfifosa'
                
                with open(spectrum_config_path, 'w') as f:
                    sp_config.write(f)
            
            # Change to spectrum path to find config
            original_cwd = os.getcwd()
            os.chdir(self.SpectrumPath)
            
            try:
                # Remote-only: avoid IndexError when meter has no spectrum config (e.g. template mismatch).
                # Check spectrum configs before creating Spectrum (ScreensaverSpectrum.__init__ uses spectrum_configs[0]).
                from spectrumconfigparser import SpectrumConfigParser, AVAILABLE_SPECTRUM_NAMES as _SP_NAMES
                _parser = SpectrumConfigParser(standalone=False)
                _parser.config[_SP_NAMES] = [self.s]
                _configs = _parser.get_spectrum_configs()
                if not _configs:
                    log_client("No spectrum configs available; spectrum not visible in current meter config", "verbose")
                    return
                # Create spectrum object (standalone=False for plugin mode)
                # Note: Spectrum.__init__ calls ScreensaverSpectrum which overwrites util.screen_rect
                self.sp = Spectrum(self.util, standalone=False)
                # Use self.sp.s (Spectrum may have fallen back to first available if requested not in template)
                self.s = self.sp.s
                # Override dimensions
                self.sp.config[SCREEN_WIDTH] = self.w
                self.sp.config[SCREEN_HEIGHT] = self.h
                # Set spectrum name and reload configs
                self.sp.config[AVAILABLE_SPECTRUM_NAMES] = [self.s]
                self.sp.config_parser.config[AVAILABLE_SPECTRUM_NAMES] = [self.s]
                self.sp.spectrum_configs = self.sp.config_parser.get_spectrum_configs()
                
                self.sp.init_spectrums()
                
                # Initialize visual components (from Spectrum.start() but without data source)
                # This sets up bounding_box for all components
                from spectrumconfigparser import REFLECTION_GAP
                self.sp.index = 0
                self.sp.set_background()
                self.sp.set_bars()
                self.sp.reflection_gap = self.sp.spectrum_configs[self.sp.index].get(REFLECTION_GAP, 0)
                self.sp.set_reflections()
                self.sp.set_toppings()
                self.sp.set_foreground()
                self.sp.init_variables()
                
                # CRITICAL: Offset all component positions by spectrum.pos from meters.txt
                # The spectrum renders with coordinates relative to its own canvas (0,0)
                # but we need to position it within the meter layout
                pos_x, pos_y = self.pos
                if pos_x != 0 or pos_y != 0:
                    for comp in self.sp.components:
                        if hasattr(comp, 'content_x'):
                            comp.content_x += pos_x
                        if hasattr(comp, 'content_y'):
                            comp.content_y += pos_y
                    print(f"[RemoteSpectrum] Applied position offset: ({pos_x}, {pos_y})")
                
                # Restore original screen_rect (full screen) - Spectrum.__init__ overwrote it
                if original_screen_rect is not None:
                    self.util.screen_rect = original_screen_rect
                else:
                    # Set to full screen if wasn't set before
                    from configfileparser import SCREEN_INFO, WIDTH, HEIGHT
                    screen_w = self.util.meter_config[SCREEN_INFO][WIDTH]
                    screen_h = self.util.meter_config[SCREEN_INFO][HEIGHT]
                    self.util.screen_rect = pg.Rect(0, 0, screen_w, screen_h)
                
                # Store spectrum clip rect for drawing
                # The spectrum content is positioned based on spectrum.x and spectrum.y from spectrum.txt
                # NOT from self.pos (meters.txt spectrum.pos which is often 0,0)
                spectrum_x = self.sp.spectrum_configs[0].get('spectrum.x', 0)
                spectrum_y = self.sp.spectrum_configs[0].get('spectrum.y', 0)
                # The clip rect should cover where the spectrum actually renders
                # Content ranges from (spectrum_x, spectrum_y - bar_height) to (spectrum_x + w, spectrum_y + reflection_height)
                # For safety, use the full spectrum canvas dimensions positioned at spectrum.x/y
                self.spectrum_clip_rect = pg.Rect(spectrum_x, spectrum_y - self.sp.height, self.w, self.h + self.sp.height)
                
                # Set run flag but DON'T start data source (we feed via network)
                self.sp.run_flag = True
                # NOT calling: self.sp.start_data_source()
                
                # Client-side: start from zero and wait for new spectrum data (avoids ghosted full bars
                # when meter/spectrum changes; decay + server data will drive bars)
                n_bars = len(self.sp._prev_bar_heights) if (hasattr(self.sp, '_prev_bar_heights') and self.sp._prev_bar_heights) else (len(self.sp.components) - 1 if self.sp.components else int(self.sp.config.get('size', 30)))
                n_bars = max(1, n_bars)
                self._local_bins = [0.0] * n_bars
                # Force-draw all bars at 0 so set_bars() full height is never shown (no full-value bar / ghost)
                if hasattr(self.sp, '_prev_bar_heights') and self.sp._prev_bar_heights:
                    for i in range(min(n_bars, len(self.sp._prev_bar_heights))):
                        self.sp._prev_bar_heights[i] = 999.0  # Bypass set_bar_y skip (prev==0 would skip)
                for i in range(n_bars):
                    idx = i + 1
                    try:
                        self.sp.set_bar_y(idx, 0.0)
                        if hasattr(self.sp, 'set_reflection_y'):
                            self.sp.set_reflection_y(idx, 0.0)
                        if hasattr(self.sp, 'set_topping_y'):
                            self.sp.set_topping_y(idx, 0.0)
                    except Exception:
                        pass
                
                self._initialized = True
                
            finally:
                os.chdir(original_cwd)
                
        except Exception as e:
            print(f"[RemoteSpectrum] Failed to initialize: {e}")
            import traceback
            traceback.print_exc()
            self.sp = None
    
    def update(self):
        """Update spectrum from network data and render."""
        dirty_rects = []
        if not self._initialized or self.sp is None:
            if not hasattr(self, '_dbg_init_warn'):
                print(f"[RemoteSpectrum] update: not initialized={not self._initialized}, sp={self.sp}")
                self._dbg_init_warn = True
            return dirty_rects
        
        # Get bar heights from network
        bins = self.spectrum_receiver.get_bins()
        current_seq = self.spectrum_receiver.seq
        
        # Fallback init if start() didn't set _local_bins (e.g. older code path)
        if self._local_bins is None and hasattr(self.sp, '_prev_bar_heights') and self.sp._prev_bar_heights:
            self._local_bins = [0.0] * len(self.sp._prev_bar_heights)
        
        if not self._local_bins:
            return dirty_rects
        
        # Check if we have new packet data
        new_packet = bins and current_seq != self._last_packet_seq
        
        
        # SMOOTH ANIMATION LOGIC:
        # 1. Always decay local bins (bars fall naturally)
        # 2. Only push bars UP when server sends genuinely NEW higher values
        # 3. Ignore repeated/stale server data so decay can work
        
        _prev_len = len(self.sp._prev_bar_heights) if (hasattr(self.sp, '_prev_bar_heights') and self.sp._prev_bar_heights) else len(self._local_bins)
        num_bars = min(len(self._local_bins), _prev_len)
        
        # Freshness gating by packet sequence is more reliable than value-diff threshold.
        server_data_changed = bool(new_packet)
        
        # Client-side: ignore "all bars at max" packet (pre-FFT after spectrum reinit); decay and wait for real data
        if bins and len(bins) >= 2:
            mx = max(bins)
            if mx > 50 and all(abs(b - mx) <= 2 for b in bins):
                # Treat as pre-FFT full-height burst: zero and don't apply this packet
                for i in range(num_bars):
                    if i < len(self._local_bins):
                        self._local_bins[i] *= self._decay_rate
                        if self._local_bins[i] < 0.5:
                            self._local_bins[i] = 0.0
                bins = None  # Skip Step 2 so we don't push full values into _local_bins
        
        # Step 1: Apply decay to all local bins (ALWAYS)
        for i in range(num_bars):
            self._local_bins[i] *= self._decay_rate
            if self._local_bins[i] < 0.5:
                self._local_bins[i] = 0
        
        # Step 2: Only use server data when packet is NEW and values are HIGHER
        if bins and server_data_changed:
            num_to_copy = min(len(bins), num_bars)
            for i in range(num_to_copy):
                server_val = bins[i]
                if server_val > self._local_bins[i]:
                    self._local_bins[i] = server_val  # Instant rise to peak
            self._last_packet_seq = current_seq
        
        # Step 3: Update visual components (no fade-in; follow server data + decay)
        for i in range(num_bars):
            new_height = self._local_bins[i]
            idx = i + 1  # 1-based index for Spectrum methods
            
            # Force update by setting prev to different value (bypass optimization)
            if hasattr(self.sp, '_prev_bar_heights') and self.sp._prev_bar_heights and i < len(self.sp._prev_bar_heights):
                self.sp._prev_bar_heights[i] = new_height + 100
            
            try:
                self.sp.set_bar_y(idx, new_height)
                if hasattr(self.sp, 'set_reflection_y'):
                    self.sp.set_reflection_y(idx, new_height)
                if hasattr(self.sp, 'set_topping_y'):
                    self.sp.set_topping_y(idx, new_height)
            except Exception:
                pass
        
        # Draw spectrum (without display.update - parent handles that)
        try:
            import pygame as pg
            prev_clip = self.util.pygame_screen.get_clip()
            # Use spectrum-specific clip rect
            clip_rect = getattr(self, 'spectrum_clip_rect', self.util.screen_rect)
            self.util.pygame_screen.set_clip(clip_rect)
            
            # Clean and draw
            if hasattr(self.sp, '_dirty_rects') and self.sp._dirty_rects:
                old_dirty = [r.copy() for r in self.sp._dirty_rects if r]
                for rect in old_dirty:
                    self.sp.draw_area(rect)
                self.sp._dirty_rects = []
                dirty_rects.extend(old_dirty)
            self.sp.draw()
            
            # Return spectrum dirty regions for parent's display.update(dirty_rects).
            if hasattr(self.sp, '_dirty_rects') and self.sp._dirty_rects:
                dirty_rects.extend([r.copy() for r in self.sp._dirty_rects if r])
            elif clip_rect:
                dirty_rects.append(clip_rect.copy())
            
            self.util.pygame_screen.set_clip(prev_clip)
        except Exception:
            pass  # Silently handle draw errors
        
        return dirty_rects
    
    def stop_thread(self):
        """Stop spectrum."""
        if self.sp:
            try:
                self.sp.stop()
            except Exception:
                pass
        self._initialized = False
    
    def get_current_bins(self):
        """Get current bar heights (for compatibility)."""
        if self.sp and hasattr(self.sp, '_prev_bar_heights'):
            return list(self.sp._prev_bar_heights)
        return None
