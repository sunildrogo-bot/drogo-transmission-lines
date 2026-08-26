"""
users_routes.py — Blueprint for all /users/* routes.
Register in app.py with: app.register_blueprint(users_bp)
"""
import os
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app
from werkzeug.utils import secure_filename
import users as user_store
import mailer

users_bp = Blueprint('users', __name__)

PHOTO_EXTS = {'jpg', 'jpeg', 'png', 'webp'}


def _save_user_photo(file_storage):
    """Save an uploaded profile photo under static/uploads/user_photos/
    and return the path relative to /static, '' if no file was given, or
    None if the file type isn't allowed (caller should treat that as a
    validation error)."""
    if not file_storage or not file_storage.filename:
        return ''
    ext = file_storage.filename.rsplit('.', 1)[-1].lower() if '.' in file_storage.filename else ''
    if ext not in PHOTO_EXTS:
        return None
    base_dir = current_app.root_path if hasattr(current_app, 'root_path') else '.'
    folder_fs = os.path.join(base_dir, 'static', 'uploads', 'user_photos')
    os.makedirs(folder_fs, exist_ok=True)
    safe_name = secure_filename(file_storage.filename)
    name_root, name_ext = os.path.splitext(safe_name)
    final_name = safe_name
    i = 1
    while os.path.exists(os.path.join(folder_fs, final_name)):
        final_name = f"{name_root}_{i}{name_ext}"
        i += 1
    file_storage.save(os.path.join(folder_fs, final_name))
    return f"uploads/user_photos/{final_name}"


def _require_admin():
    """Return a redirect/response if user is not a logged-in Admin, else None."""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Admin access required.'}), 403
    return None


# ── Page ─────────────────────────────────────────────────────────────────────

@users_bp.route('/users')
def users_page():
    guard = _require_admin()
    if guard:
        return guard
    return render_template(
        'users.html',
        user_name=session['user_name'],
        roles=user_store.ROLES,
        modules=user_store.MODULES,
        statuses=user_store.STATUSES,
    )


# ── REST API ──────────────────────────────────────────────────────────────────

@users_bp.route('/api/users', methods=['GET'])
def api_list():
    guard = _require_admin()
    if guard:
        return guard

    users = user_store.get_all(
        search=request.args.get('search', ''),
        role=request.args.get('role', ''),
        module=request.args.get('module', ''),
        status=request.args.get('status', ''),
    )
    return jsonify({'users': users, 'total': len(users)})


@users_bp.route('/api/users/<uid>', methods=['GET'])
def api_get(uid):
    guard = _require_admin()
    if guard:
        return guard

    user = user_store.get_by_id(uid)
    if not user:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(user)


@users_bp.route('/api/users/projects-for-module', methods=['GET'])
def api_projects_for_module():
    """Which projects exist in a given module — populates the "restrict to
    specific projects" checkbox list in the Add/Edit User form."""
    guard = _require_admin()
    if guard:
        return guard
    module = (request.args.get('module') or '').strip()
    if not module:
        return jsonify({'projects': []})

    from models import Project
    rows = Project.query.filter_by(module=module).order_by(Project.name.asc()).all()
    return jsonify({'projects': [{'id': p.id, 'name': p.name} for p in rows]})


@users_bp.route('/api/users', methods=['POST'])
def api_create():
    guard = _require_admin()
    if guard:
        return guard

    # multipart/form-data, not JSON — needed to carry the photo file
    # alongside the rest of the fields. roles/modules arrive as repeated
    # form keys (roles=Admin&roles=Client User), read with getlist.
    data = {
        'username': request.form.get('username', ''),
        'email':    request.form.get('email', ''),
        'contact':  request.form.get('contact', ''),
        'status':   request.form.get('status', ''),
        'roles':    request.form.getlist('roles'),
        'modules':  request.form.getlist('modules'),
        'project_ids':          request.form.getlist('project_ids'),
    }
    errors = _validate(data)
    if errors:
        return jsonify({'error': errors}), 400
    if user_store.get_by_email(data['email']):
        return jsonify({'error': 'A user with this email already exists.'}), 409

    photo_file = request.files.get('photo')
    if photo_file and photo_file.filename:
        saved = _save_user_photo(photo_file)
        if saved is None:
            return jsonify({'error': 'Profile photo must be a .jpg, .png, or .webp file.'}), 400
        data['photo_path'] = saved

    new_user = user_store.create(data)

    # Send a one-time password-setup link instead of transmitting a reusable
    # temporary password in plaintext. The token is removed from the API
    # response unless Admin needs a manual fallback because email failed.
    from security_controls import public_url
    setup_token = new_user.pop('_setup_token')
    setup_url = public_url('reset_password', token=setup_token)

    email_sent = mailer.send_welcome_email(
        to_email=new_user['email'],
        username=new_user['username'],
        setup_url=setup_url,
    )
    new_user['_email_sent'] = email_sent
    if not email_sent:
        new_user['_setup_url'] = setup_url

    return jsonify(new_user), 201


