"""
models.py — SQLAlchemy ORM models for NOVA+
Tables:
    users               — one row per account
    user_roles          — many-to-many: user ↔ role  (Admin / Client User)
    user_modules        — many-to-many: user ↔ module (Transmission Line / …)
"""
import json
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# ── Association tables (pure join tables, no extra columns) ───────────────────

user_roles = db.Table(
    'user_roles',
    db.Column('user_id',  db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    db.Column('role_id',  db.Integer, db.ForeignKey('roles.id',  ondelete='CASCADE'), primary_key=True),
)

user_modules = db.Table(
    'user_modules',
    db.Column('user_id',   db.Integer, db.ForeignKey('users.id',   ondelete='CASCADE'), primary_key=True),
    db.Column('module_id', db.Integer, db.ForeignKey('modules.id', ondelete='CASCADE'), primary_key=True),
)

# Project-wise access — an ADDITIONAL, optional restriction layered on top
# of module-level access. A user still needs their module assigned (via
# user_modules above) to see a module at all; this table narrows that
# down further to specific projects within it (the generic Project model
# used by Transmission Line/TRANS). See
# User.restricted_project_ids_for_module()/is_admin-bypass logic
# for how "no rows here" intentionally means "full access to every
# project in that module" — the backward-compatible default so this
# doesn't retroactively lock out any existing user the moment it ships.
user_projects = db.Table(
    'user_projects',
    db.Column('user_id',    db.Integer, db.ForeignKey('users.id',    ondelete='CASCADE'), primary_key=True),
    db.Column('project_id', db.Integer, db.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
)


# ── Lookup tables ─────────────────────────────────────────────────────────────

class Role(db.Model):
    __tablename__ = 'roles'
    id   = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

    def __repr__(self):
        return f'<Role {self.name}>'


class Module(db.Model):
    __tablename__ = 'modules'
    id    = db.Column(db.Integer, primary_key=True)
    name  = db.Column(db.String(100), unique=True, nullable=False)
    route = db.Column(db.String(100))   # Flask route name, e.g. 'projects'

    def __repr__(self):
        return f'<Module {self.name}>'


# ── Main user table ───────────────────────────────────────────────────────────

class User(db.Model):
    __tablename__ = 'users'

    id           = db.Column(db.Integer, primary_key=True)
    username     = db.Column(db.String(120), nullable=False)
    email        = db.Column(db.String(200), unique=True, nullable=False)
    password_hash= db.Column(db.String(256), nullable=False)
    contact      = db.Column(db.String(30),  default='')
    photo_path   = db.Column(db.String(255), default='')          # relative to /static
    status       = db.Column(db.String(20),  default='Pending')   # Active / Inactive / Pending
    last_login   = db.Column(db.String(40),  default='Never')     # display string, e.g. "14 Jul 2026, 08:30 AM"
    last_login_at = db.Column(db.DateTime, nullable=True)         # real timestamp, used to compute effective_status()
    created_at   = db.Column(db.DateTime,    default=datetime.utcnow)
    dashboard_notes = db.Column(db.Text, default='')  # personal scratch notes shown on the admin dashboard

    # How many days since last login before a user is considered inactive
    # again — drives both the Dashboard "Active Users" count and the
    # status shown in User Management, so the two always agree.
    ACTIVE_WINDOW_DAYS = 3

    def effective_status(self) -> str:
        """The status actually shown to admins: 'Pending' accounts stay
        Pending until approved; everyone else is 'Active' only if they've
        logged in within the last ACTIVE_WINDOW_DAYS, otherwise 'Inactive'
        — regardless of whatever the stored `status` column says, so this
        never drifts out of sync with real login activity."""
        if self.status == 'Pending':
            return 'Pending'
        if self.last_login_at and (datetime.utcnow() - self.last_login_at) <= timedelta(days=self.ACTIVE_WINDOW_DAYS):
            return 'Active'
        return 'Inactive'

    # Relationships
    roles   = db.relationship('Role',   secondary=user_roles,   backref='users', lazy='joined')
    modules = db.relationship('Module', secondary=user_modules, backref='users', lazy='joined')
    allowed_projects = db.relationship('Project', secondary=user_projects, backref='allowed_users')

    # ── Convenience helpers ───────────────────────────────────────────────────

    def role_names(self) -> list[str]:
        return [r.name for r in self.roles]

    def module_names(self) -> list[str]:
        return [m.name for m in self.modules]

    def module_routes(self) -> dict:
        return {m.name: m.route for m in self.modules}

    def restricted_project_ids_for_module(self, module_name: str):
        """None means "no restriction — full access to every project in
        this module" (the backward-compatible default). A set (even an
        empty one, though that shouldn't normally happen from the UI)
        means "only these specific project IDs are visible"."""
        ids = {p.id for p in self.allowed_projects if p.module == module_name}
        return ids if ids else None

    def to_dict(self, include_password: bool = False) -> dict:
        d = {
            'id':         self.id,
            'username':   self.username,
            'email':      self.email,
            'contact':    self.contact or '',
            'photo_url':  f'/static/{self.photo_path}' if self.photo_path else '',
            'roles':      self.role_names(),
            'modules':    self.module_names(),
            'status':     self.effective_status(),
            'last_login': self.last_login,
            'created_at': self.created_at.strftime('%d %b %Y') if self.created_at else '',
            'allowed_project_ids':          [p.id for p in self.allowed_projects],
        }
        if include_password:
            d['password_hash'] = self.password_hash
        return d

    def __repr__(self):
        return f'<User {self.email}>'


# ── App-wide settings (key/value) ──────────────────────────────────────────────
# Currently used for the single shared "delete password" required to delete
# any project (Transmission Line / TRANS). Set/changed from
# the Settings page by an Admin.

class AppSetting(db.Model):
    __tablename__ = 'app_settings'
    key   = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(255), nullable=False)


# All timestamps are stored in UTC (datetime.utcnow()) — this deployment is
# India-based, so anywhere a raw timestamp is formatted for display, it's
# converted with this +5:30 offset first so it actually matches local time.
IST_OFFSET = timedelta(hours=5, minutes=30)


def _fmt_ist(dt, fmt='%d %b %Y, %I:%M %p', suffix=True):
    if not dt:
        return ''
    out = (dt + IST_OFFSET).strftime(fmt)
    return out + ' IST' if suffix else out


# ── Activity log (audit trail) ─────────────────────────────────────────────────
# One row per meaningful action — currently project/module deletions, shown on
# the Settings → Activity page along with active users and login details.

class ActivityLog(db.Model):
    __tablename__ = 'activity_log'

    id           = db.Column(db.Integer, primary_key=True)
    action       = db.Column(db.String(40),  nullable=False)   # e.g. 'delete'
    entity_type  = db.Column(db.String(40),  nullable=False)   # 'Project' / 'TowerPhoto' / etc
    entity_name  = db.Column(db.String(150), default='')
    module       = db.Column(db.String(60),  default='')       # Transmission Line / TRANS
    performed_by = db.Column(db.String(150), default='')       # username at time of action
    role         = db.Column(db.String(20),  default='')       # Admin / Client User — role active at time of action
    duration_seconds = db.Column(db.Integer, nullable=True)    # for 'logout' rows: how long that session lasted
    details      = db.Column(db.String(255), default='')
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':           self.id,
            'action':       self.action,
            'entity_type':  self.entity_type,
            'entity_name':  self.entity_name,
            'module':       self.module,
            'performed_by': self.performed_by,
            'role':         self.role or '',
            'duration':     _format_duration(self.duration_seconds) if self.duration_seconds is not None else '',
            'details':      self.details,
            'created_at':   _fmt_ist(self.created_at),
            'created_at_iso': self.created_at.isoformat() + 'Z' if self.created_at else '',
        }

    @staticmethod
    def log(action, entity_type, entity_name='', module='', performed_by='', details='', role='', duration_seconds=None):
        entry = ActivityLog(action=action, entity_type=entity_type, entity_name=entity_name,
                             module=module, performed_by=performed_by, details=details,
                             role=role, duration_seconds=duration_seconds)
        db.session.add(entry)
        return entry


