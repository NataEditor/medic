import tempfile
import unittest
from pathlib import Path

from app.tasks.image_processing import optimize_medical_image
from PIL import Image


class TestImageProcessing(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.img_path = Path(self.temp_dir.name) / "test_scan.png"

        # Создаём тестовое тестовое RGB изображение 800x600
        img = Image.new("RGB", (800, 600), color="blue")
        img.save(self.img_path, "PNG")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_optimize_and_generate_thumbnail(self) -> None:
        res = optimize_medical_image(str(self.img_path))
        self.assertEqual(res["status"], "ok")

        thumb_path = Path(res["thumbnail"])
        self.assertTrue(thumb_path.exists())

        with Image.open(thumb_path) as thumb:
            self.assertLessEqual(thumb.width, 400)
            self.assertLessEqual(thumb.height, 400)


if __name__ == "__main__":
    unittest.main()
