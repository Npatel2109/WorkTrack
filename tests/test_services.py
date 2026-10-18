import base64
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from geosite.services import haversine_distance, save_data_image


class GeoServiceTests(unittest.TestCase):
    def test_haversine_zero_and_known_distance(self):
        self.assertEqual(haversine_distance(0, 0, 0, 0), 0)
        distance = haversine_distance(0, 0, 0, 0.001)
        self.assertGreater(distance, 110)
        self.assertLess(distance, 112)

    def test_haversine_rejects_invalid_coordinates(self):
        with self.assertRaises(ValueError):
            haversine_distance(91, 0, 0, 0)

    def test_save_data_image_reencodes_generated_png(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            Image.new("RGB", (12, 12), "white").save(source)
            encoded = base64.b64encode(source.read_bytes()).decode()
            filename = save_data_image(f"data:image/png;base64,{encoded}", directory)
            self.assertRegex(filename, r"^[0-9a-f]{32}\.png$")
            self.assertTrue((Path(directory) / filename).is_file())

    def test_save_data_image_rejects_invalid_data(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                save_data_image("data:image/png;base64,ZmFrZQ==", directory)


if __name__ == "__main__":
    unittest.main()
