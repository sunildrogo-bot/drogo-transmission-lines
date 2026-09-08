"""Database-only notification helpers shared by application workflows."""
from models import db, Role, UserNotification


def notify_user(user_id, title, message='', category='info', link_url=''):
    if user_id:
        # Bound storage without a background cleanup job: retain roughly the
        # latest 200 alerts per user and prune a small oldest block on demand.
        existing = UserNotification.query.filter_by(user_id=user_id).count()
        if existing >= 200:
            oldest_ids = [row[0] for row in (db.session.query(UserNotification.id)
                          .filter_by(user_id=user_id).order_by(UserNotification.id.asc()).limit(25).all())]
            if oldest_ids:
                UserNotification.query.filter(UserNotification.id.in_(oldest_ids)).delete(synchronize_session=False)
        db.session.add(UserNotification(
            user_id=user_id, title=(title or 'Notification')[:180],
            message=(message or '')[:500], category=(category or 'info')[:40],
            link_url=(link_url or '')[:500]))


def notify_users(user_ids, title, message='', category='info', link_url=''):
    for user_id in set(user_ids or []):
        notify_user(user_id, title, message, category, link_url)


def admin_user_ids():
    role = Role.query.filter_by(name='Admin').first()
    return [user.id for user in (role.users if role else []) if user.status == 'Active']
