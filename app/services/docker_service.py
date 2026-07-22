from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import docker
    from docker.errors import APIError, ImageNotFound, NotFound
except ImportError:  # Allows non-Docker health and auth tests.
    docker = None

    class DockerSDKError(Exception):
        pass

    APIError = ImageNotFound = NotFound = DockerSDKError

from ..config import get_settings
from ..models import Deployment, Node
from .plex_clone import plex_is_claimed


@dataclass
class MediaMount:
    host: str
    container: str
    read_only: bool = True


def parse_media_mounts(node: Node) -> list[MediaMount]:
    try:
        raw = json.loads(node.media_mounts_json or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid media mount JSON on node {node.name}: {exc}") from exc
    mounts: list[MediaMount] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("host") or not item.get("container"):
            raise RuntimeError("Each media mount needs host and container paths")
        mounts.append(MediaMount(str(item["host"]), str(item["container"]), bool(item.get("read_only", True))))
    return mounts


class DockerService:
    def __init__(self, node: Node):
        if docker is None:
            raise RuntimeError("Docker SDK is not installed")
        self.node = node
        self.settings = get_settings()
        self.client = docker.DockerClient(base_url=node.docker_url, timeout=120)

    def ping(self) -> bool:
        return bool(self.client.ping())

    def ensure_image(self) -> None:
        try:
            self.client.images.get(self.settings.plex_image)
        except ImageNotFound:
            self.client.images.pull(self.settings.plex_image)

    def create_or_replace(self, deployment: Deployment, claim_token: str | None = None) -> None:
        self.ensure_image()
        self.remove_container(deployment, remove_network=False)
        network_name = self._network_name(deployment)
        try:
            network = self.client.networks.get(network_name)
        except NotFound:
            network = self.client.networks.create(network_name, driver="bridge", labels=self._labels(deployment))

        volumes: dict[str, dict[str, str]] = {
            deployment.config_path: {"bind": "/config", "mode": "rw"},
            str(Path(deployment.config_path).parent / "transcode"): {"bind": "/transcode", "mode": "rw"},
        }
        Path(deployment.config_path).mkdir(parents=True, exist_ok=True)
        Path(deployment.config_path).parent.joinpath("transcode").mkdir(parents=True, exist_ok=True)
        for mount in parse_media_mounts(self.node):
            volumes[mount.host] = {"bind": mount.container, "mode": "ro" if mount.read_only else "rw"}

        environment = {
            "TZ": self.settings.tz,
            "PLEX_UID": str(self.settings.plex_uid),
            "PLEX_GID": str(self.settings.plex_gid),
            "CHANGE_CONFIG_DIR_OWNERSHIP": "false",
            "ADVERTISE_IP": f"http://{self.node.host}:{deployment.host_port}/",
        }
        if claim_token:
            environment["PLEX_CLAIM"] = claim_token.strip()

        devices = []
        if self.node.enable_hardware and os.path.exists("/dev/dri"):
            devices.append("/dev/dri:/dev/dri:rwm")

        container = self.client.containers.run(
            self.settings.plex_image,
            name=deployment.container_name,
            hostname=deployment.container_name,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            ports={"32400/tcp": deployment.host_port},
            environment=environment,
            volumes=volumes,
            devices=devices,
            network=network.name,
            labels=self._labels(deployment),
        )
        container.reload()

    def start(self, deployment: Deployment) -> None:
        self.client.containers.get(deployment.container_name).start()

    def stop(self, deployment: Deployment) -> None:
        self.client.containers.get(deployment.container_name).stop(timeout=30)

    def restart(self, deployment: Deployment) -> None:
        self.client.containers.get(deployment.container_name).restart(timeout=30)

    def status(self, deployment: Deployment) -> str:
        try:
            container = self.client.containers.get(deployment.container_name)
        except NotFound:
            return "missing"
        container.reload()
        return str(container.status)

    def logs(self, deployment: Deployment, tail: int = 200) -> str:
        try:
            return self.client.containers.get(deployment.container_name).logs(tail=tail).decode("utf-8", "replace")
        except NotFound:
            return "Container does not exist."

    def remove_container(self, deployment: Deployment, remove_network: bool = True) -> None:
        try:
            container = self.client.containers.get(deployment.container_name)
            container.remove(force=True)
        except NotFound:
            pass
        if remove_network:
            try:
                self.client.networks.get(self._network_name(deployment)).remove()
            except (NotFound, APIError):
                pass

    def claim_and_scrub(self, deployment: Deployment, claim_token: str, timeout: int = 180) -> bool:
        if not claim_token.strip().startswith("claim-"):
            raise RuntimeError("Plex claim tokens must begin with claim-")
        self.create_or_replace(deployment, claim_token=claim_token)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if plex_is_claimed(deployment.config_path):
                self.create_or_replace(deployment, claim_token=None)
                return True
            time.sleep(3)
        self.create_or_replace(deployment, claim_token=None)
        return False

    @staticmethod
    def _network_name(deployment: Deployment) -> str:
        return f"{deployment.container_name}-net"

    @staticmethod
    def _labels(deployment: Deployment) -> dict[str, str]:
        return {
            "io.galaxyplexmanager.managed": "true",
            "io.galaxyplexmanager.deployment_id": str(deployment.id),
            "io.galaxyplexmanager.owner_id": str(deployment.owner_id),
        }
