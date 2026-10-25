"""The Windows build decodes H.264 with Media Foundation; make sure CI really tests that path."""

import os
import sys

import pytest

from camandroid_desktop.video import H264Decoder, OutputOptions, Renderer

from .test_h264 import h264_stream, is_blue, is_red

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not os.environ.get("CAMANDROID_CODEC_DIR"),
    reason="set CAMANDROID_CODEC_DIR to the folder with camandroid-h264.dll (Windows)",
)


def test_media_foundation_is_used():
    dec = H264Decoder()
    assert dec.name == "Media Foundation"
    dec.close()


@pytest.mark.parametrize("size", [(320, 240), (1280, 720), (1920, 1080)])
def test_decodes_and_scales(size):
    stream = h264_stream(frames=5, size=size, color=None)
    renderer = Renderer(OutputOptions(width=640, height=360, mirror=False))
    outputs = [renderer.render(f) for f in stream]
    assert renderer.decoder_name == "Media Foundation"
    pictures = [o for o in outputs if o is not None]
    assert len(pictures) >= len(stream) - 1, "low-latency mode should output (almost) every frame at once"
    assert outputs[0] is not None, "the first key frame must come out immediately"
    pic = pictures[-1]
    assert pic.shape == (360, 640, 3)
    # Letterboxed 4:3 in 16:9, or full width for 16:9 input: sample well inside each half.
    y = 180
    left, right = pic[y, 320 - 100], pic[y, 320 + 100]
    assert is_red(left) and is_blue(right), (left, right)
    renderer.close()