def _format_duration(seconds):
    if seconds is None:
        return ''
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f'{h}h {m}m'
    if m:
        return f'{m}m'
    return '<1m'


# ── Dynamic Projects (replaces hardcoded MPPTCL / DVC / Kothegudam pages) ─────

class Project(db.Model):
    """
    A project belongs to a module (Transmission Line / TRANS). Created
    dynamically through the "+ Add Project" dialog on each module's
    listing page.
    """
    __tablename__ = 'projects'

    id          = db.Column(db.Integer, primary_key=True)
    module      = db.Column(db.String(60),  nullable=False)   # e.g. 'Transmission Line'
    name        = db.Column(db.String(150), nullable=False)
    contact_no  = db.Column(db.String(30),  default='')
    email       = db.Column(db.String(200), default='')       # project owner email
    country     = db.Column(db.String(100), default='')
    state       = db.Column(db.String(100), default='')
    logo_path   = db.Column(db.String(255), default='')       # relative to /static
    # Extra fields used by the TRANS module's richer "+ Add Project" form —
    # nullable/optional so they don't affect any other module's projects.
    client_name       = db.Column(db.String(150), default='')
    planned_divisions = db.Column(db.Integer, nullable=True)   # how many divisions the project is expected to have
    planned_towers    = db.Column(db.Integer, nullable=True)   # total towers expected across the project
    timeline          = db.Column(db.String(150), default='')  # free-form, e.g. "6 months" or a target date
    # Optional pointer to a legacy, hand-built template for the original demo
    # projects (MPPTCL / DVC / Kothegudam) so they keep working unchanged.
    legacy_route   = db.Column(db.String(80), default='')
    legacy_banner  = db.Column(db.String(255), default='')
    # Which kind(s) of inspection this project actually does — some
    # clients only want RGB, some only thermal, some both. JSON list of
    # 'rgb'/'thermal'. Drives which sections tower reports include.
    # Missing/empty is treated as "both" (matches every project created
    # before this existed).
    inspection_types = db.Column(db.Text, default='')
    created_by  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    divisions = db.relationship('Division', backref='project', cascade='all, delete-orphan',
                                 order_by='Division.created_at')

    def get_inspection_types(self):
        """Always returns a real list — ['rgb','thermal'] for legacy/
        unset projects, or whatever was actually selected."""
        import json as _json
        try:
            types = _json.loads(self.inspection_types) if self.inspection_types else None
        except (ValueError, TypeError):
            types = None
        if not types:
            return ['rgb', 'thermal']
        return types

    def to_dict(self):
        return {
            'id':            self.id,
            'module':        self.module,
            'name':          self.name,
            'contact_no':    self.contact_no or '',
            'email':         self.email or '',
            'country':       self.country or '',
            'state':         self.state or '',
            'logo_url':      f'/static/{self.logo_path}' if self.logo_path else '',
            'client_name':       self.client_name or '',
            'planned_divisions': self.planned_divisions,
            'planned_towers':    self.planned_towers,
            'timeline':          self.timeline or '',
            'legacy_route':  self.legacy_route or '',
            'legacy_banner': self.legacy_banner or '',
            'inspection_types': self.get_inspection_types(),
            'division_count': len(self.divisions),
            'line_count':    sum(len(d.lines) for d in self.divisions),
            'created_at':    self.created_at.strftime('%d %b %Y') if self.created_at else '',
        }


