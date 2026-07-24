from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .database import Base, SessionLocal, engine
from .dependencies import can_manage_user, current_user, get_db, require_admin, visible_owner_ids
from .models import Deployment, DeploymentStatus, Node, User, UserRole
from .security import hash_password, verify_password
from .services.docker_service import DockerService
from .services.plex_clone import clone_template, safe_remove_config

settings = get_settings()
app = FastAPI(title=settings.app_name)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    https_only=settings.session_https_only,
    same_site="lax",
    max_age=60 * 60 * 12,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.role == UserRole.ADMIN))
        if not admin:
            db.add(
                User(
                    username=settings.admin_username,
                    email=settings.admin_email,
                    password_hash=hash_password(settings.admin_password),
                    role=UserRole.ADMIN,
                    deployment_limit=9999,
                )
            )
        node = db.scalar(select(Node).limit(1))
        if not node:
            db.add(
                Node(
                    name=settings.default_node_name,
                    host=settings.default_node_host,
                    base_path=settings.default_node_base_path,
                    template_path=settings.default_template_path,
                    media_mounts_json=settings.default_media_mounts,
                    port_start=settings.default_port_start,
                    port_end=settings.default_port_end,
                )
            )
        db.commit()


def redirect(path: str, message: str | None = None, error: str | None = None) -> RedirectResponse:
    query = ""
    if message:
        query = f"?message={quote_plus(message)}"
    elif error:
        query = f"?error={quote_plus(error)}"
    return RedirectResponse(f"{path}{query}", status_code=status.HTTP_303_SEE_OTHER)


def page_context(request: Request, user: User, **extra: object) -> dict[str, object]:
    return {
        "request": request,
        "current_user": user,
        "app_name": settings.app_name,
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
        **extra,
    }


def get_user_or_login(request: Request, db: Session) -> User | RedirectResponse:
    try:
        return current_user(request, db)
    except HTTPException:
        return redirect("/login")


def deployment_query_for(user: User):
    stmt = select(Deployment).options(selectinload(Deployment.owner), selectinload(Deployment.node)).order_by(Deployment.created_at.desc())
    owner_ids = visible_owner_ids(user)
    if owner_ids is not None:
        stmt = stmt.where(Deployment.owner_id.in_(owner_ids))
    return stmt


def deployment_for_action(db: Session, user: User, deployment_id: int) -> Deployment:
    deployment = db.scalar(
        select(Deployment)
        .options(selectinload(Deployment.owner), selectinload(Deployment.node))
        .where(Deployment.id == deployment_id)
    )
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    owner_ids = visible_owner_ids(user)
    if owner_ids is not None and deployment.owner_id not in owner_ids:
        raise HTTPException(status_code=403, detail="You cannot manage this deployment")
    return deployment


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value[:60] or "plex"


def allocate_port(db: Session, node: Node) -> int:
    used = set(db.scalars(select(Deployment.host_port).where(Deployment.node_id == node.id)).all())
    for port in range(node.port_start, node.port_end + 1):
        if port not in used:
            return port
    raise RuntimeError(f"No free Plex ports remain on {node.name}")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("login.html", {"request": request, "app_name": settings.app_name, "error": request.query_params.get("error")})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return redirect("/login", error="Invalid username or password")
    request.session.clear()
    request.session["user_id"] = user.id
    return redirect("/")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_user_or_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    deployments = db.scalars(deployment_query_for(user)).all()
    counts = {
        "total": len(deployments),
        "running": sum(1 for item in deployments if item.status == DeploymentStatus.RUNNING),
        "stopped": sum(1 for item in deployments if item.status == DeploymentStatus.STOPPED),
        "error": sum(1 for item in deployments if item.status == DeploymentStatus.ERROR),
    }
    return templates.TemplateResponse("dashboard.html", page_context(request, user, deployments=deployments[:8], counts=counts))


@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request, db: Session = Depends(get_db)):
    user = get_user_or_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role == UserRole.USER:
        return redirect("/", error="Access denied")
    if user.role == UserRole.ADMIN:
        users = db.scalars(select(User).options(selectinload(User.parent)).order_by(User.created_at.desc())).all()
    else:
        users = db.scalars(select(User).where(User.parent_id == user.id).order_by(User.created_at.desc())).all()
    return templates.TemplateResponse("users.html", page_context(request, user, users=users, roles=UserRole))


