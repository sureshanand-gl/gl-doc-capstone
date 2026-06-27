"""OpenCV layout-worker sidecar returning coarse document regions for preview overlays."""

import argparse
import json
import sys
from pathlib import Path

import cv2


def detect_regions(image_path: Path) -> dict:
    image = cv2.imread(str(image_path))
    if image is None:
        return {"status": "decode_error", "regions": []}

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 5))
    merged = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    regions = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < 500 or w < 20 or h < 12:
            continue
        regions.append(
            {
                "bbox": [int(x), int(y), int(x + w), int(y + h)],
                "class_id": 0,
                "score": 1.0,
            }
        )

    regions.sort(key=lambda region: (region["bbox"][1], region["bbox"][0]))
    return {"status": "ready", "regions": regions[:40]}


def run_batch_mode() -> int:
    for raw_line in sys.stdin:
        image_path = Path(raw_line.strip())
        if not raw_line.strip():
            continue
        print(json.dumps(detect_regions(image_path)), flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.batch:
        return run_batch_mode()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
