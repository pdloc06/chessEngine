"""
Global configuration and asset caches for the chess program.

This module defines the static dimensions, framerate, and initializes
the global dictionaries used for caching images and text surfaces to
optimize rendering performance.
"""
import pygame as pg

# Board dimensions and layout
BOARD_WIDTH = BOARD_HEIGHT = 512
MOVE_LOG_PANEL_WIDTH = 250
MOVE_LOG_PANEL_HEIGHT = BOARD_HEIGHT

# Total window dimensions
WIDTH = BOARD_WIDTH + MOVE_LOG_PANEL_WIDTH
HEIGHT = BOARD_HEIGHT

# Board properties
DIMENSION = 8  # Dimensions of a chess board are 8x8
SQ_SIZE = BOARD_WIDTH // DIMENSION

# Framerate settings
MAX_FPS = 20
ANIMATION_FPS = 60

# Global caches
IMAGES = {}  # Storing chess pieces' images
COORD_SURFACES = {'white': {}, 'grey': {}}  # Storing pre-rendered coordinate surfaces

# Standard board colors
board_colors = [pg.Color('white'), pg.Color('grey')]