@app.post("/users/new")
def create_user(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    deployment_limit: int = Form(1),
    parent_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    actor = get_user_or_login(request, db)
    if isinstance(actor, RedirectResponse):
        return actor
    if actor.role == UserRole.USER:
        return redirect("/", error="Access denied")
    try:
        requested_role = UserRole(role)
    except ValueError:
        return redirect("/users", error="Invalid account role")
    if actor.role == UserRole.RESELLER:
        requested_role = UserRole.USER
        parent_id = actor.id
    elif requested_role == UserRole.USER and parent_id:
        parent = db.get(User, parent_id)
        if not parent or parent.role != UserRole.RESELLER:
            return redirect("/users", error="Parent must be a reseller")
    else:
        parent_id = None
    if len(password) < 8:
        return redirect("/users", error="Password must be at least 8 characters")
    db.add(
        User(
            username=username.strip(),
            email=email.strip().lower(),
            password_hash=hash_password(password),
            role=requested_role,
            parent_id=parent_id,
            deployment_limit=max(0, deployment_limit),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return redirect("/users", error="Username or email already exists")
    return redirect("/users", message="Account created")


@app.post("/users/{user_id}/toggle")
def toggle_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    actor = get_user_or_login(request, db)
    if isinstance(actor, RedirectResponse):
        return actor
    target = db.get(User, user_id)
    if not target or not can_manage_user(actor, target) or target.id == actor.id:
        return redirect("/users", error="You cannot modify that account")
    target.is_active = not target.is_active
    db.commit()
    return redirect("/users", message="Account updated")


@app.get("/nodes", response_class=HTMLResponse)
def nodes_page(request: Request, db: Session = Depends(get_db)):
    user = get_user_or_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != UserRole.ADMIN:
        return redirect("/", error="Administrator access required")
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    return templates.TemplateResponse("nodes.html", page_context(request, user, nodes=nodes))


@app.post("/nodes/new")
def create_node(
    request: Request,
    name: str = Form(...),
    host: str = Form(...),
    docker_url: str = Form("unix:///var/run/docker.sock"),
    base_path: str = Form(...),
    template_path: str = Form(...),
    media_mounts_json: str = Form("[]"),
    port_start: int = Form(32401),
    port_end: int = Form(32999),
    enable_hardware: bool = Form(False),
    db: Session = Depends(get_db),
):
    user = get_user_or_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    require_admin(user)
    try:
        json.loads(media_mounts_json)
        if port_end < port_start:
            raise ValueError("Port end must be greater than or equal to port start")
        node = Node(
            name=name.strip(),
            host=host.strip(),
            docker_url=docker_url.strip(),
            base_path=base_path.strip(),
            template_path=template_path.strip(),
            media_mounts_json=media_mounts_json.strip(),
            port_start=port_start,
            port_end=port_end,
            enable_hardware=enable_hardware,
        )
        DockerService(node).ping()
        db.add(node)
        db.commit()
    except Exception as exc:
        db.rollback()
        return redirect("/nodes", error=str(exc))
    return redirect("/nodes", message="Node added and Docker connection verified")


@app.get("/deployments", response_class=HTMLResponse)
def deployments_page(request: Request, db: Session = Depends(get_db)):
    user = get_user_or_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    deployments = db.scalars(deployment_query_for(user)).all()
    return templates.TemplateResponse("deployments.html", page_context(request, user, deployments=deployments))


@app.get("/deployments/new", response_class=HTMLResponse)
def new_deployment_page(request: Request, db: Session = Depends(get_db)):
    user = get_user_or_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    nodes = db.scalars(select(Node).where(Node.is_active.is_(True)).order_by(Node.name)).all()
    if user.role == UserRole.ADMIN:
        owners = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.username)).all()
    elif user.role == UserRole.RESELLER:
        owners = [user, *db.scalars(select(User).where(User.parent_id == user.id, User.is_active.is_(True))).all()]
    else:
        owners = [user]
    return templates.TemplateResponse("new_deployment.html", page_context(request, user, nodes=nodes, owners=owners))


@app.post("/deployments/new")
def create_deployment(
    request: Request,
    name: str = Form(...),
    node_id: int = Form(...),
    owner_id: int = Form(...),
    claim_token: str = Form(...),
    db: Session = Depends(get_db),
):
    actor = get_user_or_login(request, db)
    if isinstance(actor, RedirectResponse):
        return actor
    node = db.get(Node, node_id)
    owner = db.get(User, owner_id)
    if not node or not node.is_active or not owner or not owner.is_active:
        return redirect("/deployments/new", error="Invalid node or owner")
    allowed_owners = visible_owner_ids(actor)
    if allowed_owners is not None and owner.id not in allowed_owners:
        return redirect("/deployments/new", error="You cannot create a deployment for that owner")
    current_count = db.scalar(select(func.count(Deployment.id)).where(Deployment.owner_id == owner.id)) or 0
    if current_count >= owner.deployment_limit:
        return redirect("/deployments/new", error="Deployment limit reached for this account")
    base_slug = slugify(name)
    slug = base_slug
    suffix = 1
    while db.scalar(select(Deployment.id).where(Deployment.slug == slug)):
        suffix += 1
        slug = f"{base_slug}-{suffix}"
    host_port = allocate_port(db, node)
    deployment_root = Path(node.base_path) / "deployments" / slug
    deployment = Deployment(
        owner_id=owner.id,
        created_by_id=actor.id,
        node_id=node.id,
        name=name.strip(),
        slug=slug,
        container_name=f"galaxy-plex-{slug}",
        host_port=host_port,
        config_path=str(deployment_root / "config"),
        status=DeploymentStatus.CLONING,
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)
    try:
        clone_template(node.template_path, deployment.config_path)
        deployment.status = DeploymentStatus.STARTING
        db.commit()
        claimed = DockerService(node).claim_and_scrub(deployment, claim_token)
        deployment.claimed = claimed
        deployment.status = DeploymentStatus.RUNNING if claimed else DeploymentStatus.CLAIM_REQUIRED
        deployment.last_error = None if claimed else "Plex did not accept the claim token before timeout. Use Retry Claim with a new token."
        db.commit()
    except Exception as exc:
        deployment.status = DeploymentStatus.ERROR
        deployment.last_error = str(exc)
        db.commit()
        return redirect("/deployments", error=f"Deployment created but setup failed: {exc}")
    return redirect("/deployments", message="Plex clone created and claimed by its owner")


@app.post("/deployments/{deployment_id}/action/{action}")
def deployment_action(deployment_id: int, action: str, request: Request, db: Session = Depends(get_db)):
    user = get_user_or_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    deployment = deployment_for_action(db, user, deployment_id)
    service = DockerService(deployment.node)
    try:
        if action == "start":
            service.start(deployment)
            deployment.status = DeploymentStatus.RUNNING
        elif action == "stop":
            service.stop(deployment)
            deployment.status = DeploymentStatus.STOPPED
        elif action == "restart":
            service.restart(deployment)
            deployment.status = DeploymentStatus.RUNNING
        elif action == "rebuild":
            service.create_or_replace(deployment)
            deployment.status = DeploymentStatus.RUNNING
        else:
            raise RuntimeError("Unknown deployment action")
        deployment.last_error = None
        db.commit()
    except Exception as exc:
        deployment.status = DeploymentStatus.ERROR
        deployment.last_error = str(exc)
        db.commit()
        return redirect("/deployments", error=str(exc))
    return redirect("/deployments", message=f"{deployment.name}: {action} complete")


@app.post("/deployments/{deployment_id}/claim")
def retry_claim(deployment_id: int, request: Request, claim_token: str = Form(...), db: Session = Depends(get_db)):
    user = get_user_or_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    deployment = deployment_for_action(db, user, deployment_id)
    try:
        claimed = DockerService(deployment.node).claim_and_scrub(deployment, claim_token)
        deployment.claimed = claimed
        deployment.status = DeploymentStatus.RUNNING if claimed else DeploymentStatus.CLAIM_REQUIRED
        deployment.last_error = None if claimed else "Claim failed. Generate a fresh token and retry."
        db.commit()
    except Exception as exc:
        deployment.status = DeploymentStatus.ERROR
        deployment.last_error = str(exc)
        db.commit()
        return redirect("/deployments", error=str(exc))
    return redirect("/deployments", message="Plex claim completed" if claimed else "Plex claim was not accepted")


@app.post("/deployments/{deployment_id}/delete")
def delete_deployment(
    deployment_id: int,
    request: Request,
    purge_config: bool = Form(False),
    db: Session = Depends(get_db),
):
    user = get_user_or_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    deployment = deployment_for_action(db, user, deployment_id)
    try:
        DockerService(deployment.node).remove_container(deployment, remove_network=True)
        if purge_config:
            safe_remove_config(str(Path(deployment.config_path).parent), str(Path(deployment.node.base_path) / "deployments"))
        db.delete(deployment)
        db.commit()
    except Exception as exc:
        db.rollback()
        return redirect("/deployments", error=str(exc))
    return redirect("/deployments", message="Deployment deleted")


@app.get("/deployments/{deployment_id}/logs", response_class=HTMLResponse)
def deployment_logs(deployment_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_user_or_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    deployment = deployment_for_action(db, user, deployment_id)
    logs = DockerService(deployment.node).logs(deployment)
    return templates.TemplateResponse("logs.html", page_context(request, user, deployment=deployment, logs=logs))
