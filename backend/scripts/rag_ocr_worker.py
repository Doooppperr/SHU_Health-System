"""Extract text from one approved scanned PDF in an isolated process."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import fitz
from rapidocr import RapidOCR


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.ai.ingestion import normalize_text  # noqa: E402


def main() -> int:
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    threads = max(1, int(sys.argv[3]))
    document = fitz.open(input_path)
    ocr = RapidOCR(
        params={
            "EngineConfig.onnxruntime.intra_op_num_threads": threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        }
    )
    sections = []
    for index, page in enumerate(document):
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
        result = ocr(pixmap.tobytes("png"))
        text = normalize_text("\n".join(result.txts or ()))
        if text:
            sections.append(
                {"locator": f"第 {index + 1} 页（OCR）", "text": text}
            )
    document.close()
    output_path.write_text(
        json.dumps(sections, ensure_ascii=False), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
