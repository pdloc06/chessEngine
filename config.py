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

# Global caches
IMAGES: dict[str, pg.Surface] = {}  # Storing chess pieces' images
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
}
