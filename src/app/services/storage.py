"""Validate image bytes, strip metadata, and store under generated names."""
from __future__ import annotations

import hashlib
import io
import warnings
import uuid
from pathlib import Path
from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from app.config import get_settings
from app.errors import AppError

Image.MAX_IMAGE_PIXELS = 40_000_000


def uploads_dir() -> Path:
    path = get_settings().data_dir / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_image(data: bytes) -> tuple[bytes, int, int, str, str]:
    if len(data) > get_settings().max_upload_mb * 1024 * 1024:
        raise AppError(413, "UPLOAD_TOO_LARGE", "单张图片超过大小上限")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(data))
        with probe:
            if probe.format not in {"JPEG", "PNG"}:
                raise AppError(415, "UNSUPPORTED_IMAGE", "仅支持 PNG 或 JPG")
            fmt = probe.format
            if probe.width * probe.height > 40_000_000:
                raise AppError(413, "IMAGE_TOO_LARGE", "图片像素超过上限")
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            width, height = image.size
            output = io.BytesIO()
            # Conversion copies image.info in Pillow; clear it explicitly so PNG
            # eXIf and JPEG metadata cannot survive the sanitization step.
            sanitized = image.convert("RGBA" if fmt == "PNG" and "A" in image.getbands() else "RGB")
            sanitized.info.clear()
            if fmt == "JPEG":
                sanitized.save(output, format="JPEG", quality=95)
                mime, suffix = "image/jpeg", ".jpg"
            else:
                sanitized.save(output, format="PNG")
                mime, suffix = "image/png", ".png"
            encoded = output.getvalue()
            if len(encoded) > get_settings().max_upload_mb * 1024 * 1024:
                raise AppError(413, "UPLOAD_TOO_LARGE", "净化后的图片超过大小上限")
            return encoded, width, height, mime, suffix
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AppError(413, "IMAGE_TOO_LARGE", "图片像素超过上限") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise AppError(415, "INVALID_IMAGE", "图片无法解码") from None


async def prepare_upload(upload: UploadFile, task_id: str):
    from app.db import File
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    data = await upload.read(max_bytes + 1)
    encoded, width, height, mime, suffix = validate_image(data)
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png"} or (extension == ".png") != (suffix == ".png"):
        raise AppError(415, "IMAGE_TYPE_MISMATCH", "文件扩展名与实际图片格式不符")
    file_id = str(uuid.uuid4())
    stored_name = file_id + suffix
    record = File(
        id=file_id, task_id=task_id, display_name=Path(upload.filename or "image").name,
        stored_name=stored_name, sha256=hashlib.sha256(encoded).hexdigest(),
        width_px=width, height_px=height, mime=mime,
    )
    return record, encoded


def file_path(record) -> Path:
    root = uploads_dir().resolve()
    path = (root / record.stored_name).resolve()
    if path.parent != root:
        raise AppError(404, "FILE_NOT_FOUND", "文件不存在")
    return path