class Division(db.Model):
    """A Division (GOMD/area) belonging to a Transmission Line project.
    Rendered as a tab in the left sidebar of the project map."""
    __tablename__ = 'divisions'

    id         = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
    name       = db.Column(db.String(150), nullable=False)
    latitude   = db.Column(db.Float, nullable=False)
    longitude  = db.Column(db.Float, nullable=False)
    # Extra fields for the richer "+ Add Division" form — nullable/optional,
    # doesn't affect divisions created before these existed.
    client_name    = db.Column(db.String(150), default='')
    state          = db.Column(db.String(100), default='')
    planned_towers = db.Column(db.Integer, nullable=True)   # towers expected to be covered in this division
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    lines = db.relationship('Line', backref='division', cascade='all, delete-orphan',
                             order_by='Line.created_at')

    def to_dict(self):
        return {
            'id':        self.id,
            'project_id': self.project_id,
            'name':      self.name,
            'latitude':  self.latitude,
            'longitude': self.longitude,
            'client_name':    self.client_name or '',
            'state':          self.state or '',
            'planned_towers': self.planned_towers,
            'line_count': len(self.lines),
        }


class Line(db.Model):
    """A transmission line belonging to a Division."""
    __tablename__ = 'lines'

    id           = db.Column(db.Integer, primary_key=True)
    division_id  = db.Column(db.Integer, db.ForeignKey('divisions.id', ondelete='CASCADE'), nullable=False)
    name         = db.Column(db.String(150), nullable=False)
    start_lat    = db.Column(db.Float, nullable=False)
    start_lng    = db.Column(db.Float, nullable=False)
    end_lat      = db.Column(db.Float, nullable=False)
    end_lng      = db.Column(db.Float, nullable=False)
    length_km    = db.Column(db.Float, default=0)
    tower_count  = db.Column(db.Integer, default=0)
    kml_path     = db.Column(db.String(255), default='')   # relative to /static
    # Used in the per-tower report's "General Information" section — set
    # once per line (a survey flight typically covers a whole line at
    # once), rather than re-entered per tower or per report.
    voltage_level    = db.Column(db.String(50), default='')
    survey_date      = db.Column(db.Date, nullable=True)
    pilot_name       = db.Column(db.String(150), default='')
    inspection_name  = db.Column(db.String(150), default='')
    # Which KML ExtendedData attribute keys to actually show in the Tower
    # Details panel — a KML often carries dozens of raw fields (styleUrl,
    # styleHash, etc.) that aren't meaningful to look at; Admin picks a
    # subset per line. JSON-encoded list of key names; empty/null means
    # "not configured yet" — falls back to showing everything.
    visible_kml_attrs = db.Column(db.Text, default='')
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        import json as _json
        try:
            visible_attrs = _json.loads(self.visible_kml_attrs) if self.visible_kml_attrs else None
        except (ValueError, TypeError):
            visible_attrs = None
        return {
            'id':          self.id,
            'division_id': self.division_id,
            'name':        self.name,
            'start':       {'lat': self.start_lat, 'lng': self.start_lng},
            'end':         {'lat': self.end_lat,   'lng': self.end_lng},
            'length_km':   self.length_km or 0,
            'tower_count': self.tower_count or 0,
            'kml_url':     f'/static/{self.kml_path}' if self.kml_path else '',
            'voltage_level':   self.voltage_level or '',
            'survey_date':     self.survey_date.strftime('%Y-%m-%d') if self.survey_date else '',
            'pilot_name':      self.pilot_name or '',
            'inspection_name': self.inspection_name or '',
            'visible_kml_attrs': visible_attrs,
        }


