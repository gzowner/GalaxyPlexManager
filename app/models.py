from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    RESELLER = "reseller"
    USER = "user"


class DeploymentStatus(str, enum.Enum):
    PENDING = "pending"
    CLONING = "cloning"
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    CLAIM_REQUIRED = "claim_required"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.USER, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deployment_limit: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    parent: Mapped["User | None"] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list["User"]] = relationship(back_populates="parent")
    deployments: Mapped[list["Deployment"]] = relationship(back_populates="owner", foreign_keys="Deployment.owner_id")


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    host: Mapped[str] = mapped_column(String(255), default="127.0.0.1")
    docker_url: Mapped[str] = mapped_column(String(255), default="unix:///var/run/docker.sock")
    base_path: Mapped[str] = mapped_column(String(500))
    template_path: Mapped[str] = mapped_column(String(500))
    media_mounts_json: Mapped[str] = mapped_column(Text, default="[]")
    port_start: Mapped[int] = mapped_column(Integer, default=32401)
    port_end: Mapped[int] = mapped_column(Integer, default=32999)
    enable_hardware: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    deployments: Mapped[list["Deployment"]] = relationship(back_populates="node")


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    container_name: Mapped[str] = mapped_column(String(160), unique=True)
    host_port: Mapped[int] = mapped_column(Integer, index=True)
    config_path: Mapped[str] = mapped_column(String(500))
    status: Mapped[DeploymentStatus] = mapped_column(Enum(DeploymentStatus), default=DeploymentStatus.PENDING)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    owner: Mapped[User] = relationship(back_populates="deployments", foreign_keys=[owner_id])
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    node: Mapped[Node] = relationship(back_populates="deployments")