@users_bp.route('/api/users/<uid>', methods=['PUT'])
def api_update(uid):
    guard = _require_admin()
    if guard:
        return guard

    from models import User, db
    try:
        user_row = db.session.get(User, int(uid))
    except (TypeError, ValueError):
        user_row = None
    if not user_row:
        return jsonify({'error': 'Not found'}), 404
    previous_photo_path = user_row.photo_path
    replacement_photo_path = ''

    data = {
        'username': request.form.get('username', ''),
        'email':    request.form.get('email', ''),
        'contact':  request.form.get('contact', ''),
        'status':   request.form.get('status', ''),
        'roles':    request.form.getlist('roles'),
        'modules':  request.form.getlist('modules'),
        'project_ids':          request.form.getlist('project_ids'),
    }
    errors = _validate(data)
    if errors:
        return jsonify({'error': errors}), 400

    photo_file = request.files.get('photo')
    if photo_file and photo_file.filename:
        saved = _save_user_photo(photo_file)
        if saved is None:
            return jsonify({'error': 'Profile photo must be a .jpg, .png, or .webp file.'}), 400
        data['photo_path'] = saved
        replacement_photo_path = saved

    updated = user_store.update(uid, data)
    if not updated:
        return jsonify({'error': 'Not found'}), 404

    if replacement_photo_path and previous_photo_path != replacement_photo_path:
        from storage_cleanup import delete_stored_files
        cleanup = delete_stored_files([previous_photo_path])
        if cleanup['errors']:
            current_app.logger.warning(
                'Old profile photo cleanup failed for user %s: %s',
                uid, cleanup['errors'],
            )

    # Keep this admin's own session in sync if they edit their own roles/modules
    if str(uid) == str(session.get('user_id')):
        session['user_name'] = updated['username']
        session['user_email'] = updated['email']
        session['all_roles'] = updated['roles']
        session['modules']   = updated['modules']
        session['session_version'] = int(updated.get('session_version') or 1)
        if session.get('role') not in updated['roles']:
            session['role'] = updated['roles'][0] if updated['roles'] else 'Client User'

    return jsonify(updated)


@users_bp.route('/api/users/<uid>', methods=['DELETE'])
def api_delete(uid):
    guard = _require_admin()
    if guard:
        return guard
    if str(uid) == str(session.get('user_id')):
        return jsonify({'error': 'You cannot delete the account currently signed in.'}), 400

    ok = user_store.delete(uid)
    if not ok:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'deleted': uid})


# ── Validation ────────────────────────────────────────────────────────────────

def _validate(data: dict) -> str:
    if not data.get('username', '').strip():
        return 'Username is required.'
    if not data.get('email', '').strip():
        return 'Email is required.'
    if '@' not in data.get('email', ''):
        return 'Invalid email address.'
    roles = data.get('roles') or []
    if not isinstance(roles, list) or not roles:
        return 'At least one role must be selected.'
    invalid_roles = [r for r in roles if r not in user_store.ROLES]
    if invalid_roles:
        return f"Invalid role(s): {', '.join(invalid_roles)}"
    modules = data.get('modules') or []
    if not isinstance(modules, list) or not modules:
        return 'At least one module must be selected.'
    invalid = [m for m in modules if m not in user_store.MODULES]
    if invalid:
        return f"Invalid module(s): {', '.join(invalid)}"
    return ''