class CorridorPhoto(db.Model):
    """A photo taken anywhere along a Line's corridor — NOT tied to a
    specific tower the way TowerPhoto is (no KML point association, no
    70m-of-a-tower requirement). Shown as its own dot on the map at its
    own GPS position, in one flat gallery (not grouped by tower), with an
    optional short observation note that shows as small text next to its
    map dot once set."""
    __tablename__ = 'corridor_photos'

    id          = db.Column(db.Integer, primary_key=True)
    line_id     = db.Column(db.Integer, db.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False)
    image_path  = db.Column(db.String(255), nullable=False)
    gps_lat     = db.Column(db.Float, nullable=False)
    gps_lng     = db.Column(db.Float, nullable=False)
    observation = db.Column(db.Text, default='')
    uploaded_by = db.Column(db.String(120), default='')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':          self.id,
            'line_id':     self.line_id,
            'image_url':   f'/static/{self.image_path}' if self.image_path else '',
            'gps_lat':     self.gps_lat,
            'gps_lng':     self.gps_lng,
            'observation': self.observation or '',
            'uploaded_by': self.uploaded_by or '',
            'created_at':  self.created_at.strftime('%d %b %Y %H:%M') if self.created_at else '',
        }


class TowerPhoto(db.Model):
    """A photo attached to one tower point on a Line's map.

    Tower points themselves come from parsing the Line's KML file
    client-side on every page load — they're not individual database rows.
    tower_label (the point's name/number as it appears in the KML, e.g.
    "T12") together with line_id is what stably identifies "this same
    tower" across page loads, so photos stay attached to the right point
    even though the point itself isn't a persisted record."""
    __tablename__ = 'tower_photos'

    id          = db.Column(db.Integer, primary_key=True)
    line_id     = db.Column(db.Integer, db.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False)
    tower_label = db.Column(db.String(150), nullable=False)
    image_path  = db.Column(db.String(255), nullable=False)
    uploaded_by = db.Column(db.String(120), default='')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    gps_lat     = db.Column(db.Float, nullable=True)  # this PHOTO's own EXIF/capture GPS — not the tower's KML position
    gps_lng     = db.Column(db.Float, nullable=True)
    # SHA-256 of the file's own bytes — lets re-uploading the exact same
    # photo (e.g. running "Add Image Folder" on the same folder twice by
    # mistake) get caught and rejected instead of creating a duplicate
    # row and a duplicate copy of the file on disk.
    content_hash = db.Column(db.String(64), nullable=True, index=True)

    def to_dict(self):
        return {
            'id':          self.id,
            'line_id':     self.line_id,
            'tower_label': self.tower_label,
            'image_url':   f'/static/{self.image_path}' if self.image_path else '',
            'uploaded_by': self.uploaded_by or '',
            'created_at':  self.created_at.strftime('%d %b %Y %H:%M') if self.created_at else '',
            'defect_count': len(self.defects),
            'gps_lat':     self.gps_lat,
            'gps_lng':     self.gps_lng,
        }


