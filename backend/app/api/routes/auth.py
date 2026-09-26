from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.schemas.auth import LoginRequest, PasswordChangeRequest, TokenResponse, UserResponse
from app.services.audit import write_audit
from app.services.login_rate_limit import (
    clear_login_failures,
    login_blocked,
    record_login_failure,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip_address = request.client.host if request.client else None
    if login_blocked(payload.username, ip_address):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录失败次数过多，请稍后再试",
        )
    user = db.scalar(select(User).where(User.username == payload.username))
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        record_login_failure(payload.username, ip_address)
        write_audit(
            db,
            action="USER_LOGIN_FAILED",
            entity_type="USER",
            entity_id=payload.username,
            actor_type="ANONYMOUS",
            result="FAILURE",
            correlation_id=request.state.correlation_id,
            ip_address=ip_address,
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    clear_login_failures(payload.username, ip_address)
    token = create_access_token(user.id, user.role, user.session_version)
    write_audit(
        db,
        action="USER_LOGIN",
        entity_type="USER",
        entity_id=user.id,
        actor_user_id=user.id,
        actor_type="USER",
        correlation_id=request.state.correlation_id,
        ip_address=ip_address,
    )
    db.commit()
    return TokenResponse(
        access_token=token,
        expires_in=get_settings().access_token_minutes * 60,
    )


def _validate_password_strength(value):
    classes = [
        any(character.islower() for character in value),
        any(character.isupper() for character in value),
        any(character.isdigit() for character in value),
        any(not character.isalnum() for character in value),
    ]
    if sum(classes) < 3:
        raise HTTPException(status_code=422, detail="新密码必须包含至少三类字符")


@router.post("/change-password", response_model=TokenResponse)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=422, detail="新密码不能与当前密码相同")
    _validate_password_strength(payload.new_password)
    user.password_hash = hash_password(payload.new_password)
    user.session_version += 1
    write_audit(
        db,
        action="USER_PASSWORD_CHANGED",
        entity_type="USER",
        entity_id=user.id,
        actor_user_id=user.id,
        actor_type="USER",
        correlation_id=request.state.correlation_id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return TokenResponse(
        access_token=create_access_token(user.id, user.role, user.session_version),
        expires_in=get_settings().access_token_minutes * 60,
    )


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/logout")
def logout(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user.session_version += 1
    write_audit(
        db,
        action="USER_LOGOUT",
        entity_type="USER",
        entity_id=user.id,
        actor_user_id=user.id,
        actor_type="USER",
        correlation_id=request.state.correlation_id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return {"ok": True}
