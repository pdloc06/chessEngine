"""
Tests that every SVG asset actually rasterizes to something visible.

The asset loaders in `gui.graphics` fail quietly by design: a missing file only
prints a warning, and the caches in `config` are never cleared, so a broken set
leaves the previous set's surfaces in place and the game keeps running with the
wrong pieces on the board. A blank SVG is quieter still -- SDL_image's renderer
happily returns a fully transparent surface for a file it cannot parse (a
`<style>` block, for instance, which its rasterizer does not support), and
nothing downstream ever complains.

So the assertion that matters here is not "did a surface come back" but "does
that surface have any opaque pixels in it".
"""

import os

import pytest

# Must be set before pygame initializes its video backend, so that these tests
# run on a machine with no display attached.
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

import pygame as pg  # noqa: E402

import config  # noqa: E402
from gui import graphics  # noqa: E402

PIECES = ['bB', 'bK', 'bN', 'bP', 'bQ', 'bR', 'wB', 'wK', 'wN', 'wP', 'wQ', 'wR']


@pytest.fixture(scope='module', autouse=True)
def display():
    """Give pygame a video surface; loading images requires one."""
    pg.init()
    pg.display.set_mode((64, 64))
    yield
    pg.quit()


def has_opaque_pixels(surface: pg.Surface) -> bool:
    """
    Report whether a surface contains any non-transparent pixel.

    Sampling every 2nd pixel is plenty to tell "a drawn piece" from "an empty
    surface", and keeps the whole suite well under a second.

    Parameters
    ----------
    surface : pygame.Surface
        The rasterized image to inspect.

    Returns
    -------
    bool
        True if at least one sampled pixel has a non-zero alpha.
    """
    width, height = surface.get_size()
    return any(
        surface.get_at((x, y)).a > 0
        for x in range(0, width, 2)
        for y in range(0, height, 2)
    )


@pytest.mark.parametrize('piece_set', graphics.list_piece_sets())
def test_every_piece_set_renders(piece_set):
    """Each discovered set supplies all 12 pieces, at both cached sizes."""
    graphics.load_pieces_images(piece_set)

    for piece in PIECES:
        for cache, name in ((config.IMAGES, 'IMAGES'),
                            (config.SMALL_IMAGES, 'SMALL_IMAGES')):
            assert piece in cache, f"{piece_set}/{piece} missing from {name}"
            surface = cache[piece]
            assert surface.get_width() > 0 and surface.get_height() > 0
            assert has_opaque_pixels(surface), (
                f"{piece_set}/{piece}.svg rasterized to a blank surface in {name}"
            )


def test_piece_sets_are_discovered():
    """The pieces/ directory holds real sets, not just the config fallback."""
    sets = graphics.list_piece_sets()
    assert 'standard' in sets and 'neo' in sets, sets


def test_assets_load_from_any_working_directory(tmp_path, monkeypatch):
    """
    Assets resolve against the package, not the shell's current directory.

    The loaders used to build `Path('pieces')` relative to the CWD, so the
    game only started when launched from the repo root. Running the whole
    load from an empty temp directory is what catches a regression back to
    a relative path -- from the repo root, a broken path still works.
    """
    monkeypatch.chdir(tmp_path)

    assert 'standard' in graphics.list_piece_sets()

    graphics.load_pieces_images('standard')
    assert has_opaque_pixels(config.IMAGES['wK'])

    graphics.load_eval_icons()
    assert has_opaque_pixels(config.EVAL_ICONS_LOG['best'])


def test_every_eval_icon_renders():
    """Every review badge named by EVAL_ICON_NAMES loads at both sizes."""
    graphics.load_eval_icons()

    for name in graphics.EVAL_ICON_NAMES:
        for cache, cache_name in ((config.EVAL_ICONS_LOG, 'EVAL_ICONS_LOG'),
                                  (config.EVAL_ICONS_BOARD, 'EVAL_ICONS_BOARD')):
            assert name in cache, f"{name} missing from {cache_name}"
            surface = cache[name]
            assert surface.get_width() > 0 and surface.get_height() > 0
            assert has_opaque_pixels(surface), (
                f"{name}.svg rasterized to a blank surface in {cache_name}"
            )