class SmeAssignment(db.Model):
    """An SME assigned to inspect a specific Line — mirrors PilotAssignment.
    Admin assigns a line to an SME; once assigned, that SME can mark RGB
    defects, take thermal measurements, view corridor photos, and generate
    the tower report for that line, the same as Admin can, but without any
    upload ability (uploading photos stays Admin-only)."""
    __tablename__ = 'sme_assignments'
    __table_args__ = (db.UniqueConstraint('line_id', 'sme_user_id', name='uq_sme_line'),)

    id          = db.Column(db.Integer, primary_key=True)
    line_id     = db.Column(db.Integer, db.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False)
    sme_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    assigned_by = db.Column(db.String(120), default='')
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    seen_by_sme = db.Column(db.Boolean, default=False)

    line = db.relationship('Line')
    sme  = db.relationship('User')

    def to_dict(self):
        return {
            'id': self.id, 'line_id': self.line_id, 'sme_user_id': self.sme_user_id,
            'sme_name': self.sme.username if self.sme else '',
            'assigned_by': self.assigned_by or '',
            'assigned_at': self.assigned_at.strftime('%d %b %Y, %H:%M') if self.assigned_at else '',
            'seen_by_sme': bool(self.seen_by_sme),
        }


class PilotLocation(db.Model):
    """A Pilot's most recent known GPS position — one row per pilot,
    overwritten on each ping rather than logged historically, since only
    "where are they right now" matters for Admin's live map. Updated
    periodically from the Pilot's own device while their portal tab is
    open (with their permission — browser geolocation)."""
    __tablename__ = 'pilot_locations'

    pilot_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    lat           = db.Column(db.Float, nullable=False)
    lng           = db.Column(db.Float, nullable=False)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow)

    pilot = db.relationship('User')

    def to_dict(self):
        return {
            'pilot_user_id': self.pilot_user_id,
            'pilot_name': self.pilot.username if self.pilot else '',
            'lat': self.lat, 'lng': self.lng,
            'updated_at': self.updated_at.strftime('%d %b %Y %H:%M') if self.updated_at else '',
        }


class PilotAssignment(db.Model):
    """A Pilot assigned to fly/photograph a specific Line. seen_by_pilot
    drives the "new assignment" notification popup on the Pilot's
    dashboard — set False when Admin assigns (or reassigns) the line,
    flipped True once the Pilot has actually seen it there."""
    __tablename__ = 'pilot_assignments'
    __table_args__ = (db.UniqueConstraint('line_id', 'pilot_user_id', name='uq_pilot_line'),)

    id            = db.Column(db.Integer, primary_key=True)
    line_id       = db.Column(db.Integer, db.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False)
    pilot_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    assigned_by   = db.Column(db.String(120), default='')
    assigned_at   = db.Column(db.DateTime, default=datetime.utcnow)
    seen_by_pilot = db.Column(db.Boolean, default=False)

    line  = db.relationship('Line')
    pilot = db.relationship('User')

    def to_dict(self):
        return {
            'id': self.id, 'line_id': self.line_id, 'pilot_user_id': self.pilot_user_id,
            'pilot_name': self.pilot.username if self.pilot else '',
            'assigned_by': self.assigned_by or '',
            'assigned_at': self.assigned_at.strftime('%d %b %Y, %H:%M') if self.assigned_at else '',
            'seen_by_pilot': bool(self.seen_by_pilot),
        }


