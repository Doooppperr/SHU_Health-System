from __future__ import annotations

from io import BytesIO
from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import Institution, InstitutionImage, Package
from app.services.storage import get_storage_backend


INSTITUTION_FIELDS = {
    "branch_name",
    "address",
    "district",
    "metro_info",
    "consult_phone",
    "ext",
    "closed_day",
    "description",
}
REQUIRED_INSTITUTION_FIELDS = {"branch_name", "address", "district"}
PACKAGE_FIELDS = {"name", "focus_area", "gender_scope", "price", "description",
                  "package_type", "audience", "booking_notice"}
VALID_GENDER_SCOPES = {"all", "male", "female"}
IMAGE_LIMIT = 8
IMAGE_MAX_BYTES = 5 * 1024 * 1024
IMAGE_MAX_SIDE = 6000
IMAGE_MAX_PIXELS = 25_000_000
IMAGE_FORMATS = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "WEBP": (".webp", "image/webp"),
}


class ManagementValidationError(ValueError):
    pass


def parse_bool(raw_value):
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def apply_institution_payload(
    institution: Institution,
    payload: dict,
    *,
    creating: bool = False,
) -> Institution:
    if not isinstance(payload, dict):
        raise ManagementValidationError("request body must be an object")
    if creating:
        missing = [
            field for field in REQUIRED_INSTITUTION_FIELDS if not str(payload.get(field) or "").strip()
        ]
        if missing:
            raise ManagementValidationError(
                f"required institution fields: {', '.join(sorted(missing))}"
            )

    for field in INSTITUTION_FIELDS:
        if field not in payload:
            continue
        raw_value = payload.get(field)
        value = str(raw_value).strip() if raw_value is not None else ""
        if field in REQUIRED_INSTITUTION_FIELDS and not value:
            raise ManagementValidationError(f"{field} cannot be blank")
        setattr(institution, field, value or None)
    return institution


def apply_package_payload(package: Package, payload: dict, *, creating: bool = False) -> Package:
    if not isinstance(payload, dict):
        raise ManagementValidationError("request body must be an object")
    if creating:
        for field in ("name", "focus_area"):
            if not str(payload.get(field) or "").strip():
                raise ManagementValidationError(f"{field} is required")

    for field in PACKAGE_FIELDS:
        if field not in payload:
            continue
        if field in {"name", "focus_area"}:
            value = str(payload.get(field) or "").strip()
            if not value:
                raise ManagementValidationError(f"{field} cannot be blank")
            setattr(package, field, value)
        elif field == "gender_scope":
            value = str(payload.get(field) or "").strip()
            if value not in VALID_GENDER_SCOPES:
                raise ManagementValidationError("invalid gender_scope")
            package.gender_scope = value
        elif field == "price":
            try:
                value = float(payload.get(field))
            except (TypeError, ValueError):
                raise ManagementValidationError("price must be numeric") from None
            if value < 0:
                raise ManagementValidationError("price must be non-negative")
            package.price = value
        elif field == "package_type":
            value = str(payload.get(field) or "").strip()
            if value not in {"special", "combined"}:
                raise ManagementValidationError("package_type must be special or combined")
            package.package_type = value
        elif field in {"audience", "booking_notice"}:
            setattr(package, field, str(payload.get(field) or "").strip() or None)
        else:
            package.description = str(payload.get(field) or "").strip() or None

    if "is_active" in payload:
        value = parse_bool(payload.get("is_active"))
        if value is None:
            raise ManagementValidationError("is_active must be boolean")
        package.is_active = value
    return package


def image_payload(image: InstitutionImage) -> dict:
    return {
        "id": image.id,
        "institution_id": image.institution_id,
        "image_url": image.image_url,
        "sort_order": image.sort_order,
        "is_cover": image.sort_order == 0,
        "created_at": image.created_at.isoformat() if image.created_at else None,
    }


def institution_payload(
    institution: Institution, *, include_administrator: bool = False
) -> dict:
    payload = institution.to_dict()
    payload["images"] = [image_payload(item) for item in institution.images]
    if include_administrator:
        payload["administrator"] = (
            {
                "id": institution.administrator.id,
                "username": institution.administrator.username,
            }
            if institution.administrator is not None
            else None
        )
    return payload


