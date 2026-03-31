#!/usr/bin/env python3
"""
PeppyMeter Remote Client - Version checking.

Waits for server availability and compares client/server versions.
Shows blocking pygame UI on mismatch.
"""

import sys
import time

from peppy_common import (
    __version__,
    SERVER_WAIT_TIMEOUT_SEC,
    SERVER_RETRY_INTERVAL_SEC,
    _resolve_pygame_ui_font,
    _parse_semver_tuple,
    _compare_remote_release_versions,
    log_client,
)

def wait_for_server_plugin_version(server_info, config_fetcher, timeout_sec=SERVER_WAIT_TIMEOUT_SEC):
    """
    Wait until this host's Volumio answers HTTP getRemoteConfig, or timeout.

    UDP discovery may advertise plugin_version from *any* peer on the LAN; we do not trust it
    for compatibility — only a successful HTTP response from server_info['ip'] counts.

    Returns (status, plugin_version_or_none):
      status: 'ok' | 'missing' | 'timeout'
    """
    ip = server_info.get('ip', '')
    label = server_info.get('hostname') or ip or 'server'
    deadline = time.time() + float(timeout_sec)
    next_fetch = 0.0
    attempt = 0
    use_pygame = False
    screen = None
    clock = None
    font_title = font_body = font_hint = None
    pygame_mod = None
    try:
        import pygame as _pg
        pygame_mod = _pg
        _pg.init()
        screen = _pg.display.set_mode((800, 480), _pg.RESIZABLE)
        _pg.display.set_caption('PeppyMeter Remote — Waiting for server')
        clock = _pg.time.Clock()
        try:
            font_title = _pg.font.Font(None, 26)
            font_body = _pg.font.Font(None, 20)
            font_hint = _pg.font.Font(None, 18)
        except Exception:
            font_title = _resolve_pygame_ui_font(_pg, 22)
            font_body = _resolve_pygame_ui_font(_pg, 17)
            font_hint = _resolve_pygame_ui_font(_pg, 15)
        use_pygame = True
    except Exception:
        pass

    print(f"Waiting for Volumio / PeppyMeter at {label} ({ip})... (up to {int(timeout_sec)}s)")
    log_client(f"Waiting for server HTTP (plugin version), timeout={timeout_sec}s", "basic")

    try:
        while time.time() < deadline:
            now = time.time()
            if now >= next_fetch:
                attempt += 1
                next_fetch = now + SERVER_RETRY_INTERVAL_SEC
                ok, _, _ = config_fetcher.fetch()
                if ok:
                    pv = (getattr(config_fetcher, 'cached_plugin_version', None) or '').strip()
                    if pv:
                        print(f"  Server reachable (plugin {pv}).")
                        return 'ok', pv
                    print('  Server responded but does not advertise plugin_version (old plugin).')
                    return 'missing', None
                log_client(f"Server HTTP not ready yet (attempt {attempt})", "verbose", "network")
                if not use_pygame:
                    left = max(0, int(deadline - now))
                    print(f"  Still waiting... ({left}s left)")

            if use_pygame and screen is not None and pygame_mod is not None:
                for event in pygame_mod.event.get():
                    if event.type == pygame_mod.QUIT:
                        print('Waiting cancelled by user.')
                        return 'timeout', None
                screen.fill((20, 20, 28))
                y = 36
                rows = (
                    ('Waiting for server', font_title, (180, 200, 255)),
                    (f'{label} ({ip})', font_body, (220, 220, 230)),
                    ('', font_body, (220, 220, 230)),
                    ('Starting Volumio or the PeppyMeter plugin can take a minute.', font_body, (200, 200, 210)),
                    ('This window will close when the server answers.', font_body, (200, 200, 210)),
                    (f'Attempt {attempt}  ·  {max(0, int(deadline - time.time()))}s left', font_hint, (140, 140, 155)),
                )
                for text, font, color in rows:
                    if text:
                        s = font.render(text, True, color)
                        screen.blit(s, (40, y))
                    y += 30 if font is font_title else 26
                pygame_mod.display.flip()
                clock.tick(30)
            else:
                time.sleep(0.15)

        print('Timed out waiting for server HTTP.')
        return 'timeout', None
    finally:
        if use_pygame and pygame_mod is not None:
            try:
                pygame_mod.quit()
            except Exception:
                pass


