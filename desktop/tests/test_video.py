import io

import numpy as np
from PIL import Image

from camandroid_desktop.protocol import FLAG_FRONT_CAMERA, Frame
from camandroid_desktop.video import OutputOptions, render, to_array


def frame_of(img: Image.Image, rotation: int = 0) -> Frame:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    return Frame(0, rotation, 0, buf.getvalue())


def two_tone(width=200, height=100) -> Image.Image:
    """Left half red, right half blue."""
    img = Image.new("RGB", (width, height), (255, 0, 0))
    img.paste((0, 0, 255), (width // 2, 0, width, height))
    return img


def is_red(px):
    return px[0] > 200 and px[2] < 60


def is_blue(px):
    return px[2] > 200 and px[0] < 60


def test_output_has_requested_size():
    out = render(frame_of(two_tone()), OutputOptions(width=320, height=240, mirror=False))
    assert out.size == (320, 240)
    assert to_array(out).shape == (240, 320, 3)


def test_rotation_is_clockwise():
    # After a 90° clockwise turn the left (red) half ends up on top.
    out = render(frame_of(two_tone()), OutputOptions(width=100, height=200, mirror=False))
    assert out.size == (100, 200)
    out = render(frame_of(two_tone(), rotation=90), OutputOptions(width=100, height=200, mirror=False))
    assert is_red(out.getpixel((50, 20)))
    assert is_blue(out.getpixel((50, 180)))


def test_extra_rotation_adds_to_phone_rotation():
    out = render(frame_of(two_tone(), rotation=90), OutputOptions(width=200, height=100, rotate=270, mirror=False))
    assert is_red(out.getpixel((20, 50)))


def test_mirror():
    out = render(frame_of(two_tone()), OutputOptions(width=200, height=100, mirror=True))
    assert is_blue(out.getpixel((20, 50)))


def test_fit_letterboxes_and_fill_crops():
    portrait = frame_of(Image.new("RGB", (90, 160), (0, 255, 0)))
    fit = render(portrait, OutputOptions(width=160, height=90, mirror=False))
    assert fit.getpixel((5, 45)) == (0, 0, 0)  # black bar
    filled = render(portrait, OutputOptions(width=160, height=90, fill=True, mirror=False))
    assert filled.getpixel((5, 45))[1] > 200


def test_front_camera_is_mirrored_by_default_and_checkbox_flips_it():
    front = Frame(0, 0, FLAG_FRONT_CAMERA, frame_of(two_tone()).data)
    assert not OutputOptions().mirror
    assert is_blue(render(front, OutputOptions(width=200, height=100)).getpixel((20, 50)))  # mirrored
    assert is_red(render(front, OutputOptions(width=200, height=100, mirror=True)).getpixel((20, 50)))
    back = frame_of(two_tone())
    assert is_red(render(back, OutputOptions(width=200, height=100)).getpixel((20, 50)))  # as seen
    assert is_blue(render(back, OutputOptions(width=200, height=100, mirror=True)).getpixel((20, 50)))


def test_output_size_is_exact_for_odd_ratios():
    for size in ((641, 359), (100, 333), (1920, 1080)):
        img = Image.new("RGB", size, (0, 200, 0))
        for rotation in (0, 90):
            for fill in (False, True):
                out = render(frame_of(img, rotation), OutputOptions(width=640, height=360, fill=fill))
                assert out.size == (640, 360)




def test_toggling_fill_mid_frame_uses_one_snapshot(monkeypatch):
    """The GUI thread flips "Crop to fill" while the session thread renders a frame."""
    from camandroid_desktop import video

    opts = OutputOptions(width=1920, height=1080, fill=True)
    real = video._scaled_size

    def scaled_then_toggle(size, swap, o):
        result = real(size, swap, o)
        opts.fill = not opts.fill  # the click lands between sizing and composing
        return result

    monkeypatch.setattr(video, "_scaled_size", scaled_then_toggle)
    renderer = video.Renderer(opts)
    portrait = frame_of(Image.new("RGB", (1080, 1920), (0, 255, 0)))
    for _ in range(4):
        assert renderer.render(portrait).shape == (1080, 1920, 3)


def test_compose_never_overflows_the_canvas():
    from camandroid_desktop.video import compose

    big = np.zeros((3413, 1920, 3), dtype=np.uint8)
    assert compose(big, 0, OutputOptions(width=1920, height=1080, fill=False, mirror=False)).shape == (1080, 1920, 3)
