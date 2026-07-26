"""Build the checked-in demo media set from openly licensed source files.

This command is intentionally separate from database seeding. It downloads
only the sources listed below, re-encodes every image without source metadata,
adds a clear non-diagnostic watermark to medical examples, and writes an
auditable manifest beside the generated files.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps


BACKEND_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_ROOT = BACKEND_ROOT / "uploads"
MANIFEST_PATH = BACKEND_ROOT / "demo_media_manifest.json"
USER_AGENT = "HealthDocDemoMedia/1.0 (open-license demo asset builder)"

COMMONS_SOURCES = {
    "ecg": {
        "title": "心电图波形",
        "filename": "ECG Paper.jpg",
        "source_url": "https://commons.wikimedia.org/wiki/File:ECG_Paper.jpg",
        "author": "美国国家心肺血液研究所（NHLBI）",
        "license": "美国联邦政府作品，公有领域",
    },
    "chest_2346": {
        "title": "胸部影像",
        "filename": "Chest X-ray 2346.jpg",
        "source_url": "https://commons.wikimedia.org/wiki/File:Chest_X-ray_2346.jpg",
        "author": "Wikimedia Commons 贡献者",
        "license": "CC0 1.0",
    },
    "chest": {
        "title": "胸部影像",
        "filename": "Chest X-Ray.jpg",
        "source_url": "https://commons.wikimedia.org/wiki/File:Chest_X-Ray.jpg",
        "author": "美国国家卫生研究院（NIH）",
        "license": "美国联邦政府作品，公有领域",
    },
    "spirometry": {
        "title": "肺功能曲线",
        "filename": "Spirometry NIH.jpg",
        "source_url": "https://commons.wikimedia.org/wiki/File:Spirometry_NIH.jpg",
        "author": "美国国家心肺血液研究所（NHLBI）",
        "license": "美国联邦政府作品，公有领域",
    },
    "blood_sem": {
        "title": "血细胞显微影像",
        "filename": "SEM blood cells.jpg",
        "source_url": "https://commons.wikimedia.org/wiki/File:SEM_blood_cells.jpg",
        "author": "美国国家癌症研究所（NCI）",
        "license": "美国联邦政府作品，公有领域",
    },
    "blood": {
        "title": "血液基础检查影像",
        "filename": "Blood cells 090304-F-5951M-108.jpg",
        "source_url": "https://commons.wikimedia.org/wiki/File:Blood_cells_090304-F-5951M-108.jpg",
        "author": "美国空军照片 / Senior Airman Eric Harris",
        "license": "美国联邦政府作品，公有领域",
    },
    "liver": {
        "title": "腹部超声影像",
        "filename": "Ultrasonography of a normal liver.jpg",
        "source_url": "https://commons.wikimedia.org/wiki/File:Ultrasonography_of_a_normal_liver.jpg",
        "author": "Mikael Häggström",
        "license": "CC0 1.0",
    },
}

REPORT_ASSIGNMENTS = (
    ("report-3-metabolic.png", "blood_sem", "代谢检查"),
    ("report-6-digestive.png", "liver", "腹部超声"),
    ("report-7-respiratory.png", "spirometry", "肺功能检查"),
    ("report-10-basic.png", "ecg", "心电检查"),
    ("report-11-basic.png", "chest_2346", "胸部影像"),
    ("report-12-basic.png", "blood", "血液基础检查"),
    ("report-13-basic.png", "chest", "胸部影像"),
    ("report-14-basic.png", "ecg", "心电检查"),
    ("report-15-basic.png", "liver", "腹部超声"),
    ("report-16-basic.png", "blood_sem", "代谢检查"),
    ("report-17-basic.png", "spirometry", "肺功能检查"),
    ("report-18-basic.png", "ecg", "心电检查"),
    ("report-19-basic.png", "chest_2346", "胸部影像"),
    ("report-20-basic.png", "blood", "血液基础检查"),
    ("report-21-basic.png", "liver", "腹部超声"),
)

UNSPLASH_PHOTOS = (
    ("photo-1629410484397-a4dcd74088a0", "Gonzalo Kenny", "体检中心走廊"),
    ("photo-1719934398679-d764c1410770", "Arturo Esparza", "候检区走廊"),
    ("photo-1519494140681-8b17d830a3e9", "Martha Dominguez de Gouveia", "医疗机构走廊"),
    ("photo-1584451049700-ec9b394f3805", "Martha Dominguez de Gouveia", "体检检查室"),
    ("photo-1495433923968-85c6751d2df6", "Cory Mogk", "医疗机构通道"),
    ("photo-1530299297082-0846efbd2cdd", "Quilia", "体检中心通道"),
    ("photo-1777269749032-d8d458ae594d", "Tasha Kostyuk", "医疗中心走廊"),
    ("photo-1551076805-e1869033e561", "Arseny Togulev", "检查床与设备"),
    ("photo-1710074213379-2a9c2653046a", "Zoshua Colah", "体检观察室"),
    ("photo-1710074213374-e68503a1b795", "Zoshua Colah", "医疗观察室"),
    ("photo-1648224394432-8830fec15349", "Annie Spratt", "医疗检查环境"),
    ("photo-1580281657702-257584239a55", "National Cancer Institute", "医疗检查室"),
    ("photo-1682365114691-f0264ad25c52", "fr0ggy5", "诊室环境"),
    ("photo-1512677859289-868722942457", "Martha Dominguez de Gouveia", "医疗观察床"),
    ("photo-1648224395362-45708f929dc2", "Annie Spratt", "医疗设备环境"),
)


def _download(url: str) -> bytes:
    for attempt in range(4):
        response = requests.get(url, timeout=45, headers={"User-Agent": USER_AGENT})
        if response.status_code != 429:
            response.raise_for_status()
            if len(response.content) < 8_000:
                raise RuntimeError(f"下载内容异常：{url}")
            return response.content
        time.sleep(2 * (attempt + 1))
    response.raise_for_status()


def _font(size: int):
    candidates = (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    raise RuntimeError("未找到可渲染中文水印的字体")


def _render(raw: bytes, size: tuple[int, int], *, watermark: bool) -> bytes:
    with Image.open(BytesIO(raw)) as source:
        source.load()
        clean = Image.new("RGB", source.size, (255, 255, 255))
        if "A" in source.getbands():
            clean.paste(source.convert("RGBA"), mask=source.getchannel("A"))
        else:
            clean.paste(source.convert("RGB"))
    image = ImageOps.fit(clean, size, method=Image.Resampling.LANCZOS)
    if watermark:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        label = "开放授权演示附件 · 非诊断依据"
        font = _font(25)
        bounds = draw.textbbox((0, 0), label, font=font)
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        pad_x, pad_y = 18, 12
        right, bottom = size[0] - 22, size[1] - 20
        left, top = right - width - pad_x * 2, bottom - height - pad_y * 2
        draw.rounded_rectangle((left, top, right, bottom), radius=10, fill=(5, 22, 28, 190))
        draw.text((left + pad_x, top + pad_y - bounds[1]), label, font=font, fill=(255, 255, 255, 245))
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    output = BytesIO()
    image.save(output, "PNG", optimize=True, compress_level=9)
    return output.getvalue()


def _write_item(*, storage_key: str, raw: bytes, kind: str, title: str,
                category: str, source_url: str, download_url: str, author: str,
                license_name: str, watermark: bool) -> dict:
    size = (960, 540) if kind == "report_attachment" else (1280, 720)
    output = _render(raw, size, watermark=watermark)
    path = UPLOAD_ROOT / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(output)
    with Image.open(BytesIO(output)) as verified:
        if verified.getexif():
            raise RuntimeError(f"输出文件仍包含 EXIF：{storage_key}")
        width, height = verified.size
    return {
        "storage_key": storage_key,
        "kind": kind,
        "category": category,
        "title": title,
        "source_url": source_url,
        "download_url": download_url,
        "author": author,
        "license": license_name,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "processing": (
            "等比裁切、重新编码为 PNG、清除元数据、添加非诊断水印"
            if watermark else "等比裁切、重新编码为 PNG、清除元数据"
        ),
        "mime_type": "image/png",
        "width": width,
        "height": height,
        "byte_size": len(output),
        "sha256": hashlib.sha256(output).hexdigest(),
    }


def main() -> None:
    rows = []
    commons_cache: dict[str, bytes] = {}
    for filename, source_key, category in REPORT_ASSIGNMENTS:
        source = COMMONS_SOURCES[source_key]
        download_url = (
            "https://commons.wikimedia.org/wiki/Special:Redirect/file/"
            + quote(source["filename"])
        )
        if source_key not in commons_cache:
            commons_cache[source_key] = _download(download_url)
        raw = commons_cache[source_key]
        rows.append(_write_item(
            storage_key=f"health-assets/demo-v8/{filename}",
            raw=raw,
            kind="report_attachment",
            title=f"{category}开放授权演示附件",
            category=category,
            source_url=source["source_url"],
            download_url=download_url,
            author=source["author"],
            license_name=source["license"],
            watermark=True,
        ))

    for index, (photo_id, author, category) in enumerate(UNSPLASH_PHOTOS, start=1):
        download_url = (
            f"https://images.unsplash.com/{photo_id}"
            "?auto=format&fit=crop&fm=jpg&q=88&w=1800"
        )
        rows.append(_write_item(
            storage_key=f"institutions/demo-v8/branch-{index}-cover.png",
            raw=_download(download_url),
            kind="institution_cover",
            title=f"{category}开放授权展示图",
            category=category,
            source_url="https://unsplash.com/s/photos/medical-interior",
            download_url=download_url,
            author=author,
            license_name="Unsplash License",
            watermark=False,
        ))

    if len(rows) != 30 or len({row["storage_key"] for row in rows}) != 30:
        raise RuntimeError("演示素材清单必须恰好包含 30 个不同文件")
    manifest = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "usage_notice": "仅用于 HealthDoc 功能演示；医学附件不构成诊断依据。",
        "license_references": {
            "Wikimedia Commons": "https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia",
            "Unsplash License": "https://unsplash.com/license",
        },
        "items": rows,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"已生成 {len(rows)} 个素材并写入 {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
