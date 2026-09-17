from io import BytesIO

from PIL import Image


def decode_preview(data: bytes, maximum_size: tuple[int, int]) -> Image.Image:
    with Image.open(BytesIO(data)) as image:
        image.load()
        preview = image.convert("RGB")
    preview.thumbnail(maximum_size, Image.Resampling.LANCZOS)
    return preview