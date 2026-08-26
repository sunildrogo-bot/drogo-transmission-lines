"""
generate_missing_thumbnails.py — Backfills thumbnails for tower photos
uploaded before thumbnail generation existed (or where it failed at
upload time for some reason).

New uploads already get a ~480px thumbnail generated automatically
alongside the raw photo — this is what the grid actually loads now,
instead of the multi-MB drone original, which is what made "opening
images" feel slow. This script catches up everything that doesn't have
one yet.

Safe to run more than once — a photo that already has thumbnail_path
set is skipped.

Run manually:  python3 generate_missing_thumbnails.py
"""
import os

from app import create_app
from models import db, CorridorPhoto, TowerPhoto
from projects_routes import _generate_flat_thumbnail, _generate_thumbnail, UPLOAD_BASE


def run(verbose=True):
    app = create_app()
    with app.app_context():
        base_dir = app.root_path
        photos = TowerPhoto.query.filter(
            db.or_(TowerPhoto.thumbnail_path.is_(None), TowerPhoto.thumbnail_path == ''),
            TowerPhoto.image_path.isnot(None),
        ).all()

        generated = 0
        skipped_missing = 0
        failed = 0

        for photo in photos:
            if not photo.image_path:
                continue
            full_path = os.path.join(base_dir, 'static', photo.image_path)
            if not os.path.isfile(full_path):
                if verbose:
                    print(f"[generate_missing_thumbnails] SKIP (file missing on disk): {photo.image_path}")
                skipped_missing += 1
                continue

            # image_path is relative to /static, e.g.
            # "uploads/tower_photos/<proj>/<div>/<line>/<tower>/raw/<file>"
            # — split off the "uploads/" prefix and the filename to get
            # the subfolder _generate_thumbnail expects.
            rel = photo.image_path
            if rel.startswith('uploads/'):
                rel = rel[len('uploads/'):]
            subfolder_raw, filename = os.path.split(rel)

            thumb_path = _generate_thumbnail(base_dir, subfolder_raw, filename)
            if thumb_path:
                photo.thumbnail_path = thumb_path
                generated += 1
                if verbose:
                    print(f"[generate_missing_thumbnails] generated: {thumb_path}")
                db.session.commit()
            else:
                failed += 1
                if verbose:
                    print(f"[generate_missing_thumbnails] FAILED (unreadable image?): {photo.image_path}")

        corridor_generated = 0
        corridor_failed = 0
        corridor_missing = 0
        corridor_photos = CorridorPhoto.query.filter(
            db.or_(CorridorPhoto.thumbnail_path.is_(None), CorridorPhoto.thumbnail_path == ''),
            CorridorPhoto.image_path.isnot(None),
        ).all()
        for photo in corridor_photos:
            full_path = os.path.join(base_dir, 'static', photo.image_path or '')
            if not os.path.isfile(full_path):
                corridor_missing += 1
                continue
            rel = photo.image_path[len('uploads/'):] if photo.image_path.startswith('uploads/') else photo.image_path
            flat_subfolder, filename = os.path.split(rel)
            thumb_path = _generate_flat_thumbnail(base_dir, flat_subfolder, filename)
            if thumb_path:
                photo.thumbnail_path = thumb_path
                corridor_generated += 1
                db.session.commit()
            else:
                corridor_failed += 1

        print(
            f"\n[generate_missing_thumbnails] Done. "
            f"tower-generated={generated}, tower-failed={failed}, tower-missing={skipped_missing}; "
            f"corridor-generated={corridor_generated}, corridor-failed={corridor_failed}, "
            f"corridor-missing={corridor_missing}"
        )


if __name__ == '__main__':
    run()
