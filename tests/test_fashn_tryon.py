from __future__ import annotations

import base64
import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from fashn_tryon.fashn_client import FashnApiError
from fashn_tryon.image_prep import preprocess_image
from fashn_tryon.runner import TryonRunner, CliRuntimeError, resolve_model_images
from fashn_tryon.store import create_run_dir, slugify_filename


def _make_png_data_uri(width: int = 4, height: int = 4, color: str = "red") -> str:
    buf = BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _make_image(path: Path, color: str = "white", size: tuple[int, int] = (8, 8), mode: str = "RGB") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, color).save(path)


def _default_options() -> dict[str, object]:
    return {
        "category": "tops",
        "garment_photo_type": "model",
        "mode": "quality",
        "num_samples": 1,
        "seed": 42,
        "output_format": "png",
        "segmentation_free": True,
        "moderation_level": "permissive",
    }


def _make_runner(client, **overrides) -> TryonRunner:
    defaults = dict(
        client=client,
        model_name="tryon-v1.6",
        poll_interval=0.01,
        poll_timeout=5.0,
        max_retries=0,
        request_concurrency=1,
    )
    defaults.update(overrides)
    return TryonRunner(**defaults)


# ---------------------------------------------------------------------------
# Recording mock client — captures what the runner actually sends
# ---------------------------------------------------------------------------

class RecordingClient:
    """Records submitted payloads so tests can assert on them."""

    def __init__(self, output_data_uri: str | None = None) -> None:
        self.submitted_payloads: list[dict] = []
        self._output = output_data_uri or _make_png_data_uri()

    def run_prediction(self, payload):
        self.submitted_payloads.append(payload)
        return {"id": f"pred-{len(self.submitted_payloads)}"}

    def get_status(self, prediction_id):
        return {"status": "completed", "output": [self._output]}, {"x-fashn-credits-used": "1"}

    def download_file(self, url):
        raise AssertionError("should not be called for base64 outputs")


class FailThenSucceedClient:
    """First submit raises 503, second succeeds. Records all payloads."""

    def __init__(self) -> None:
        self.submitted_payloads: list[dict] = []
        self._output = _make_png_data_uri()

    def run_prediction(self, payload):
        self.submitted_payloads.append(payload)
        if len(self.submitted_payloads) == 1:
            raise FashnApiError(503, "Unavailable", "boom")
        return {"id": f"pred-{len(self.submitted_payloads)}"}

    def get_status(self, prediction_id):
        return {"status": "completed", "output": [self._output]}, {"x-fashn-credits-used": "1"}

    def download_file(self, url):
        raise AssertionError("should not be called")


