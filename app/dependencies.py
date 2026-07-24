from __future__ import annotations

from collections.abc import Generator

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import User, UserRole


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(request: Request, db: Session) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    user = db.get(User, int(user_id))
    if not user or not user.is_active:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user


def require_admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")


def can_manage_user(actor: User, target: User) -> bool:
    if actor.role == UserRole.ADMIN:
        return True
    return actor.role == UserRole.RESELLER and target.parent_id == actor.id and target.role == UserRole.USER


def visible_owner_ids(user: User) -> set[int] | None:
    if user.role == UserRole.ADMIN:
        return None
    if user.role == UserRole.RESELLER:
        return {user.id, *(child.id for child in user.children if child.is_active)}
    return {user.id}
