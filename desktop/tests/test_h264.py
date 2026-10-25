from fractions import Fraction

import pytest
from PIL import Image

from camandroid_desktop.protocol import FLAG_KEY_FRAME, Frame
from camandroid_desktop.video import OutputOptions, Renderer, available_codecs

from .test_video import is_blue, is_red, two_tone

av = pytest.importorskip("av")
if "libx264" not in av.codecs_available:
    pytest.skip("this FFmpeg build has no H.264 encoder for test streams", allow_module_level=True)


def h264_stream(frames=10, size=(320, 240), color=(255, 0, 0)):
    """Encodes a few frames like the phone does: Annex-B, no B-frames, SPS/PPS before key frames."""
    enc = av.CodecContext.create("libx264", "w")
    enc.width, enc.height, enc.pix_fmt = size[0], size[1], "yuv420p"
    enc.time_base = Fraction(1, 30)
    enc.options = {"tune": "zerolatency", "bframes": "0", "x264-params": "repeat-headers=1"}
    img = two_tone(*size) if color is None else Image.new("RGB", size, color)
    out = []
    for i in range(frames):
        vf = av.VideoFrame.from_image(img).reformat(format="yuv420p")
        vf.pts = i
        out += [Frame(i, 0, FLAG_KEY_FRAME if p.is_keyframe else 0, bytes(p)) for p in enc.encode(vf)]
    out += [Frame(0, 0, FLAG_KEY_FRAME if p.is_keyframe else 0, bytes(p)) for p in enc.encode(None)]
    return out


def test_h264_frames_render_immediately():
    stream = h264_stream(color=None)
    assert stream[0].key_frame
    renderer = Renderer(OutputOptions(width=320, height=240, mirror=False))
    outputs = [renderer.render(f) for f in stream]
    # Low delay: every access unit produces a picture straight away.
    assert all(o is not None for o in outputs)
    assert renderer.codec == "h264"
    assert is_red(outputs[0][120, 20]) and is_blue(outputs[0][120, 300])


def test_h264_waits_for_key_frame():
    stream = h264_stream()
    renderer = Renderer(OutputOptions(width=640, height=360))
    assert renderer.render(stream[1]) is None  # delta frame before any key frame is skipped
    out = renderer.render(stream[0])
    assert out.shape == (360, 640, 3)


def test_h264_is_preferred_when_available():
    assert available_codecs() == ["h264", "jpeg"]
