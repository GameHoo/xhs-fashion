from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from fashn_tryon.fashn_client import FashnApiError
from fashn_tryon.runner import TryonRunner
from fashn_tryon.store import create_run_dir


PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7ZQ1cAAAAASUVORK5CYII="
)


class SubmitOneFailOneSuccessClient:
    def __init__(self) -> None:
        self.submit_count = 0

    def run_prediction(self, payload):
        self.submit_count += 1
        if self.submit_count == 1:
            raise FashnApiError(503, "Unavailable", "boom")
        return {"id": f"pred-{self.submit_count}"}

    def get_status(self, prediction_id):
        return {"status": "completed", "output": [PNG_DATA_URI]}, {"x-fashn-credits-used": "1"}

    def download_file(self, url):
        raise AssertionError("download_file should not be called for base64 outputs")


class CompletedEmptyOutputClient:
    def run_prediction(self, payload):
        return {"id": "pred-empty"}

    def get_status(self, prediction_id):
        return {"status": "completed", "output": []}, {"x-fashn-credits-used": "1"}

    def download_file(self, url):
        raise AssertionError("download_file should not be called for empty outputs")


class FixedDateTime:
    @classmethod
    def now(cls):
        from datetime import datetime

        return datetime(2026, 3, 7, 18, 0, 0)


class FashnTryonTests(unittest.TestCase):
    def _make_image(self, path: Path, color: str) -> None:
        Image.new("RGB", (8, 8), color).save(path)

    def _default_options(self) -> dict[str, object]:
        return {
            "category": "auto",
            "garment_photo_type": "model",
            "mode": "balanced",
            "num_samples": 1,
            "seed": 42,
            "output_format": "png",
            "segmentation_free": True,
            "moderation_level": "permissive",
        }

    def test_submit_failure_is_recorded_without_aborting_batch(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            user = tmp_path / "user.png"
            look_a = tmp_path / "look-a.png"
            look_b = tmp_path / "look-b.png"
            self._make_image(user, "white")
            self._make_image(look_a, "black")
            self._make_image(look_b, "blue")

            runner = TryonRunner(
                client=SubmitOneFailOneSuccessClient(),
                model_name="tryon-v1.6",
                poll_interval=0.01,
                poll_timeout=1.0,
                max_retries=0,
                request_concurrency=1,
            )

            result = runner.create_run(
                user_image=user,
                model_images=[look_a, look_b],
                output_dir=tmp_path / "out",
                options=self._default_options(),
            )

            self.assertEqual(result["status"], "partial_success")
            self.assertEqual(result["completed"], 1)
            self.assertEqual(result["failed"], 1)
            self.assertTrue((Path(result["run_dir"]) / "results.json").exists())

            jobs_by_source = {Path(job["source_image"]).name: job for job in result["jobs"]}
            self.assertEqual(jobs_by_source["look-a.png"]["status"], "failed")
            self.assertEqual(jobs_by_source["look-b.png"]["status"], "completed")
            self.assertTrue(jobs_by_source["look-b.png"]["output_paths"])

    def test_completed_prediction_with_empty_outputs_is_failed(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            user = tmp_path / "user.png"
            look = tmp_path / "look.png"
            self._make_image(user, "white")
            self._make_image(look, "black")

            runner = TryonRunner(
                client=CompletedEmptyOutputClient(),
                model_name="tryon-v1.6",
                poll_interval=0.01,
                poll_timeout=1.0,
                max_retries=0,
                request_concurrency=1,
            )

            result = runner.create_run(
                user_image=user,
                model_images=[look],
                output_dir=tmp_path / "out",
                options=self._default_options(),
            )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["completed"], 0)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(result["jobs"][0]["output_paths"], [])
            self.assertIn("without any output images", result["jobs"][0]["error"]["message"])

            results_payload = json.loads((Path(result["run_dir"]) / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(results_payload["status"], "failed")

    def test_create_run_dir_avoids_same_second_collisions(self) -> None:
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            with patch("fashn_tryon.store.datetime", FixedDateTime):
                first = create_run_dir(output_dir)
                second = create_run_dir(output_dir)

            self.assertNotEqual(first, second)
            self.assertEqual(first.name, "tryon_20260307_180000")
            self.assertEqual(second.name, "tryon_20260307_180000_01")
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())


if __name__ == "__main__":
    unittest.main()
