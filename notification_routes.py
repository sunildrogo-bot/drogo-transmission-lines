from flask import Blueprint, jsonify, request, session
from models import db, UserNotification

notification_bp = Blueprint('notification_bp', __name__)


@notification_bp.route('/api/notifications')
def api_notifications():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Login required.'}), 401
    rows = (UserNotification.query.filter_by(user_id=user_id)
            .order_by(UserNotification.id.desc()).limit(50).all())
    unread = UserNotification.query.filter_by(user_id=user_id, is_read=False).count()
    return jsonify({'notifications': [row.to_dict() for row in rows], 'unread': unread})


@notification_bp.route('/api/notifications/read', methods=['POST'])
def api_notifications_read():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Login required.'}), 401
    data = request.get_json(silent=True) or {}
    ids = data.get('ids')
    query = UserNotification.query.filter_by(user_id=user_id, is_read=False)
    if isinstance(ids, list):
        clean_ids = [int(value) for value in ids if str(value).isdigit()]
        query = query.filter(UserNotification.id.in_(clean_ids)) if clean_ids else query.filter(db.false())
    query.update({'is_read': True}, synchronize_session=False)
    db.session.commit()
    return jsonify({'ok': True})
