"""Local Simplified Chinese OCR engine and Riven-card image preprocessing.

Uses `rapidocr_onnxruntime` (local CPU ONNX inference) with preprocessing
specifically tuned to suppress Warframe's purple holographic Riven background
and preserve fine punctuation (`+`, `-`, `.`, `%`) and Chinese characters.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np


@dataclass
class OCRBox:
    """Single OCR detection result."""

    text: str
    confidence: float
    bbox: List[List[float]]  # 4 corner points [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]

    @property
    def x_min(self) -> float:
        return min(pt[0] for pt in self.bbox)

    @property
    def x_max(self) -> float:
        return max(pt[0] for pt in self.bbox)

    @property
    def y_min(self) -> float:
        return min(pt[1] for pt in self.bbox)

    @property
    def y_max(self) -> float:
        return max(pt[1] for pt in self.bbox)

    @property
    def x_center(self) -> float:
        return (self.x_min + self.x_max) / 2.0

    @property
    def y_center(self) -> float:
        return (self.y_min + self.y_max) / 2.0

    @property
    def height(self) -> float:
        return max(1.0, self.y_max - self.y_min)


@dataclass
class OCRResult:
    """Structured output of a single OCR run."""

    boxes: List[OCRBox] = field(default_factory=list)
    lines: List[str] = field(default_factory=list)
    avg_confidence: float = 0.0
    elapsed_ms: float = 0.0
    preprocess_mode: str = "riven_enhanced"
    image_width: int = 0
    image_height: int = 0

    @property
    def raw_text(self) -> str:
        return "\n".join(self.lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "preprocess_mode": self.preprocess_mode,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "avg_confidence": round(self.avg_confidence, 4),
            "lines": self.lines,
            "boxes": [
                {
                    "text": b.text,
                    "confidence": round(b.confidence, 4),
                    "bbox": [[round(x, 1), round(y, 1)] for x, y in b.bbox],
                }
                for b in self.boxes
            ],
        }


class RivenImagePreprocessor:
    """Image enhancement pipeline tailored for Warframe Riven Mod cards.

    Riven cards feature bright pale-lavender/white text rendered over a noisy,
    saturated dark-purple holographic background. Standard thresholding often
    destroys thin minus signs (`-`) and decimal points (`.`). Instead, this
    pipeline:
      1. Upscales smaller cards so punctuation and strokes have enough pixels.
      2. Suppresses saturated purple background noise via LAB/HSV luminance-
         saturation separation.
      3. Applies edge-preserving bilateral denoising + CLAHE contrast boost.
      4. Lightly sharpens edges to keep `+`, `-`, `.`, and `%` crisp.
    """

    VALID_MODES = ("riven_enhanced", "riven_inverted", "grayscale_clahe", "raw")

    def __init__(
        self,
        target_min_height: int = 960,
        max_scale: float = 2.5,
        clahe_clip_limit: float = 2.6,
        clahe_grid_size: Tuple[int, int] = (8, 8),
        sharpen_strength: float = 0.65,
    ) -> None:
        self.target_min_height = target_min_height
        self.max_scale = max_scale
        self.clahe_clip_limit = clahe_clip_limit
        self.clahe_grid_size = clahe_grid_size
        self.sharpen_strength = sharpen_strength

    def _adaptive_upscale(self, image: np.ndarray) -> np.ndarray:
        """Upscale small card crops with bicubic interpolation."""
        height = image.shape[0]
        if height <= 0 or height >= self.target_min_height:
            return image
        scale = min(self.max_scale, self.target_min_height / float(height))
        if scale <= 1.05:
            return image
        new_w = int(round(image.shape[1] * scale))
        new_h = int(round(image.shape[0] * scale))
        return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

    def _suppress_purple_background(self, bgr: np.ndarray) -> np.ndarray:
        """Extract a high-contrast grayscale map emphasizing pale Riven text.

        Riven text has high luminance (L / V) and relatively low saturation (S),
        while the purple smoky background has higher saturation. Combining the
        perceptual LAB L-channel with a saturation-penalized HSV V-channel
        strongly suppresses purple nebula noise without hard-binarizing strokes.
        """
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        l_chan = lab[:, :, 0].astype(np.float32)

        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        s_chan = hsv[:, :, 1].astype(np.float32)
        v_chan = hsv[:, :, 2].astype(np.float32)

        # Penalize highly saturated purple pixels while keeping bright pale text
        desaturated_v = np.clip(v_chan - 0.32 * s_chan, 0.0, 255.0)
        combined = 0.55 * l_chan + 0.45 * desaturated_v
        return np.clip(combined, 0.0, 255.0).astype(np.uint8)

    def _unsharp_mask(self, gray: np.ndarray) -> np.ndarray:
        """Apply gentle unsharp masking to clarify `.` , `-` , `+` , and `%`."""
        blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=1.2)
        sharpened = cv2.addWeighted(
            gray,
            1.0 + self.sharpen_strength,
            blurred,
            -self.sharpen_strength,
            0.0,
        )
        return sharpened

    def process(self, bgr_image: np.ndarray, mode: str = "riven_enhanced") -> np.ndarray:
        """Run preprocessing and return a 3-channel BGR image ready for RapidOCR."""
        if mode not in self.VALID_MODES:
            raise ValueError(f"Unknown preprocess mode '{mode}'. Valid: {self.VALID_MODES}")

        if bgr_image is None or bgr_image.size == 0:
            raise ValueError("Input image is empty.")

        upscaled = self._adaptive_upscale(bgr_image)

        if mode == "raw":
            return upscaled

        if mode == "grayscale_clahe":
            gray = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)
            denoised = cv2.bilateralFilter(gray, d=5, sigmaColor=40, sigmaSpace=40)
            clahe = cv2.createCLAHE(
                clipLimit=self.clahe_clip_limit,
                tileGridSize=self.clahe_grid_size,
            )
            enhanced = clahe.apply(denoised)
            sharpened = self._unsharp_mask(enhanced)
            return cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)

        # `riven_enhanced` and `riven_inverted`
        # Warframe Riven cards have bright pale-lavender text over a subtle dark
        # diamond-mesh background. Aggressive local CLAHE amplifies the dark mesh
        # dots and can clip leading '-' signs or turn the reroll icon into '9'.
        # Instead, we smoothly suppress dark background texture via soft black-point
        # clipping + bilateral denoising, preserving crisp text strokes and signs.
        denoised_bgr = cv2.bilateralFilter(upscaled, d=5, sigmaColor=25, sigmaSpace=25)
        text_map = self._suppress_purple_background(denoised_bgr)

        # Soft background floor: push dark purple diamond-mesh noise smoothly to black
        # while expanding the bright lavender/white text dynamic range.
        low_p, high_p = np.percentile(text_map, (48, 99.6))
        if high_p - low_p > 25:
            stretched = np.clip(
                (text_map.astype(np.float32) - low_p) * (255.0 / (high_p - low_p)),
                0,
                255,
            ).astype(np.uint8)
        else:
            stretched = text_map

        # Very gentle sharpening to keep '-', '.', '%' crisp without haloing
        blurred = cv2.GaussianBlur(stretched, (0, 0), sigmaX=1.0)
        sharpened = cv2.addWeighted(stretched, 1.35, blurred, -0.35, 0.0)

        if mode == "riven_inverted":
            sharpened = cv2.bitwise_not(sharpened)

        return cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)


def _needs_space_between(left_text: str, right_text: str) -> bool:
    """Decide whether to insert a space when merging two horizontally adjacent OCR fragments."""
    if not left_text or not right_text:
        return False
    left_ch = left_text[-1]
    right_ch = right_text[0]
    if left_ch.isspace() or right_ch.isspace():
        return False
    # If both are CJK characters, join without inserting an artificial space
    if ("\u4e00" <= left_ch <= "\u9fff") and ("\u4e00" <= right_ch <= "\u9fff"):
        return False
    # Keep sign attached to number (e.g. '+' and '145.2%')
    if left_ch in "+-−x×" and (right_ch.isdigit() or right_ch == "."):
        return False
    # Keep percent sign attached to preceding number
    if right_ch in "%％" and (left_ch.isdigit() or left_ch == "."):
        return False
    return True


def merge_boxes_to_lines(
    boxes: Sequence[OCRBox],
    y_tolerance_ratio: float = 0.55,
) -> List[str]:
    """Sort OCR boxes in reading order and merge fragments sitting on the same text line.

    RapidOCR occasionally splits a stat line into two horizontal boxes such as
    `+145.2%` and `暴击几率`. Merging boxes with overlapping vertical centers
    restores clean single lines before regex parsing.
    """
    if not boxes:
        return []

    sorted_boxes = sorted(boxes, key=lambda b: (b.y_center, b.x_min))
    rows: List[List[OCRBox]] = []

    for box in sorted_boxes:
        placed = False
        for row in rows:
            avg_y = sum(b.y_center for b in row) / len(row)
            avg_h = sum(b.height for b in row) / len(row)
            if abs(box.y_center - avg_y) <= max(10.0, avg_h * y_tolerance_ratio):
                row.append(box)
                placed = True
                break
        if not placed:
            rows.append([box])

    # Sort rows top-to-bottom, and boxes within each row left-to-right
    rows.sort(key=lambda r: sum(b.y_center for b in r) / len(r))

    merged_lines: List[str] = []
    for row in rows:
        row.sort(key=lambda b: b.x_min)
        line_text = ""
        for box in row:
            piece = box.text.strip()
            if not piece:
                continue
            if not line_text:
                line_text = piece
            elif _needs_space_between(line_text, piece):
                line_text = f"{line_text} {piece}"
            else:
                line_text = f"{line_text}{piece}"
        if line_text:
            merged_lines.append(line_text)

    return merged_lines


class RivenOCREngine:
    """Local Simplified Chinese OCR wrapper backed by `rapidocr_onnxruntime`."""

    def __init__(
        self,
        preprocess_mode: str = "riven_enhanced",
        min_confidence: float = 0.45,
        preprocessor: Optional[RivenImagePreprocessor] = None,
        ocr_kwargs: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.preprocess_mode = preprocess_mode
        self.min_confidence = min_confidence
        self.preprocessor = preprocessor or RivenImagePreprocessor()
        self._ocr_kwargs = ocr_kwargs or {}
        self._engine: Any = None
        self._engine_lock = threading.RLock()
        self._active_ocr_lang: str = "zh_CN"

    @property
    def active_ocr_lang(self) -> str:
        return self._active_ocr_lang

    def reload_for_language(
        self,
        lang_code: str,
        *,
        rec_model_path: Optional[str | Path] = None,
        rec_keys_path: Optional[str | Path] = None,
    ) -> bool:
        """Hot-reload RapidOCR recognition model & dictionary for a newly selected language."""
        with self._engine_lock:
            self._active_ocr_lang = lang_code
            updated = {
                k: v
                for k, v in self._ocr_kwargs.items()
                if k not in ("rec_model_path", "rec_keys_path")
            }
            if rec_model_path and Path(rec_model_path).is_file():
                updated["rec_model_path"] = str(Path(rec_model_path).resolve())
            if rec_keys_path and Path(rec_keys_path).is_file():
                updated["rec_keys_path"] = str(Path(rec_keys_path).resolve())
            self._ocr_kwargs = updated
            self._engine = None
            return True

    def _ensure_engine(self) -> Any:
        with self._engine_lock:
            if self._engine is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                except ImportError as exc:
                    raise ImportError(
                        "Missing dependency 'rapidocr_onnxruntime'. "
                        "Please run: pip install -r requirements.txt"
                    ) from exc

                # Default parameters tuned for small crisp game UI text
                params = {
                    "det_box_thresh": 0.4,
                    "det_unclip_ratio": 1.7,
                }
                params.update(self._ocr_kwargs)
                try:
                    self._engine = RapidOCR(**params)
                except TypeError:
                    # Fallback for versions with different constructor signatures
                    self._engine = RapidOCR()
            return self._engine

    def _run_single_pass(
        self,
        bgr_image: np.ndarray,
        mode: str,
        debug_dir: Optional[str | Path] = None,
    ) -> OCRResult:
        t0 = time.perf_counter()
        processed = self.preprocessor.process(bgr_image, mode=mode)

        if debug_dir is not None:
            out_dir = Path(debug_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"preprocessed_{mode}.png"
            ok, encoded = cv2.imencode(".png", processed)
            if ok:
                out_path.write_bytes(encoded.tobytes())

        # Serialize RapidOCR / ONNXRuntime inference against concurrent workers & reload_for_language()
        with self._engine_lock:
            engine = self._ensure_engine()
            raw_result, _ = engine(processed)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        boxes: List[OCRBox] = []
        if raw_result:
            for item in raw_result:
                # RapidOCR item format: [bbox, text, score]
                bbox, text, score = item[0], str(item[1]), float(item[2])
                if score < self.min_confidence:
                    continue
                clean_text = text.strip()
                if not clean_text:
                    continue
                boxes.append(OCRBox(text=clean_text, confidence=score, bbox=bbox))

        lines = merge_boxes_to_lines(boxes)
        avg_conf = (
            sum(b.confidence for b in boxes) / len(boxes) if boxes else 0.0
        )
        return OCRResult(
            boxes=boxes,
            lines=lines,
            avg_confidence=avg_conf,
            elapsed_ms=elapsed_ms,
            preprocess_mode=mode,
            image_width=int(processed.shape[1]),
            image_height=int(processed.shape[0]),
        )

    def recognize(
        self,
        bgr_image: np.ndarray,
        mode: Optional[str] = None,
        fallback_to_raw: bool = True,
        debug_dir: Optional[str | Path] = None,
    ) -> OCRResult:
        """Recognize text on a Riven card image.

        If `fallback_to_raw` is enabled and the primary enhanced pass finds very
        few lines (< 3) or low average confidence (< 0.72), it automatically runs
        a fallback pass on `raw` and returns whichever pass yields a higher score.
        """
        chosen_mode = mode or self.preprocess_mode
        primary = self._run_single_pass(bgr_image, mode=chosen_mode, debug_dir=debug_dir)

        if (
            fallback_to_raw
            and chosen_mode != "raw"
            and (len(primary.lines) < 3 or primary.avg_confidence < 0.72)
        ):
            fallback = self._run_single_pass(bgr_image, mode="raw", debug_dir=debug_dir)
            primary_score = len(primary.lines) * primary.avg_confidence
            fallback_score = len(fallback.lines) * fallback.avg_confidence
            if fallback_score > primary_score:
                return fallback

        return primary
