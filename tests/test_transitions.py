import pytest
from PIL import Image

from pixelpanel import transitions as tr

W, H = 64, 16
OLD = Image.new("RGB", (W, H), (200, 0, 0))
NEW = Image.new("RGB", (W, H), (0, 0, 200))


@pytest.mark.parametrize("kind", [k for k in tr.TRANSITIONS if k not in ("none", "random")])
def test_every_transition_ends_on_the_new_page_and_holds_it(kind):
    out = tr.build(kind, OLD, NEW)
    assert len(out.frames) >= 3
    assert all(f.size == (W, H) for f in out.frames)
    assert out.frames[-1].tobytes() == NEW.tobytes()
    assert out.durations[-1] >= tr.HOLD_MS  # last frame stays until the real page arrives
    assert 0 < tr.play_ms(out) < 4000


def test_none_has_no_frames():
    assert tr.build("none", OLD, NEW) is None


def test_random_picks_a_real_transition():
    for _ in range(20):
        out = tr.build("random", OLD, NEW)
        assert out is not None and out.frames[-1].tobytes() == NEW.tobytes()


def test_nyan_draws_a_rainbow_then_fades_it():
    out = tr.build("nyan", OLD, NEW)
    colors_mid = {c for _, c in out.frames[len(out.frames) // 2].getcolors(4096)}
    assert tr.RAINBOW[0] in colors_mid and tr.RAINBOW[4] in colors_mid  # red and blue bands
    # once the cat is gone the rainbow fades over the new page instead of cutting to it
    fade = out.frames[-3]
    assert fade.tobytes() != NEW.tobytes() and tr.RAINBOW[0] not in {c for _, c in fade.getcolors(4096)}


def test_frames_size_follows_the_pictures():
    old, new = Image.new("RGB", (16, 64)), Image.new("RGB", (16, 64), (9, 9, 9))
    out = tr.build("push_left", old, new)
    assert out.frames[0].size == (16, 64)
