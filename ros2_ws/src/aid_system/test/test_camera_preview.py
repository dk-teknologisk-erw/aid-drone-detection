from io import BytesIO

from PIL import Image

from aid_system.camera_preview import decode_preview


def test_decode_preview_preserves_aspect_ratio_and_bounds_size():
    source = Image.new("RGB", (1920, 1080), color=(20, 40, 60))
    encoded = BytesIO()
    source.save(encoded, format="JPEG")

    preview = decode_preview(encoded.getvalue(), (480, 270))

    assert preview.mode == "RGB"
    assert preview.size == (480, 270)