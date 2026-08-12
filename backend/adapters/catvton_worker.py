from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one or more isolated local CatVTON try-on jobs."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--weights-marker")
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--steps", type=int, default=35)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def _jobs_from_manifest(manifest: dict, output: str | None) -> list[dict]:
    jobs = manifest.get("jobs")
    if jobs is None:
        if not output:
            raise SystemExit("--output is required for a single-job manifest.")
        jobs = [
            {
                "id": manifest.get("id", "single"),
                "person": manifest.get("person"),
                "garments": manifest.get("garments", []),
                "output": output,
            }
        ]
    if not isinstance(jobs, list) or not jobs:
        raise SystemExit("The try-on manifest contains no jobs.")
    for job in jobs:
        if not job.get("person") or not job.get("output"):
            raise SystemExit("Every try-on job needs a person and output path.")
        if not job.get("garments"):
            raise SystemExit("Every try-on job needs at least one supported garment.")
    return jobs


def main() -> None:
    args = parse_args()
    repo = Path(args.repo).resolve()
    cache = Path(args.cache).resolve()
    marker = (
        Path(args.weights_marker).resolve()
        if args.weights_marker
        else cache / ".aiwl-weights-ready.json"
    )
    if not repo.exists():
        raise SystemExit(f"CatVTON source is missing: {repo}")

    offline = marker.exists()
    os.environ.setdefault("HF_HOME", str(cache))
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.path.insert(0, str(repo))

    import torch
    from diffusers.image_processor import VaeImageProcessor
    from huggingface_hub import snapshot_download
    from PIL import Image

    from model.cloth_masker import AutoMasker
    from model.pipeline import CatVTONPipeline
    from utils import init_weight_dtype, resize_and_crop, resize_and_padding

    if not torch.cuda.is_available():
        raise SystemExit("CatVTON requires a CUDA GPU in this local configuration.")

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    jobs = _jobs_from_manifest(manifest, args.output)
    checkpoint = snapshot_download(
        repo_id="zhengchong/CatVTON",
        cache_dir=str(cache),
        local_files_only=offline,
    )
    dtype = init_weight_dtype("bf16")
    pipeline = CatVTONPipeline(
        base_ckpt="booksforcharlie/stable-diffusion-inpainting",
        attn_ckpt=checkpoint,
        attn_ckpt_version="mix",
        weight_dtype=dtype,
        use_tf32=True,
        device="cuda",
    )
    automasker = AutoMasker(
        densepose_ckpt=str(Path(checkpoint) / "DensePose"),
        schp_ckpt=str(Path(checkpoint) / "SCHP"),
        device="cuda",
    )
    mask_processor = VaeImageProcessor(
        vae_scale_factor=8,
        do_normalize=False,
        do_binarize=True,
        do_convert_grayscale=True,
    )

    category_map = {
        "top": "upper",
        "bottom": "lower",
        "one_piece": "overall",
        "outerwear": "outer",
    }
    results: list[dict] = []
    with torch.inference_mode():
        for job_index, job in enumerate(jobs):
            person_path = Path(job["person"]).resolve()
            current = resize_and_crop(
                Image.open(person_path).convert("RGB"),
                (args.width, args.height),
            )
            applied: list[str] = []
            for garment_index, garment in enumerate(job["garments"]):
                category = category_map.get(garment["category"])
                if category is None:
                    continue
                cloth = resize_and_padding(
                    Image.open(Path(garment["image_path"]).resolve()).convert("RGB"),
                    (args.width, args.height),
                )
                mask = automasker(current, category)["mask"]
                mask = mask_processor.blur(mask, blur_factor=9)
                generator = torch.Generator(device="cuda").manual_seed(
                    args.seed + job_index * 100 + garment_index
                )
                current = pipeline(
                    image=current,
                    condition_image=cloth,
                    mask=mask,
                    num_inference_steps=args.steps,
                    guidance_scale=2.5,
                    generator=generator,
                )[0]
                applied.append(garment["id"])

            output = Path(job["output"]).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            current.save(output, "JPEG", quality=91, optimize=True)
            results.append(
                {
                    "id": job.get("id", str(job_index)),
                    "output": str(output),
                    "applied": applied,
                }
            )

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "checkpoint": str(checkpoint),
                "device": torch.cuda.get_device_name(0),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "results": results,
                "device": torch.cuda.get_device_name(0),
                "offline_cache": offline,
            }
        )
    )


if __name__ == "__main__":
    main()
