import uuid

from fastapi.encoders import jsonable_encoder

from app.models import AuditLog


def write_audit(
    db,
    *,
    action,
    entity_type,
    entity_id=None,
    actor_user_id=None,
    actor_type="SYSTEM",
    before_data=None,
    after_data=None,
    result="SUCCESS",
    correlation_id=None,
    ip_address=None,
):
    entry = AuditLog(
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        before_data=jsonable_encoder(before_data or {}),
        after_data=jsonable_encoder(after_data or {}),
        result=result,
        correlation_id=correlation_id or str(uuid.uuid4()),
        ip_address=ip_address,
    )
    db.add(entry)
    return entry