def _sanitized_image_upload(upload) -> tuple[FileStorage, str]:
    max_bytes = int(current_app.config.get("INSTITUTION_IMAGE_MAX_BYTES", IMAGE_MAX_BYTES))
    raw = upload.read(max_bytes + 1)
    if not raw:
        raise ManagementValidationError("image file is required")
    if len(raw) > max_bytes:
        raise ManagementValidationError("image must not exceed 5 MB")

    try:
        with Image.open(BytesIO(raw)) as probe:
            image_format = (probe.format or "").upper()
            width, height = probe.size
            if (
                width <= 0
                or height <= 0
                or max(width, height) > IMAGE_MAX_SIDE
                or width * height > IMAGE_MAX_PIXELS
            ):
                raise ManagementValidationError("image dimensions are too large")
            probe.verify()
        if image_format not in IMAGE_FORMATS:
            raise ManagementValidationError("only JPEG, PNG and WebP images are supported")
        with Image.open(BytesIO(raw)) as source:
            source.load()
            cleaned = ImageOps.exif_transpose(source).copy()
    except ManagementValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError):
        raise ManagementValidationError("invalid image file") from None

    width, height = cleaned.size
    if width <= 0 or height <= 0 or max(width, height) > IMAGE_MAX_SIDE or width * height > IMAGE_MAX_PIXELS:
        raise ManagementValidationError("image dimensions are too large")

    extension, content_type = IMAGE_FORMATS[image_format]
    if image_format == "JPEG" and cleaned.mode not in {"RGB", "L"}:
        cleaned = cleaned.convert("RGB")

    rendered = BytesIO()
    save_options = {}
    if image_format == "JPEG":
        save_options = {"quality": 90, "optimize": True}
    elif image_format == "PNG":
        save_options = {"optimize": True}
    elif image_format == "WEBP":
        save_options = {"quality": 90, "method": 4}
    cleaned.save(rendered, format=image_format, **save_options)
    rendered.seek(0)
    filename = f"institution-image{extension}"
    return FileStorage(stream=rendered, filename=filename, content_type=content_type), extension


def save_institution_image(institution: Institution, upload) -> InstitutionImage:
    current_count = InstitutionImage.query.filter_by(institution_id=institution.id).count()
    if current_count >= IMAGE_LIMIT:
        raise ManagementValidationError("an institution can have at most 8 images")
    sanitized_upload, _extension = _sanitized_image_upload(upload)
    storage = get_storage_backend(current_app.config)
    saved = storage.save(sanitized_upload, subdir=f"institutions/{institution.id}")
    image = InstitutionImage(
        institution_id=institution.id,
        storage_key=saved["key"],
        image_url=saved["url"],
        sort_order=current_count,
    )
    db.session.add(image)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        storage.delete(saved["key"])
        raise ManagementValidationError("image order conflict, please retry") from None
    except Exception:
        db.session.rollback()
        storage.delete(saved["key"])
        raise
    return image


def _rebuild_images(institution_id: int, ordered_images: list[InstitutionImage]) -> list[InstitutionImage]:
    snapshots = [
        {
            "id": item.id,
            "institution_id": institution_id,
            "storage_key": item.storage_key,
            "image_url": item.image_url,
            "created_at": item.created_at,
        }
        for item in ordered_images
    ]
    tracked = InstitutionImage.query.filter_by(institution_id=institution_id).all()
    for item in tracked:
        if item in db.session:
            db.session.expunge(item)
    InstitutionImage.query.filter_by(institution_id=institution_id).delete(synchronize_session=False)
    db.session.flush()
    rebuilt = []
    for order, snapshot in enumerate(snapshots):
        rebuilt_item = InstitutionImage(**snapshot, sort_order=order)
        db.session.add(rebuilt_item)
        rebuilt.append(rebuilt_item)
    db.session.flush()
    return rebuilt


def reorder_institution_images(institution_id: int, image_ids) -> list[InstitutionImage]:
    if not isinstance(image_ids, list) or any(isinstance(item, bool) for item in image_ids):
        raise ManagementValidationError("image_ids must be a list of integers")
    try:
        parsed_ids = [int(item) for item in image_ids]
    except (TypeError, ValueError):
        raise ManagementValidationError("image_ids must be a list of integers") from None
    if len(parsed_ids) != len(set(parsed_ids)):
        raise ManagementValidationError("image_ids cannot contain duplicates")

    existing = (
        InstitutionImage.query.filter_by(institution_id=institution_id)
        .order_by(InstitutionImage.sort_order.asc())
        .all()
    )
    if set(parsed_ids) != {item.id for item in existing}:
        raise ManagementValidationError("image_ids must contain every institution image exactly once")
    by_id = {item.id: item for item in existing}
    rebuilt = _rebuild_images(institution_id, [by_id[item_id] for item_id in parsed_ids])
    db.session.commit()
    return rebuilt


def delete_institution_image(institution_id: int, image_id: int) -> bool:
    images = (
        InstitutionImage.query.filter_by(institution_id=institution_id)
        .order_by(InstitutionImage.sort_order.asc())
        .all()
    )
    target = next((item for item in images if item.id == image_id), None)
    if target is None:
        return False
    remaining = [item for item in images if item.id != image_id]
    storage_key = target.storage_key
    _rebuild_images(institution_id, remaining)
    db.session.commit()
    try:
        get_storage_backend(current_app.config).delete(storage_key)
    except OSError:
        current_app.logger.exception(
            "failed to clean institution image after database deletion: %s",
            storage_key,
        )
    return True
