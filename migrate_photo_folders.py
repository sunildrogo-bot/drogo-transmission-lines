"""
migrate_photo_folders.py — One-time cleanup for photos uploaded before the
nested folder structure existed.

New uploads already save to:
    uploads/tower_photos/<project>/<division>/<line>/<tower>/raw/<file>
and get a defects/ duplicate automatically the moment a defect is marked
on them. This script catches up everything uploaded BEFORE that existed —
photos still sitting in the old flat uploads/tower_photos/<file> layout.

For each one it:
  1. Resolves the photo's real project/division/line/tower via its Line
  2. Moves the actual file on disk into the new nested raw/ location
  3. Updates TowerPhoto.image_path in the database to match
  4. If the photo already has a defect marked on it, makes its defects/
     duplicate right now too (new uploads only get this at the moment a
     defect is created — these photos predate that, so nothing ever
     triggered it for them)

Safe to run more than once — anything already in the new structure
(image_path containing '/raw/' or '/defects/') is skipped. Never deletes
anything; only moves, copies, and updates database paths.

Run manually:  python3 migrate_photo_folders.py
"""
import os
import shutil

from app import create_app
from models import db, TowerPhoto, Line
from projects_routes import _slug, _duplicate_photo_for_defects, UPLOAD_BASE


def _is_already_migrated(image_path: str) -> bool:
    return '/raw/' in image_path or '/defects/' in image_path


def run(verbose=True):
    app = create_app()
    with app.app_context():
        base_dir = app.root_path
        photos = TowerPhoto.query.all()

        moved = 0
        duplicated = 0
        skipped_already = 0
        skipped_missing = 0
        skipped_no_line = 0

        for photo in photos:
            if not photo.image_path:
                continue
            if _is_already_migrated(photo.image_path):
                skipped_already += 1
                continue

            line = Line.query.get(photo.line_id)
            division = line.division if line else None
            project = division.project if division else None
            if not line:
                if verbose:
                    print(f"[migrate_photo_folders] SKIP (no line found, id={photo.id}): {photo.image_path}")
                skipped_no_line += 1
                continue

            src = os.path.join(base_dir, 'static', photo.image_path)
            if not os.path.isfile(src):
                if verbose:
                    print(f"[migrate_photo_folders] SKIP (file missing on disk): {photo.image_path}")
                skipped_missing += 1
                continue

            subfolder = os.path.join(
                'tower_photos', _slug(project.name if project else None),
                _slug(division.name if division else None), _slug(line.name if line else None),
                _slug(photo.tower_label), 'raw',
            )
            dest_dir = os.path.join(base_dir, UPLOAD_BASE, subfolder)
            os.makedirs(dest_dir, exist_ok=True)

            filename = os.path.basename(photo.image_path)
            name_root, name_ext = os.path.splitext(filename)
            dest_name = filename
            i = 1
            while os.path.isfile(os.path.join(dest_dir, dest_name)):
                dest_name = f"{name_root}_{i}{name_ext}"
                i += 1
            dest = os.path.join(dest_dir, dest_name)

            shutil.move(src, dest)
            new_rel_path = f"uploads/{subfolder}/{dest_name}"
            if verbose:
                print(f"[migrate_photo_folders] moved: {photo.image_path}  ->  {new_rel_path}")
            photo.image_path = new_rel_path
            moved += 1

            # This photo predates the auto-duplicate-on-defect logic — if
            # it already has a defect marked on it, make its permanent
            # defects/ copy right now, same as a brand-new defect would.
            if photo.defects and not photo.defect_copy_path:
                if _duplicate_photo_for_defects(photo):
                    duplicated += 1
                    if verbose:
                        print(f"[migrate_photo_folders]   + defects/ copy made ({len(photo.defects)} defect(s) on this photo)")

            db.session.commit()

        print(
            f"\n[migrate_photo_folders] Done. "
            f"moved={moved}, defects-copies-made={duplicated}, "
            f"already-migrated={skipped_already}, missing-on-disk={skipped_missing}, "
            f"no-line={skipped_no_line}"
        )


if __name__ == '__main__':
    run()
