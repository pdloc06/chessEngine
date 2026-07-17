"""
Global configuration and asset caches for the chess program.

This module defines the static dimensions, framerate, color theme, and
initializes the global dictionaries used for caching images and text
surfaces to optimize rendering performance.
"""
import pygame as pg

# Board dimensions and layout
BOARD_WIDTH = BOARD_HEIGHT = 512
MOVE_LOG_PANEL_WIDTH = 250

# Player info bars (rendered above and below the board)
PLAYER_BAR_HEIGHT = 40
BOARD_TOP = PLAYER_BAR_HEIGHT  # Vertical pixel offset where the board starts

# Total window dimensions
WIDTH = BOARD_WIDTH + MOVE_LOG_PANEL_WIDTH
HEIGHT = BOARD_HEIGHT + 2 * PLAYER_BAR_HEIGHT
MOVE_LOG_PANEL_HEIGHT = HEIGHT

# Board properties
DIMENSION = 8  # Dimensions of a chess board are 8x8
SQ_SIZE = BOARD_WIDTH // DIMENSION

# Framerate settings
MAX_FPS = 20
ANIMATION_FPS = 60

# AI search settings
AI_MAX_DEPTH = 4       # Maximum iterative-deepening depth for the move finder
AI_TIME_LIMIT = 5.0    # Soft time limit (seconds) per AI move

# Time controls offered on the pre-game menu, keyed by display name to
# (initial_seconds, increment_seconds). `None` initial seconds means the
# clocks are disabled entirely (the classic, untimed experience).
GAME_MODES: dict[str, tuple[int | None, int]] = {
    'Bullet': (60, 0),
    'Blitz': (5 * 60, 0),
    'Rapid': (10 * 60, 0),
    'Classical': (30 * 60, 0),
    'No Clock': (None, 0),
}

# When True, the AI tries to host the search in a separate UCI engine
# process running under PyPy, whose JIT makes the pure-Python search ~2x
# faster (see uci_client.py). Needs PyPy on PATH or installed through uv
# (`uv python install pypy3.11`); silently falls back to the in-process
# search when unavailable, so the game works either way.
AI_USE_UCI_ENGINE = True

# Global caches
IMAGES: dict[str, pg.Surface] = {}  # Storing chess pieces' images
SMALL_IMAGES: dict[str, pg.Surface] = {}  # Downscaled piece images for the captured-material row
CAPTURED_ICON_SIZE = 16
COORD_SURFACES: dict[str, dict[str, pg.Surface]] = {'white': {}, 'grey': {}}  # Storing pre-rendered coordinate surfaces

# Standard board colors
board_colors: list[pg.Color] = [pg.Color('white'), pg.Color('grey')]

# Shared UI theme colors (lichess-inspired dark panel palette)
THEME: dict[str, pg.Color] = {
    'panel_bg': pg.Color('#262421'),
    'panel_row': pg.Color('#2b2927'),
    'panel_select': pg.Color('#4c4a48'),
    'button': pg.Color('#3c3a38'),
    'button_hover': pg.Color('#4c4a48'),
    'border': pg.Color('#5c5a58'),
    'text': pg.Color('white'),
    'text_dim': pg.Color('#c9c8c7'),
    'text_muted': pg.Color('#989795'),
    'accent': pg.Color('#629924'),
    'bar_bg': pg.Color('#1f1d1b'),
    'bar_active': pg.Color('#333130'),
    'clock_bg': pg.Color('#3c3a38'),         # Idle clock (not this player's turn)
    'clock_active_bg': pg.Color('#e8e6e3'),  # Ticking clock, light so it reads as "live"
    'clock_low_bg': pg.Color('#c0392b'),     # Ticking clock under 20 seconds remaining
}
