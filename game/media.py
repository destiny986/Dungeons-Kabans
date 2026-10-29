"""Validated immutable PNG storage. No filesystem writes occur during preparation."""
import hashlib
import io
import os
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import MediaAsset, ImportJob
from .validators import validate_relative_path


@dataclass(frozen=True)
class PreparedPNG:
    data: bytes
    digest: str
    width: int
    height: int


def prepare_png(upload, *, size):
    try:
        upload.seek(0)
        data = upload.read(5 * 1024 * 1024 + 1)
        if len(data) > 5 * 1024 * 1024:
            raise ValueError('size')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != 'PNG' or getattr(image, 'n_frames', 1) != 1 or max(image.size) > 4096:
                    raise ValueError('format or dimensions')
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                normalized = ImageOps.fit(image.convert('RGBA'), (size, size), method=Image.Resampling.LANCZOS)
                output = io.BytesIO()
                normalized.save(output, format='PNG')
        result = output.getvalue()
        return PreparedPNG(result, hashlib.sha256(result).hexdigest(), size, size)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError('Загрузите целое изображение PNG до 5 МиБ, размером не более 4096 × 4096.') from exc


def asset_path(asset):
    validate_relative_path(asset.path)
    root = Path(settings.MEDIA_ROOT).resolve()
    target = (root / asset.path).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValidationError('Недопустимый путь изображения.')
    return target


def store_prepared(prepared):
    asset, _ = MediaAsset.objects.get_or_create(digest=prepared.digest, defaults={
        'path': f'{prepared.digest}.png', 'width': prepared.width, 'height': prepared.height})
    path = asset_path(asset)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if not path.exists():
        descriptor, name = tempfile.mkstemp(prefix='.png-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(prepared.data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o644)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
    return asset


def store_png(upload, *, size):
    return store_prepared(prepare_png(upload, size=size))


def _contains_reference(value, tokens):
    if isinstance(value, dict):
        return any(str(k) in tokens or _contains_reference(v, tokens) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_reference(v, tokens) for v in value)
    return str(value) in tokens


def release_assets(asset_ids):
    """Recheck references under a write transaction after the caller commits."""
    ids = tuple(set(asset_ids) - {None})
    def release():
        for pk in ids:
            with transaction.atomic():
                asset = MediaAsset.objects.select_for_update().filter(pk=pk).first()
                if asset is None or asset.items.exists() or asset.actions.exists() or asset.portraits.exists():
                    continue
                tokens = {str(asset.pk), asset.digest, asset.path}
                jobs = ImportJob.objects.exclude(status__in=['complete', 'cancelled', 'failed'])
                if any(_contains_reference(job.payload, tokens) or _contains_reference(job.preview, tokens) for job in jobs):
                    continue
                path = asset_path(asset)
                asset.delete()
                path.unlink(missing_ok=True)
    transaction.on_commit(release)


def cleanup_unreferenced_file(prepared):
    """Compensate a rolled-back database write without touching shared assets."""
    with transaction.atomic():
        if not MediaAsset.objects.filter(digest=prepared.digest).exists():
            asset_path(MediaAsset(path=f'{prepared.digest}.png')).unlink(missing_ok=True)