class TowerInspectionStatus(db.Model):
    """Per-tower "Inspection Done" flag — line_id + tower_label together
    identify the tower (same pattern as TowerPhoto, since towers aren't
    their own database rows). A tower with zero marked defects could
    either be a genuinely good tower or one nobody has finished reviewing
    yet — this flag is how Admin explicitly says "done, this one's
    reviewed" rather than the report generator or the client guessing
    from defect count alone. Both report generation and client-facing
    visibility of a tower's photos/defects are gated on this being True.

    Also holds the pilot's zone classification (red/yellow/green) for
    this tower — a separate concern from inspection_done (set by the
    pilot in the field before capturing, not by Admin after review), but
    keyed the same way, so it lives on the same per-tower status row
    rather than a second table."""
    __tablename__ = 'tower_inspection_status'
    __table_args__ = (db.UniqueConstraint('line_id', 'tower_label', name='uq_tower_inspection'),)

    id             = db.Column(db.Integer, primary_key=True)
    line_id        = db.Column(db.Integer, db.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False)
    tower_label    = db.Column(db.String(150), nullable=False)
    inspection_done = db.Column(db.Boolean, default=False)
    marked_by      = db.Column(db.String(120), default='')
    marked_at      = db.Column(db.DateTime, nullable=True)
    zone           = db.Column(db.String(10), default='')   # '', 'red', 'yellow', 'green'
    zone_set_by    = db.Column(db.String(120), default='')
    zone_set_at    = db.Column(db.DateTime, nullable=True)
    # Pilot's own "done at this tower" — set zone, drone photos captured
    # separately (not through the app), then Submit. Distinct from
    # inspection_done above, which is Admin/SME reviewing the actual
    # uploaded photos afterward — this just means the pilot's visit here
    # is finished.
    pilot_submitted    = db.Column(db.Boolean, default=False)
    pilot_submitted_by = db.Column(db.String(120), default='')
    pilot_submitted_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'line_id': self.line_id, 'tower_label': self.tower_label,
            'inspection_done': bool(self.inspection_done),
            'marked_by': self.marked_by or '',
            'marked_at': self.marked_at.strftime('%d %b %Y %H:%M') if self.marked_at else '',
            'zone': self.zone or '',
            'zone_set_by': self.zone_set_by or '',
            'zone_set_at': self.zone_set_at.strftime('%d %b %Y %H:%M') if self.zone_set_at else '',
            'pilot_submitted': bool(self.pilot_submitted),
            'pilot_submitted_by': self.pilot_submitted_by or '',
            'pilot_submitted_at': self.pilot_submitted_at.strftime('%d %b %Y %H:%M') if self.pilot_submitted_at else '',
        }


class TowerDefect(db.Model):
    """A defect marked directly on a tower photo (polygon/rectangle/circle
    drawn over the 2D image, plus a short observation form) — this is the
    TRANS module's equivalent of the chimney module's 3D defect markings,
    just on a flat photo instead of a 3D model.

    shape_coords are stored as PERCENTAGES of the image's width/height
    (0-100), not raw pixels — that keeps the marking correctly aligned
    regardless of what size the image happens to be displayed at, since
    percentage-of-image-bounds is resolution-independent while raw pixel
    coordinates would only be correct at the exact display size they were
    drawn at."""
    __tablename__ = 'tower_defects'

    id             = db.Column(db.Integer, primary_key=True)
    tower_photo_id = db.Column(db.Integer, db.ForeignKey('tower_photos.id', ondelete='CASCADE'), nullable=False)
    shape_type     = db.Column(db.String(20), nullable=False)   # 'polygon' | 'rect' | 'circle'
    shape_coords   = db.Column(db.Text, nullable=False)         # JSON list of {x, y} in % of image bounds
    component_name = db.Column(db.String(150), default='')
    location       = db.Column(db.String(20), default='')       # 'Top' | 'Middle' | 'Bottom'
    defect_type    = db.Column(db.String(100), default='')      # e.g. 'Corrosion', 'Broken Insulator'
    observation    = db.Column(db.String(255), default='')
    severity       = db.Column(db.String(20), default='Minor')  # matches the chimney module's severity vocabulary
    status         = db.Column(db.String(20), default='Open')   # 'Open' | 'Closed'
    comments       = db.Column(db.Text, default='')
    created_by     = db.Column(db.String(120), default='')
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    photo = db.relationship('TowerPhoto', backref=db.backref('defects', cascade='all, delete-orphan',
                                                               order_by='TowerDefect.created_at'))

    def to_dict(self):
        coords = []
        if self.shape_coords:
            try:
                coords = json.loads(self.shape_coords)
            except (TypeError, ValueError):
                coords = []
        return {
            'id':              self.id,
            'tower_photo_id':  self.tower_photo_id,
            'shape_type':      self.shape_type,
            'shape_coords':    coords,
            'component_name':  self.component_name or '',
            'location':        self.location or '',
            'defect_type':     self.defect_type or '',
            'observation':     self.observation or '',
            'severity':        self.severity or 'Minor',
            'status':          self.status or 'Open',
            'comments':        self.comments or '',
            'created_by':      self.created_by or '',
            'created_at':      self.created_at.strftime('%d %b %Y %H:%M') if self.created_at else '',
        }


