"""
migrate_generate_thumbnails.py — One-time backfill for photos uploaded
before thumbnails existed.

Covers both TowerPhoto (the map/KML tower photo grid) and CorridorPhoto
(the corridor photo gallery) — same underlying problem for both: the
grid is supposed to load a small ~480px thumbnail per tile, not the
full drone photo, but any photo uploaded before thumbnails existed has
no thumbnail_path, so the grid silently falls back to the full raw
image for each one — often 2-5MB per photo.

This script finds every photo (of either kind) with a raw image on disk
but no thumbnail yet, and generates one — same ~480px/quality-72 JPEG
the upload path itself produces, so the result is identical either way.

Safe to run more than once — anything that already has a thumbnail is
skipped. Never touches the raw file; only adds a new thumb file
alongside it and updates the database row.

Run manually:  python3 migrate_generate_thumbnails.py
"""
import os

from app import create_app
from models import db, TowerPhoto, CorridorPhoto
from projects_routes import _generate_thumbnail, _generate_flat_thumbnail


def _run_tower_photos(app, base_dir, verbose=True):
    photos = (TowerPhoto.query
              .filter(TowerPhoto.image_path.isnot(None))
              .filter((TowerPhoto.thumbnail_path.is_(None)) | (TowerPhoto.thumbnail_path == ''))
              .all())

    generated = 0
    skipped_missing = 0
    failed = 0

    for photo in photos:
        if not photo.image_path or '/raw/' not in photo.image_path:
            # Legacy flat-path photos that were never moved into the
            # new raw/ folder structure — nothing this script can
            # safely assume about their layout, so leave them alone.
            skipped_missing += 1
            continue

        full_path = os.path.join(base_dir, 'static', photo.image_path)
        if not os.path.isfile(full_path):
            if verbose:
                print(f"[migrate_generate_thumbnails] SKIP tower photo (file missing): {photo.image_path}")
            skipped_missing += 1
            continue

        subfolder_raw, filename = photo.image_path[len('uploads/'):].rsplit('/', 1)
        thumb_path = _generate_thumbnail(base_dir, subfolder_raw, filename)
        if not thumb_path:
            if verbose:
                print(f"[migrate_generate_thumbnails] FAILED tower photo (could not process image): {photo.image_path}")
            failed += 1
            continue

        photo.thumbnail_path = thumb_path
        generated += 1
        if verbose:
            print(f"[migrate_generate_thumbnails] tower thumbnail: {photo.image_path}  ->  {thumb_path}")
        db.session.commit()

    print(
        f"[migrate_generate_thumbnails] Tower photos done. "
        f"generated={generated}, skipped(missing/legacy-path)={skipped_missing}, failed={failed}"
    )


def _run_corridor_photos(app, base_dir, verbose=True):
    photos = (CorridorPhoto.query
              .filter(CorridorPhoto.image_path.isnot(None))
              .filter((CorridorPhoto.thumbnail_path.is_(None)) | (CorridorPhoto.thumbnail_path == ''))
              .all())

    generated = 0
    skipped_missing = 0
    failed = 0

    for photo in photos:
        full_path = os.path.join(base_dir, 'static', photo.image_path)
        if not os.path.isfile(full_path):
            if verbose:
                print(f"[migrate_generate_thumbnails] SKIP corridor photo (file missing): {photo.image_path}")
            skipped_missing += 1
            continue

        subfolder, filename = photo.image_path[len('uploads/'):].rsplit('/', 1)
        thumb_path = _generate_flat_thumbnail(base_dir, subfolder, filename)
        if not thumb_path:
            if verbose:
                print(f"[migrate_generate_thumbnails] FAILED corridor photo (could not process image): {photo.image_path}")
            failed += 1
            continue

        photo.thumbnail_path = thumb_path
        generated += 1
        if verbose:
            print(f"[migrate_generate_thumbnails] corridor thumbnail: {photo.image_path}  ->  {thumb_path}")
        db.session.commit()

    print(
        f"[migrate_generate_thumbnails] Corridor photos done. "
        f"generated={generated}, skipped(missing)={skipped_missing}, failed={failed}"
    )


def run(verbose=True):
    app = create_app()
    with app.app_context():
        base_dir = app.root_path
        _run_tower_photos(app, base_dir, verbose)
        _run_corridor_photos(app, base_dir, verbose)


if __name__ == '__main__':
    run()

