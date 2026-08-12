from __future__ import annotations

import io
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from PIL import Image, ImageDraw
from pillow_heif import from_pillow


TEST_STORAGE = tempfile.TemporaryDirectory(prefix="ai-wardrobe-lab-test-")
os.environ["AIWL_DATA_DIR"] = TEST_STORAGE.name
os.environ["OPENAI_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402
from backend.app import app  # noqa: E402
from backend.mobile_sessions import mobile_sessions  # noqa: E402
from backend.outfit_engine import generate_combinations  # noqa: E402
from backend.tournament import (  # noqa: E402
    conservative_cost_usd,
    estimate_full_panel_calls,
    estimate_tournament_calls,
    select_diverse,
)


def garment_image(color: str, shape: str = "shirt") -> bytes:
    image = Image.new("RGB", (520, 620), "white")
    draw = ImageDraw.Draw(image)
    if shape == "shirt":
        draw.polygon([(120, 150), (210, 90), (310, 90), (400, 150), (340, 240), (340, 530), (180, 530), (180, 240)], fill=color)
    elif shape == "pants":
        draw.polygon([(170, 70), (350, 70), (330, 550), (265, 550), (250, 245), (230, 550), (165, 550)], fill=color)
    else:
        draw.rounded_rectangle((70, 240, 450, 430), radius=70, fill=color)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


def heic_garment_image(color: str) -> bytes:
    image = Image.new("RGB", (420, 520), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((90, 55, 330, 470), radius=38, fill=color)
    buffer = io.BytesIO()
    from_pillow(image).save(buffer, quality=86)
    return buffer.getvalue()


class AIWardrobeLabApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def tearDown(self) -> None:
        mobile_sessions.stop()

    def test_tournament_protocol_math_and_diversity(self) -> None:
        self.assertEqual(estimate_tournament_calls(720), 114)
        self.assertEqual(estimate_full_panel_calls(720), 540)
        self.assertEqual(
            conservative_cost_usd("gpt-5.6-luna", 2000, 1000),
            0.0085,
        )
        scored = [
            {"outfit_id": "a1", "base_key": "a", "score": {"overall": 9.5}},
            {"outfit_id": "a2", "base_key": "a", "score": {"overall": 9.4}},
            {"outfit_id": "b1", "base_key": "b", "score": {"overall": 9.0}},
            {"outfit_id": "c1", "base_key": "c", "score": {"overall": 8.5}},
        ]
        selected = select_diverse(scored, count=3, max_per_base=1)
        self.assertEqual([row["outfit_id"] for row in selected], ["a1", "b1", "c1"])

    def test_accessories_can_stack_in_local_combinations(self) -> None:
        items = [
            {"id": "top", "category": "top", "dominant_color": "black", "available": True},
            {"id": "bottom", "category": "bottom", "dominant_color": "tan", "available": True},
            {"id": "hat", "category": "accessory", "dominant_color": "black", "available": True},
            {"id": "watch", "category": "accessory", "dominant_color": "blue", "available": True},
            {"id": "ring", "category": "accessory", "dominant_color": "gray", "available": True},
        ]

        candidates, considered = generate_combinations(items, limit=12)

        self.assertEqual(considered, 8)
        accessory_counts = {
            sum(item["category"] == "accessory" for item in candidate["items"])
            for candidate in candidates
        }
        self.assertEqual(accessory_counts, {0, 1, 2, 3})

    def test_local_wardrobe_privacy_and_shortlist_flow(self) -> None:
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertFalse(health.json()["openai_configured"])
        self.assertEqual(health.json()["focus_group"]["max_panel_size"], 6)
        self.assertEqual(health.json()["focus_group"]["tournament_budget_usd"], 3.0)
        self.assertIsNone(self.client.get("/api/tournaments/current").json())

        uploads = [
            ("Black shirt", "top", "#202020", "shirt"),
            ("Blue pants", "bottom", "#244c85", "pants"),
            ("White shoes", "shoes", "#c8c5bd", "shoes"),
        ]
        item_ids = []
        for name, category, color, shape in uploads:
            response = self.client.post(
                "/api/items",
                data={"name": name, "category": category},
                files={"file": (f"{name}.jpg", garment_image(color, shape), "image/jpeg")},
            )
            self.assertEqual(response.status_code, 200, response.text)
            item_ids.append(response.json()["id"])

        items = self.client.get("/api/items").json()
        self.assertEqual(len(items), 3)
        self.assertTrue(all(item["available"] for item in items))

        worn = self.client.patch(
            f"/api/items/{item_ids[0]}",
            json={"last_worn": date.today().isoformat()},
        )
        self.assertEqual(worn.status_code, 200)
        self.assertEqual(worn.json()["last_worn"], date.today().isoformat())

        renamed = self.client.patch(
            f"/api/items/{item_ids[0]}",
            json={"name": "Renamed black shirt"},
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["name"], "Renamed black shirt")

        reference = self.client.post(
            "/api/reference",
            files={"file": ("reference.jpg", garment_image("#ba8874"), "image/jpeg")},
        )
        self.assertEqual(reference.status_code, 200, reference.text)
        self.assertIn("privacy", reference.json()["privacy_media_url"])

        shortlist = self.client.post("/api/outfits/generate", json={"limit": 8})
        self.assertEqual(shortlist.status_code, 200, shortlist.text)
        body = shortlist.json()
        self.assertGreaterEqual(body["combinations_considered"], 1)
        self.assertGreaterEqual(len(body["outfits"]), 1)
        self.assertTrue(body["outfits"][0]["board_media_url"].startswith("/media/outfits/"))
        def fake_tryon(jobs, manifest_path):
            results = []
            for job in jobs:
                output = Path(job["output"])
                output.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (768, 1024), "#8a7668").save(output, "JPEG")
                results.append({"id": job["id"], "output": str(output), "applied": []})
            return {"results": results, "device": "test GPU", "offline_cache": True}

        fake_catvton_marker = Path(TEST_STORAGE.name) / "catvton-ready.json"
        fake_catvton_python = Path(TEST_STORAGE.name) / "catvton-python.exe"
        fake_catvton_marker.touch()
        fake_catvton_python.touch()
        with (
            patch("backend.app.settings.CATVTON_READY_MARKER", fake_catvton_marker),
            patch("backend.app.settings.CATVTON_PYTHON", fake_catvton_python),
            patch("backend.app.run_tryon_batch", side_effect=fake_tryon),
        ):
            tryon = self.client.post(
                "/api/tryon", json={"outfit_ids": [body["outfits"][0]["id"]]}
            )
            cached_tryon = self.client.post(
                "/api/tryon", json={"outfit_ids": [body["outfits"][0]["id"]]}
            )
        self.assertEqual(tryon.status_code, 200, tryon.text)
        self.assertEqual(tryon.json()["generated"], 1)
        self.assertEqual(tryon.json()["outfits"][0]["tryon"]["status"], "complete")
        self.assertTrue(tryon.json()["outfits"][0]["tryon"]["privacy_media_url"])
        self.assertEqual(cached_tryon.status_code, 200, cached_tryon.text)
        self.assertEqual(cached_tryon.json()["generated"], 0)
        self.assertEqual(cached_tryon.json()["cache_hits"], 1)

        panel = self.client.post(
            "/api/evaluations",
            json={
                "audience": "Women aged 18-25 at a bar",
                "impression": "Fashionable, confident, and approachable",
                "outfit_ids": [body["outfits"][0]["id"]],
            },
        )
        self.assertEqual(panel.status_code, 409)
        self.assertIn("OPENAI_API_KEY", panel.json()["detail"])

        tournament = self.client.post(
            "/api/tournaments",
            json={
                "audience": "Women aged 18-25 at a bar",
                "impression": "Fashionable, confident, and approachable",
                "mode": "full_panel",
            },
        )
        self.assertEqual(tournament.status_code, 409)
        self.assertIn("OPENAI_API_KEY", tournament.json()["detail"])

        invalid_panel_size = self.client.post(
            "/api/evaluations",
            json={
                "audience": "Women aged 18-25 at a bar",
                "impression": "Fashionable, confident, and approachable",
                "outfit_ids": [body["outfits"][0]["id"]],
                "panel_size": 7,
            },
        )
        self.assertEqual(invalid_panel_size.status_code, 422)

        async def fake_panel(**kwargs):
            ranking = [
                {
                    "panel_label": label,
                    "fashionable": 8.0,
                    "confident": 8.0,
                    "approachable": 7.0,
                    "audience_fit": 8.0,
                    "overall": 7.8,
                    "consensus": "Coordinated and intentional.",
                    "rationales": ["Coordinated and intentional."],
                    "concerns": [],
                    "responses": kwargs["panel_size"],
                }
                for label in kwargs["labels"]
            ]
            usage = [
                {
                    "persona_key": f"test-{index}",
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cached_tokens": 0,
                }
                for index in range(kwargs["panel_size"])
            ]
            return ranking, usage

        custom_panel_payload = {
            "audience": "Women aged 18-25 at a bar",
            "impression": "Fashionable, confident, and approachable",
            "outfit_ids": [body["outfits"][0]["id"]],
            "panel_size": 3,
        }
        with (
            patch("backend.app.settings.OPENAI_API_KEY", "test-key"),
            patch("backend.app.run_panel", side_effect=fake_panel) as panel_runner,
        ):
            custom_panel = self.client.post(
                "/api/evaluations", json=custom_panel_payload
            )
            cached_panel = self.client.post(
                "/api/evaluations", json=custom_panel_payload
            )
        self.assertEqual(custom_panel.status_code, 200, custom_panel.text)
        self.assertEqual(custom_panel.json()["panel_size"], 3)
        self.assertEqual(custom_panel.json()["api_calls"], 3)
        self.assertFalse(custom_panel.json()["cache_hit"])
        self.assertEqual(cached_panel.status_code, 200, cached_panel.text)
        self.assertEqual(cached_panel.json()["api_calls"], 0)
        self.assertTrue(cached_panel.json()["cache_hit"])
        self.assertEqual(panel_runner.await_count, 1)
        self.assertEqual(panel_runner.await_args.kwargs["panel_size"], 3)

    def test_private_mobile_session_heic_and_remote_boundary(self) -> None:
        with patch(
            "backend.mobile_sessions.discover_private_ipv4",
            return_value="192.168.123.10",
        ):
            started = self.client.post("/api/mobile/session")
        self.assertEqual(started.status_code, 200, started.text)
        admin = started.json()
        self.assertTrue(admin["active"])
        self.assertRegex(admin["pin"], r"^\d{6}$")
        self.assertTrue(admin["qr_media_url"].startswith("/media/cache/"))
        parsed = urlparse(admin["mobile_url"])
        self.assertEqual(parsed.hostname, "192.168.123.10")
        self.assertEqual(parsed.port, 3000)
        join_token = parse_qs(parsed.fragment)["join"][0]

        wrong = self.client.post(
            "/api/mobile/auth",
            json={"join_token": join_token, "pin": "000000" if admin["pin"] != "000000" else "000001"},
        )
        self.assertEqual(wrong.status_code, 401)
        authenticated = self.client.post(
            "/api/mobile/auth",
            json={"join_token": join_token, "pin": admin["pin"]},
        )
        self.assertEqual(authenticated.status_code, 200, authenticated.text)
        access_token = authenticated.json()["access_token"]
        authorization = {"Authorization": f"Bearer {access_token}"}

        remote = TestClient(app, client=("192.168.123.21", 50123))
        try:
            preflight = remote.options(
                "/api/mobile/items",
                headers={
                    "Origin": "http://192.168.123.45:3000",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization",
                },
            )
            self.assertEqual(preflight.status_code, 200, preflight.text)
            self.assertEqual(
                preflight.headers.get("access-control-allow-origin"),
                "http://192.168.123.45:3000",
            )
            fallback_preflight = remote.options(
                "/api/mobile/items",
                headers={
                    "Origin": "http://192.168.123.45:3001",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization",
                },
            )
            self.assertEqual(fallback_preflight.status_code, 200, fallback_preflight.text)
            self.assertEqual(
                fallback_preflight.headers.get("access-control-allow-origin"),
                "http://192.168.123.45:3001",
            )
            self.assertEqual(remote.get("/api/items").status_code, 403)
            self.assertEqual(remote.get("/api/mobile/session/current").status_code, 403)
            status = remote.get("/api/mobile/status", headers=authorization)
            self.assertEqual(status.status_code, 200, status.text)
            self.assertIsNone(status.json()["pin"])
            self.assertIsNone(status.json()["mobile_url"])

            upload = remote.post(
                "/api/mobile/items",
                data={"name": "Phone HEIC", "category": "outerwear"},
                files={"file": ("phone.heic", heic_garment_image("#9e3f64"), "image/heic")},
                headers=authorization,
            )
            self.assertEqual(upload.status_code, 200, upload.text)
            self.assertEqual(upload.json()["item"]["category"], "outerwear")
            self.assertEqual(upload.json()["session"]["uploads"], 1)
            self.assertEqual(
                remote.get(upload.json()["item"]["media_url"]).status_code,
                403,
            )

            reference = remote.post(
                "/api/mobile/reference",
                files={"file": ("body.jpg", garment_image("#5a9e83"), "image/jpeg")},
                headers=authorization,
            )
            self.assertEqual(reference.status_code, 200, reference.text)
            self.assertTrue(reference.json()["session"]["reference_uploaded"])

            finished = remote.post("/api/mobile/finish", headers=authorization)
            self.assertEqual(finished.status_code, 200, finished.text)
            self.assertEqual(
                remote.get("/api/mobile/status", headers=authorization).status_code,
                410,
            )
        finally:
            remote.close()

        self.assertFalse(self.client.get("/api/mobile/session/current").json()["active"])


if __name__ == "__main__":
    unittest.main()