class ThermalPoint(db.Model):
    """A temperature measurement on a thermal photo — Point (single spot),
    Rectangle (area), or Line, matching DJI Thermal Analysis Tool 3's three
    measurement types. Reads the actual radiometric temperature(s) at the
    marked location(s), not just a visual reading off the color palette.

    shape_coords follows the same percentage-of-image-bounds convention as
    TowerDefect.shape_coords (resolution-independent regardless of display
    size): 1 point for 'point', 2 corners for 'rect', 2 endpoints for
    'line'. x_pct/y_pct duplicate the FIRST coordinate for quick access
    (e.g. label placement) without parsing shape_coords every time.

    avg_c/min_c/max_c are computed once at creation time by decoding the
    R-JPEG's embedded radiometric data (see thermal_decode.py) and stored,
    rather than recomputed on every view — decoding needs the DJI Thermal
    SDK native library and is not free. For a 'point' shape all three are
    equal (a single reading); temperature_c mirrors avg_c for older callers.
    raw_avg/raw_min/raw_max are the underlying raw sensor value(s) DJI's
    calibration converted into avg_c/min_c/max_c — read separately from
    the file (see thermal_decode.get_raw_matrix()), so nullable
    independently of whether the temperature itself decoded successfully.
    Viewing is open to Admin + Client (same as TowerDefect); adding/
    deleting a point is Admin-only."""
    __tablename__ = 'thermal_points'

    id             = db.Column(db.Integer, primary_key=True)
    tower_photo_id = db.Column(db.Integer, db.ForeignKey('tower_photos.id', ondelete='CASCADE'), nullable=False)
    x_pct          = db.Column(db.Float, nullable=False)
    y_pct          = db.Column(db.Float, nullable=False)
    shape_type     = db.Column(db.String(10), default='point')  # 'point' | 'rect' | 'line'
    shape_coords   = db.Column(db.Text, default='')             # JSON list of {x, y} in % of image bounds
    temperature_c  = db.Column(db.Float, nullable=True)   # = avg_c; null if the SDK couldn't decode this image
    min_c          = db.Column(db.Float, nullable=True)
    max_c          = db.Column(db.Float, nullable=True)
    avg_c          = db.Column(db.Float, nullable=True)
    raw_avg        = db.Column(db.Float, nullable=True)   # underlying raw sensor value(s) behind avg_c/min_c/max_c
    raw_min        = db.Column(db.Float, nullable=True)
    raw_max        = db.Column(db.Float, nullable=True)
    label          = db.Column(db.String(100), default='')
    error          = db.Column(db.String(255), default='')  # decode failure reason, shown instead of a value
    created_by     = db.Column(db.String(120), default='')
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    photo = db.relationship('TowerPhoto', backref=db.backref('thermal_points', cascade='all, delete-orphan',
                                                               order_by='ThermalPoint.created_at'))

    def to_dict(self):
        coords = []
        if self.shape_coords:
            try:
                coords = json.loads(self.shape_coords)
            except (TypeError, ValueError):
                coords = []
        return {
            'id':              self.id,
            'tower_photo_id':  self.tower_photo_id,
            'x_pct':           self.x_pct,
            'y_pct':           self.y_pct,
            'shape_type':      self.shape_type or 'point',
            'shape_coords':    coords,
            'temperature_c':   self.temperature_c,
            'min_c':           self.min_c,
            'max_c':           self.max_c,
            'avg_c':           self.avg_c,
            'raw_avg':         self.raw_avg,
            'raw_min':         self.raw_min,
            'raw_max':         self.raw_max,
            'label':           self.label or '',
            'error':           self.error or '',
            'created_by':      self.created_by or '',
            'created_at':      self.created_at.strftime('%d %b %Y %H:%M') if self.created_at else '',
        }


