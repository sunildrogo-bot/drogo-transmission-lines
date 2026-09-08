"""
seed_db.py — Creates lookup data and optional demo projects.

Run once after applying migrations:
    flask --app app db upgrade
    python seed_db.py

Safe to re-run: existing projects and lookup records are preserved.
"""
from app import create_app
from models import db, Role, Module
from users import _get_or_create_role, _get_or_create_module, ROLES, MODULES, MODULE_ROUTES
import os

app = create_app()

def seed():
    os.makedirs('instance', exist_ok=True)

    with app.app_context():
        # ── Seed lookup tables ────────────────────────────────────────────────
        for r in ROLES:
            _get_or_create_role(r)
        for m in MODULES:
            mod = Module.query.filter_by(name=m).first()
            if not mod:
                db.session.add(Module(name=m, route=MODULE_ROUTES.get(m, '')))
            elif mod.route != MODULE_ROUTES.get(m, ''):
                # Keeps existing rows in sync if MODULE_ROUTES changes later
                # (e.g. Chimney Inspection used to point at the 'mpptcl' placeholder).
                mod.route = MODULE_ROUTES.get(m, '')
        db.session.commit()
        print("✓ Roles and modules seeded")

        # ── Seed demo Transmission Line projects ────────────────────────────────
        from models import Project, Division, Line

        if not Project.query.filter_by(name='Damodar Valley Corporation').first():
            db.session.add(Project(
                module='Transmission Line',
                name='Damodar Valley Corporation',
                contact_no='+91 90000 00001',
                email='owner@dvc.example.com',
                country='India', state='West Bengal / Jharkhand',
                logo_path='images/logos/dvc-logo.png',
            ))

        mpptcl = Project.query.filter_by(name='Madhya Pradesh Power Transmission').first()
        if not mpptcl:
            mpptcl = Project(
                module='Transmission Line',
                name='Madhya Pradesh Power Transmission',
                contact_no='+91 90000 00002',
                email='owner@mpptcl.example.com',
                country='India', state='Madhya Pradesh',
                logo_path='images/logos/mpptcl-logo.png',
            )
            db.session.add(mpptcl)
            db.session.flush()
            division = Division(project_id=mpptcl.id, name='Bhopal Division', latitude=23.2599, longitude=77.4126)
            db.session.add(division)
            db.session.flush()
            db.session.add(Line(
                division_id=division.id, name='Bhopal–Indore 400kV',
                start_lat=23.2599, start_lng=77.4126, end_lat=22.7196, end_lng=75.8577,
                length_km=190.5, tower_count=420,
            ))

        db.session.commit()
        print("✓ Demo projects seeded (DVC, MPPTCL)")


if __name__ == '__main__':
    seed()