class RemoteRetryClient:
    """First prediction fails with retryable PipelineError, retry succeeds."""

    def __init__(self) -> None:
        self.submit_count = 0

    def run_prediction(self, payload):
        self.submit_count += 1
        return {"id": f"pred-{self.submit_count}"}

    def get_status(self, prediction_id):
        if prediction_id == "pred-1":
            return {
                "status": "failed",
                "error": {"name": "PipelineError", "message": "GPU transient"},
            }, {}
        return {"status": "completed", "output": [_make_png_data_uri()]}, {"x-fashn-credits-used": "1"}

    def download_file(self, url):
        raise AssertionError("should not be called")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPayloadConstruction(unittest.TestCase):
    """Verify the runner sends correctly structured payloads to the API."""

    def test_payload_contains_data_uri_images_and_options(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            user = tmp_path / "user.png"
            look = tmp_path / "look.png"
            _make_image(user)
            _make_image(look, "black")

            client = RecordingClient()
            runner = _make_runner(client)
            runner.create_run(
                user_image=user,
                model_images=[look],
                output_dir=tmp_path / "out",
                options=_default_options(),
            )

            self.assertEqual(len(client.submitted_payloads), 1)
            payload = client.submitted_payloads[0]

            # model_name 正确
            self.assertEqual(payload["model_name"], "tryon-v1.6")

            inputs = payload["inputs"]
            # 图片是合法 data URI
            self.assertTrue(inputs["model_image"].startswith("data:image/jpeg;base64,"))
            self.assertTrue(inputs["garment_image"].startswith("data:image/jpeg;base64,"))

            # data URI 能解码成合法图片
            for key in ("model_image", "garment_image"):
                _, b64 = inputs[key].split(",", 1)
                img = Image.open(BytesIO(base64.b64decode(b64)))
                self.assertEqual(img.mode, "RGB")

            # options 透传正确
            self.assertEqual(inputs["category"], "tops")
            self.assertEqual(inputs["garment_photo_type"], "model")
            self.assertEqual(inputs["mode"], "quality")
            self.assertEqual(inputs["seed"], 42)
            self.assertTrue(inputs["return_base64"])

    def test_multi_look_sends_separate_payloads_with_same_user(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            user = tmp_path / "user.png"
            _make_image(user)
            looks = []
            for i in range(3):
                p = tmp_path / f"look-{i}.png"
                _make_image(p, ["red", "green", "blue"][i])
                looks.append(p)

            client = RecordingClient()
            runner = _make_runner(client)
            runner.create_run(
                user_image=user,
                model_images=looks,
                output_dir=tmp_path / "out",
                options=_default_options(),
            )

            self.assertEqual(len(client.submitted_payloads), 3)
            # 所有 payload 共享同一个 user image (model_image)
            user_uris = {p["inputs"]["model_image"] for p in client.submitted_payloads}
            self.assertEqual(len(user_uris), 1)
            # 每个 payload 的 garment_image 不同
            garment_uris = [p["inputs"]["garment_image"] for p in client.submitted_payloads]
            self.assertEqual(len(set(garment_uris)), 3)


class TestOutputSaving(unittest.TestCase):
    """Verify output images are correctly decoded and saved to disk."""

    def test_base64_output_saved_as_valid_image(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            user = tmp_path / "user.png"
            look = tmp_path / "look.png"
            _make_image(user)
            _make_image(look)

            output_uri = _make_png_data_uri(16, 16, "blue")
            client = RecordingClient(output_data_uri=output_uri)
            runner = _make_runner(client)
            result = runner.create_run(
                user_image=user,
                model_images=[look],
                output_dir=tmp_path / "out",
                options=_default_options(),
            )

            output_path = Path(result["jobs"][0]["output_paths"][0])
            self.assertTrue(output_path.exists())
            with Image.open(output_path) as img:
                self.assertEqual(img.size, (16, 16))

    def test_run_dir_contains_all_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            user = tmp_path / "user.png"
            look = tmp_path / "look.png"
            _make_image(user)
            _make_image(look)

            runner = _make_runner(RecordingClient())
            result = runner.create_run(
                user_image=user,
                model_images=[look],
                output_dir=tmp_path / "out",
                options=_default_options(),
            )

            run_dir = Path(result["run_dir"])
            for name in ("manifest.json", "results.json", "errors.json", "summary.txt"):
                self.assertTrue((run_dir / name).exists(), f"missing {name}")

            results = json.loads((run_dir / "results.json").read_text())
            self.assertEqual(results["status"], "ok")
            self.assertEqual(results["completed"], 1)

            # prepared 目录存在且包含预处理后的 JPEG
            prepared = list((run_dir / "prepared").glob("*.jpg"))
            self.assertGreaterEqual(len(prepared), 2)  # user + look


class TestImagePreprocessing(unittest.TestCase):
    """Verify actual image transformations."""

    def test_oversized_image_is_downscaled(self) -> None:
        with TemporaryDirectory() as tmp:
            src = Path(tmp) / "big.png"
            dst = Path(tmp) / "prepared.jpg"
            _make_image(src, size=(4000, 3000))

            meta = preprocess_image(src, dst)
            self.assertEqual(meta["original_width"], 4000)
            self.assertEqual(meta["original_height"], 3000)
            self.assertLessEqual(max(meta["prepared_width"], meta["prepared_height"]), 2000)

            with Image.open(dst) as img:
                self.assertEqual(img.format, "JPEG")
                self.assertLessEqual(max(img.size), 2000)

    def test_rgba_alpha_flattened_onto_white(self) -> None:
        with TemporaryDirectory() as tmp:
            src = Path(tmp) / "rgba.png"
            dst = Path(tmp) / "prepared.jpg"
            # 半透明红色
            Image.new("RGBA", (10, 10), (255, 0, 0, 128)).save(src)

            preprocess_image(src, dst)
            with Image.open(dst) as img:
                self.assertEqual(img.mode, "RGB")
                r, g, b = img.getpixel((5, 5))
                # 红色混合白色背景，R 应该高，B 和 G 中等偏上
                self.assertGreater(r, 200)
                self.assertGreater(g, 50)

    def test_small_image_not_upscaled(self) -> None:
        with TemporaryDirectory() as tmp:
            src = Path(tmp) / "small.png"
            dst = Path(tmp) / "prepared.jpg"
            _make_image(src, size=(100, 80))

            meta = preprocess_image(src, dst)
            self.assertEqual(meta["prepared_width"], 100)
            self.assertEqual(meta["prepared_height"], 80)


class TestRetryBehavior(unittest.TestCase):

    def test_retryable_remote_error_resubmits(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            user = tmp_path / "user.png"
            look = tmp_path / "look.png"
            _make_image(user)
            _make_image(look)

            client = RemoteRetryClient()
            runner = _make_runner(client, max_retries=1)
            result = runner.create_run(
                user_image=user,
                model_images=[look],
                output_dir=tmp_path / "out",
                options=_default_options(),
            )

            self.assertEqual(result["status"], "ok")
            # 验证确实提交了两次（第一次失败 + 重试）
            self.assertEqual(client.submit_count, 2)
            self.assertEqual(result["jobs"][0]["retry_count"], 1)

    def test_submit_failure_does_not_abort_batch(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            user = tmp_path / "user.png"
            a = tmp_path / "a.png"
            b = tmp_path / "b.png"
            _make_image(user)
            _make_image(a, "black")
            _make_image(b, "blue")

            client = FailThenSucceedClient()
            runner = _make_runner(client)
            result = runner.create_run(
                user_image=user,
                model_images=[a, b],
                output_dir=tmp_path / "out",
                options=_default_options(),
            )

            self.assertEqual(result["status"], "partial_success")
            # 验证两个 job 都尝试提交了
            self.assertEqual(len(client.submitted_payloads), 2)


class TestNormalizeOutputs(unittest.TestCase):
    """Verify _normalize_outputs handles FASHN's various response shapes."""

    def _normalize(self, output):
        return _make_runner(RecordingClient())._normalize_outputs(output)

    def test_none(self) -> None:
        self.assertEqual(self._normalize(None), [])

    def test_single_string(self) -> None:
        self.assertEqual(self._normalize("data:image/png;base64,abc"), ["data:image/png;base64,abc"])

    def test_list(self) -> None:
        self.assertEqual(self._normalize(["a", "b"]), ["a", "b"])

    def test_nested_images_key(self) -> None:
        self.assertEqual(self._normalize({"images": ["x", "y"]}), ["x", "y"])

    def test_dict_with_url(self) -> None:
        self.assertEqual(self._normalize({"url": "https://cdn.fashn.ai/out.png"}), ["https://cdn.fashn.ai/out.png"])

    def test_unsupported_type_raises(self) -> None:
        with self.assertRaises(CliRuntimeError):
            self._normalize(12345)


class TestResolveModelImages(unittest.TestCase):

    def test_filters_non_image_files(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _make_image(tmp_path / "a.png")
            _make_image(tmp_path / "b.jpg")
            (tmp_path / "c.txt").write_text("not an image")
            (tmp_path / "d.json").write_text("{}")

            result = resolve_model_images([], str(tmp_path))
            names = {p.name for p in result}
            self.assertEqual(names, {"a.png", "b.jpg"})

    def test_deduplicates(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            img = tmp_path / "a.png"
            _make_image(img)

            result = resolve_model_images([str(img)], str(tmp_path))
            self.assertEqual(len(result), 1)

    def test_empty_raises(self) -> None:
        with self.assertRaises(CliRuntimeError):
            resolve_model_images([], None)


class TestSlugify(unittest.TestCase):

    def test_spaces_and_parens(self) -> None:
        self.assertEqual(slugify_filename("My Look (1).png"), "my-look-1")

    def test_all_special_chars(self) -> None:
        self.assertEqual(slugify_filename("@#$.png"), "image")


class TestCreateRunDir(unittest.TestCase):

    def test_same_second_collision(self) -> None:
        from datetime import datetime

        class FixedDateTime:
            @classmethod
            def now(cls):
                return datetime(2026, 3, 7, 18, 0, 0)

        with TemporaryDirectory() as tmp:
            with patch("fashn_tryon.store.datetime", FixedDateTime):
                first = create_run_dir(Path(tmp))
                second = create_run_dir(Path(tmp))

            self.assertNotEqual(first, second)
            self.assertTrue(first.name.startswith("tryon_20260307_180000"))


if __name__ == "__main__":
    unittest.main()