class TowerReport(db.Model):
    """A generated per-tower PDF report (RGB Visual Inspection Report).
    Generation is Admin-only; once generated, the file is stored on disk
    (like tower photos) with this row pointing at it, so Client sessions
    can download the already-generated report without being able to
    trigger generation themselves. Re-generating for the same tower
    replaces the existing row/file rather than accumulating duplicates."""
    __tablename__ = 'tower_reports'

    id           = db.Column(db.Integer, primary_key=True)
    line_id      = db.Column(db.Integer, db.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False)
    tower_label  = db.Column(db.String(150), nullable=False)
    report_path  = db.Column(db.String(255), nullable=False)   # relative to /static
    generated_by = db.Column(db.String(120), default='')
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':           self.id,
            'line_id':      self.line_id,
            'tower_label':  self.tower_label,
            'report_url':   f'/static/{self.report_path}' if self.report_path else '',
            'generated_by': self.generated_by or '',
            'generated_at': self.generated_at.strftime('%d %b %Y %H:%M') if self.generated_at else '',
        }


class Announcement(db.Model):
    """Admin-posted announcements shown as a bell popup on the home page.
    Replaces the earlier Help-ticket system — this is one-way (Admin ->
    everyone) rather than users raising individual problems."""
    __tablename__ = 'announcements'

    id          = db.Column(db.Integer, primary_key=True)
    title       = db.Column(db.String(150), nullable=False)
    message     = db.Column(db.Text, default='')
    image_path  = db.Column(db.String(255), default='')   # relative to /static
    created_by  = db.Column(db.String(120), default='')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'message': self.message or '',
            'image_url': f'/static/{self.image_path}' if self.image_path else '',
            'created_by': self.created_by or '',
            'created_at': _fmt_ist(self.created_at, suffix=False),
        }


class HelpTicket(db.Model):
    """A help/support request raised from Settings → Help, by an Admin,
    Client, or Pilot describing a problem. Feeds the top notification
    bell — Admin works through each ticket via the 'Checking' /
    'Problem Resolved' actions."""
    __tablename__ = 'help_tickets'

    STATUSES = ('Open', 'Checking', 'Resolved')

    id                 = db.Column(db.Integer, primary_key=True)
    subject            = db.Column(db.String(150), nullable=False)
    description        = db.Column(db.Text, default='')
    reporter_type      = db.Column(db.String(20), default='Client')  # Client / Pilot / Admin — self-selected at submission
    submitted_by       = db.Column(db.String(120), default='')
    status             = db.Column(db.String(20), default='Open')    # Open (new) / Checking / Resolved
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at         = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_by        = db.Column(db.String(120), default='')
    seen_by_reporter   = db.Column(db.Boolean, default=True)   # flips to False whenever Admin updates status, so the raiser gets notified

    def to_dict(self):
        return {
            'id': self.id,
            'subject': self.subject,
            'description': self.description or '',
            'reporter_type': self.reporter_type or 'Client',
            'submitted_by': self.submitted_by or '',
            'status': self.status or 'Open',
            'created_at': _fmt_ist(self.created_at, '%d %b %Y %H:%M', suffix=False),
            'updated_at': _fmt_ist(self.updated_at, '%d %b %Y %H:%M', suffix=False),
            'resolved_by': self.resolved_by or '',
            'seen_by_reporter': self.seen_by_reporter if self.seen_by_reporter is not None else True,
        }


class ContactInquiry(db.Model):
    """A query submitted from the public homepage's contact box, by
    someone who isn't logged in and may not even have an account yet —
    a prospective client reaching out. Separate from HelpTicket, which
    is for existing logged-in users reporting a problem."""
    __tablename__ = 'contact_inquiries'

    STATUSES = ('New', 'Contacted', 'Closed')

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(120), nullable=False)
    email       = db.Column(db.String(150), nullable=False)
    company     = db.Column(db.String(150), default='')
    message     = db.Column(db.Text, nullable=False)
    status      = db.Column(db.String(20), default='New')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'company': self.company or '',
            'message': self.message,
            'status': self.status or 'New',
            'created_at': _fmt_ist(self.created_at, '%d %b %Y %H:%M', suffix=False),
        }