def show_version_mismatch_screen(title, body_lines):
    """
    Blocking fullscreen message until user closes (click, Enter, Escape, or window close).
    Works on Linux and Windows (pygame).
    """
    import pygame
    pygame.init()
    screen = pygame.display.set_mode((880, 520), pygame.RESIZABLE)
    pygame.display.set_caption('PeppyMeter Remote — Version')
    try:
        font_title = pygame.font.Font(None, 28)
        font_body = pygame.font.Font(None, 22)
        font_hint = pygame.font.Font(None, 20)
    except Exception:
        font_title = _resolve_pygame_ui_font(pygame, 24)
        font_body = _resolve_pygame_ui_font(pygame, 18)
        font_hint = _resolve_pygame_ui_font(pygame, 16)
    clock = pygame.time.Clock()
    bg = (22, 22, 30)
    fg = (230, 230, 235)
    accent = (255, 120, 100)
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_SPACE):
                    running = False
            elif event.type == pygame.MOUSEBUTTONDOWN:
                running = False
        screen.fill(bg)
        y = 40
        t = font_title.render(title, True, accent)
        screen.blit(t, (40, y))
        y += 48
        for line in body_lines:
            if not line:
                y += 12
                continue
            s = font_body.render(line, True, fg)
            screen.blit(s, (40, y))
            y += 28
        hint = font_hint.render('Click or press Enter / Escape to close', True, (140, 140, 155))
        screen.blit(hint, (40, 480))
        pygame.display.flip()
        clock.tick(30)
    pygame.quit()


def check_remote_version_and_exit_if_mismatch(
    server_info, config_fetcher, skip_check=False, wait_timeout_sec=SERVER_WAIT_TIMEOUT_SEC
):
    """
    Wait for Volumio HTTP if needed, then compare PeppyMeter Screensaver release with this client.
    On mismatch or unreachable server after wait, show blocking UI and exit.
    """
    if skip_check:
        return
    status, server_pv = wait_for_server_plugin_version(server_info, config_fetcher, timeout_sec=wait_timeout_sec)
    client_pv = __version__

    if status == 'timeout':
        show_version_mismatch_screen(
            'Server not reachable',
            [
                'Could not connect to Volumio within the wait time.',
                'Power on the device, wait until Volumio is ready, enable PeppyMeter Screensaver,',
                'then run peppy_remote again (or increase --server-wait-timeout).',
                '',
                f'This client (peppy_remote): {client_pv}',
            ],
        )
        sys.exit(1)

    if status == 'missing' or not server_pv:
        show_version_mismatch_screen(
            'Server plugin is too old',
            [
                'This Volumio system does not advertise a PeppyMeter Screensaver version.',
                'Update PeppyMeter Screensaver on the Volumio device to a release that supports',
                'remote version checks, then try again.',
                '',
                f'This client (peppy_remote): {client_pv}',
            ],
        )
        sys.exit(1)

    cmp = _compare_remote_release_versions(client_pv, server_pv)
    if cmp == 0:
        return
    if cmp is None:
        show_version_mismatch_screen(
            'Version check failed',
            [
                'Could not compare release versions.',
                f'Client: {client_pv}  Server: {server_pv}',
                'Update both sides to the latest PeppyMeter Screensaver / peppy_remote releases.',
            ],
        )
        sys.exit(1)

    if cmp < 0:
        show_version_mismatch_screen(
            'Update peppy_remote',
            [
                'This remote client is older than the PeppyMeter Screensaver plugin on the server.',
                'Update peppy_remote on this machine to match the server, then try again.',
                '',
                f'Server (plugin): {server_pv}',
                f'This client (peppy_remote): {client_pv}',
            ],
        )
        sys.exit(1)

    show_version_mismatch_screen(
        'Update PeppyMeter Screensaver on Volumio',
        [
            'The PeppyMeter Screensaver plugin on the server is older than this remote client.',
            'Update the plugin on the Volumio device (or install a matching peppy_remote release),',
            'then try again.',
            '',
            f'Server (plugin): {server_pv}',
            f'This client (peppy_remote): {client_pv}',
        ],
    )
    sys.exit(1)
