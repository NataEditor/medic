from pathlib import Path

from loguru import logger
from PIL import Image, ImageOps

from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.image_processing.optimize_medical_image")
def optimize_medical_image(file_path_str: str) -> dict[str, str]:
    """Сжатие сканов/снимков и создание превью-миниатюры (Pillow)."""
    src_path = Path(file_path_str)
    if not src_path.exists():
        logger.warning("Файл для оптимизации не найден: {}", file_path_str)
        return {"status": "file_not_found"}

    try:
        with Image.open(src_path) as img:
            # Исправляем ориентацию по EXIF (актуально для фото со смартфонов)
            img = ImageOps.exif_transpose(img)

            # Конвертируем в RGB если RGBA/CMYK
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            # Генерируем миниатюру
            thumb_path = src_path.with_name(f"{src_path.stem}_thumb.jpg")
            thumb = img.copy()
            thumb.thumbnail((400, 400), Image.Resampling.LANCZOS)
            thumb.save(thumb_path, "JPEG", quality=80, optimize=True)

            # Оптимизируем оригинал, если слишком большой (>2560px)
            max_dimension = 2560
            if max(img.size) > max_dimension:
                img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                img.save(src_path, "JPEG", quality=85, optimize=True)

            logger.info("Изображение {} успешно оптимизировано", src_path.name)
            return {
                "status": "ok",
                "original": str(src_path),
                "thumbnail": str(thumb_path),
            }
    except Exception as exc:
        logger.error("Ошибка при обработке изображения {}: {}", file_path_str, exc)
        return {"status": "error", "error": str(exc)}
