"""
Tests for the Texel tuner's EPD loader.

The tuner's arithmetic is checked by its own held-out error, but the loader is
the part that can be silently wrong: a misparsed result label flips a position's
ground truth, and a sampling bug that returns a prefix of the file instead of a
spread through it would go unnoticed because the fit still runs and still prints
numbers. Both are cheap to pin down.
"""
from engine.tools.tune import EPD_BLOCK_SIZE, load_epd

# One real line from quiet-labeled.epd, and the pieces it should decode to.
SAMPLE = ('rn2kb1r/ppp1pp1p/2q3p1/3nN3/3P4/2N1P3/PPb2PPP/R1B1KB1R b KQkq -'
          ' c9 "0-1";')


def _write_epd(tmp_path, lines):
    """
    Write an EPD file and return its path.

    Parameters
    ----------
    tmp_path : pathlib.Path
        pytest's temporary directory.
    lines : list of str
        Lines to write, without trailing newlines.

    Returns
    -------
    str
        Path to the written file.
    """
    path = tmp_path / 'positions.epd'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return str(path)


def test_load_epd_reads_the_position_and_its_label(tmp_path):
    """
    A single line must produce one position carrying the right result.

    The FEN here has only four fields, which is what these files contain; the
    parser has to accept that rather than demand the two clock fields.
    """
    groups = load_epd(_write_epd(tmp_path, [SAMPLE]), limit=None)
    positions, results = groups[0]
    assert len(positions) == len(results) == 1
    assert results[0] == 0.0                      # "0-1" is a Black win
    assert positions[0].white_to_move is False
    assert len(positions[0].white_pieces) == 14
    assert len(positions[0].black_pieces) == 15


def test_load_epd_maps_every_result_token(tmp_path):
    """
    All three labels must decode, and a malformed line must be skipped rather
    than kill the run — one bad line in 725,000 should not cost the fit.
    """
    lines = [SAMPLE.replace('"0-1"', f'"{token}"')
             for token in ('1-0', '0-1', '1/2-1/2')]
    lines.append('not a fen at all c9 "1-0";')
    lines.append(SAMPLE.replace('"0-1"', '"*"'))     # unfinished game
    groups = load_epd(_write_epd(tmp_path, lines), limit=None)
    assert [r for _, results in groups for r in results] == [1.0, 0.0, 0.5]


def test_load_epd_groups_by_block_so_a_game_is_not_split(tmp_path):
    """
    Positions must arrive in contiguous blocks, because the train/validation
    split works on whole groups. Splitting positions instead would leak a
    game's answer across the cut and make held-out error meaningless.
    """
    groups = load_epd(_write_epd(tmp_path, [SAMPLE] * (EPD_BLOCK_SIZE * 2 + 5)),
                      limit=None)
    assert [len(positions) for positions, _ in groups] == [
        EPD_BLOCK_SIZE, EPD_BLOCK_SIZE, 5]


def test_load_epd_samples_across_the_whole_file(tmp_path):
    """
    A limit must thin the file out, not truncate it.

    The file may be ordered by source, so reading the first N lines would fit
    on one corner of it. Marking each block by its result makes the spread
    visible: sampling a third of six blocks has to reach the later ones.
    """
    tokens = ['1-0', '0-1', '1/2-1/2', '1-0', '0-1', '1/2-1/2']
    lines = []
    for token in tokens:
        lines += [SAMPLE.replace('"0-1"', f'"{token}"')] * EPD_BLOCK_SIZE
    groups = load_epd(_write_epd(tmp_path, lines), limit=EPD_BLOCK_SIZE * 2)

    assert len(groups) == 2
    assert sum(len(positions) for positions, _ in groups) == EPD_BLOCK_SIZE * 2
    # Blocks 0 and 3 of 6, so the second sample comes from the file's back half.
    assert [results[0] for _, results in groups] == [1.0, 1.0]

    # Asking for more than half the blocks is where truncation used to hide:
    # the stride collapsed to 1 and the sampler became `lines[:limit]`, so the
    # tail stayed unreachable no matter which seed ran. Blocks 0, 1, 3, 4.
    groups = load_epd(_write_epd(tmp_path, lines), limit=EPD_BLOCK_SIZE * 4)
    assert [results[0] for _, results in groups] == [1.0, 0.0, 1.0, 0.0]
