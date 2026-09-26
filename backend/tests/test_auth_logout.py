from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.routes.auth import logout
from app.models import AuditLog, Base, User


def test_logout_revokes_existing_session_version_and_is_audited():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        user = User(
            username="logout-user",
            password_hash="unused",
            role="operator",
            session_version=3,
        )
        db.add(user)
        db.commit()
        request = SimpleNamespace(
            state=SimpleNamespace(correlation_id="logout-correlation"),
            client=None,
        )

        assert logout(request=request, user=user, db=db) == {"ok": True}
        assert user.session_version == 4
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "USER_LOGOUT"))
        assert audit is not None
        assert audit.actor_user_id == user.id
