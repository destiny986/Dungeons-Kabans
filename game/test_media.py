import io
import tempfile
from pathlib import Path

from PIL import Image
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TransactionTestCase, override_settings

from . import media
from .models import Item, MediaAsset, ImportJob
from . import signals  # Registration is also performed by GameConfig.ready in production.


def png(color='red', size=(40, 20), format='PNG'):
    output = io.BytesIO()
    Image.new('RGB', size, color).save(output, format)
    return SimpleUploadedFile('image.png', output.getvalue(), content_type='image/png')


class MediaTests(TransactionTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        override = override_settings(MEDIA_ROOT=self.directory.name)
        override.enable()
        self.addCleanup(override.disable)

    def test_preparation_is_nonpersistent_and_normalizes(self):
        prepared = media.prepare_png(png(), size=256)
        self.assertEqual((prepared.width, prepared.height), (256, 256))
        self.assertEqual(Image.open(io.BytesIO(prepared.data)).mode, 'RGBA')
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
        first = media.store_prepared(prepared)
        self.assertEqual(first.pk, media.store_prepared(prepared).pk)
        self.assertTrue(media.asset_path(first).is_file())

    def test_rejects_forged_truncated_oversized_uploads(self):
        for upload in [png(format='JPEG'), SimpleUploadedFile('bad.png', png().read()[:35]),
                       SimpleUploadedFile('huge.png', b'x' * (5 * 1024 * 1024 + 1)), png(size=(4097, 1))]:
            with self.subTest(upload=upload.name), self.assertRaises(ValidationError):
                media.prepare_png(upload, size=256)
        self.assertFalse(MediaAsset.objects.exists())

    def test_shared_release_and_rollback(self):
        asset = media.store_png(png(), size=256)
        item = Item.objects.create(name='Меч', description='Меч', icon=asset)
        media.release_assets([asset.pk])
        self.assertTrue(media.asset_path(asset).exists())
        try:
            with transaction.atomic():
                item.delete()
                media.release_assets([asset.pk])
                raise RuntimeError('rollback')
        except RuntimeError:
            pass
        self.assertTrue(media.asset_path(asset).exists())
        item = Item.objects.get(name='Меч')
        item.delete()
        media.release_assets([asset.pk])
        self.assertFalse(MediaAsset.objects.filter(pk=asset.pk).exists())
        self.assertFalse((Path(self.directory.name) / asset.path).exists())

    def test_escaped_path_rejected(self):
        with self.assertRaises(ValidationError):
            media.asset_path(MediaAsset(path='../outside.png'))

    def test_active_import_protects_asset_until_cancelled(self):
        asset = media.store_png(png(), size=256)
        job = ImportJob.objects.create(session_key='session', directory='job', payload={'assets': {asset.digest: {'filename':'image.png'}}})
        media.release_assets([asset.pk])
        self.assertTrue(media.asset_path(asset).exists())
        job.status = 'cancelled'
        job.save()
        media.release_assets([asset.pk])
        self.assertFalse((Path(self.directory.name) / asset.path).exists())

    def test_queryset_delete_releases_only_unshared_media(self):
        asset = media.store_png(png(), size=256)
        first = Item.objects.create(name='Первый',description='x',icon=asset)
        second = Item.objects.create(name='Второй',description='x',icon=asset)
        Item.objects.filter(pk=first.pk).delete()
        self.assertTrue(media.asset_path(asset).exists())
        Item.objects.filter(pk=second.pk).delete()
        self.assertFalse((Path(self.directory.name) / asset.path).exists())
