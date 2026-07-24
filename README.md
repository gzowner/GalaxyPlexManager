# GalaxyPlexManager

GalaxyPlexManager is a role-based Docker control panel that clones a prepared Plex master template into isolated, customer-owned Plex Media Server containers.

## Current MVP

- **Admin** controls the site, nodes, accounts, and every Plex deployment.
- **Reseller** can create normal users and manage the reseller's own deployments plus deployments owned by direct child users.
- **User** can create and manage only their own Plex deployments.
- Every account has a configurable deployment limit.
- New Plex servers are cloned from a master configuration snapshot.
- Master Plex identity and account fields are removed from each clone.
- A free host port is allocated automatically from the selected node's range.
- The customer supplies a fresh `claim-...` token from their own Plex account.
- The claim token is never stored in PostgreSQL. After Plex accepts it, the container is recreated without the token in its Docker environment.
- Owners can start, stop, restart, rebuild, view logs, retry claiming, or delete deployments they are authorized to manage.

## Important deployment model

```text
Master Plex template
├── Customer 1 Plex (own identity, account, port, config)
├── Customer 2 Plex (own identity, account, port, config)
└── Customer 3 Plex (own identity, account, port, config)
```

Media is shared read-only. Each clone has an independent Plex database and metadata directory after cloning.

## Requirements

- Ubuntu 24.04 recommended
- Docker Engine with the Compose plugin
- A prepared Plex master config snapshot
- Media mounted on the Docker host
- Local SSD/NVMe space for each customer's Plex config and metadata

## Install

After the MVP branch is merged to `main`:

```bash
curl -fsSL https://raw.githubusercontent.com/gzowner/GalaxyPlexManager/main/scripts/install.sh | sudo bash
```

For testing the active development branch:

```bash
BRANCH=agent/initial-platform bash <(curl -fsSL https://raw.githubusercontent.com/gzowner/GalaxyPlexManager/agent/initial-platform/scripts/install.sh)
```

The installer writes the generated admin password to:

```text
/root/galaxyplexmanager-admin-password.txt
```

The panel defaults to:

```text
http://SERVER-IP:8080
```

## Prepare the master template

The source Plex server should be stopped or snapshotted before capture. The master should contain the desired libraries, metadata, posters, collections, and settings, but no customer activity.

```bash
sudo apt-get install -y rsync sqlite3
sudo bash /opt/GalaxyPlexManager/scripts/capture_master.sh \
  /path/to/master/plex/config \
  /opt/galaxyplexmanager/templates/master \
  master-plex-container-name
```

The third argument is optional. When provided, the capture script stops the container, copies the config consistently, checks the Plex SQLite database, and starts the master again.

## Media mount configuration

Nodes use JSON to define host-to-container media paths. The **container paths must match the paths stored in the master Plex database**.

```json
[
  {
    "host": "/mnt/unionfs/Media/Movies",
    "container": "/data/Movies",
    "read_only": true
  },
  {
    "host": "/mnt/unionfs/Media/TV",
    "container": "/data/TV Shows",
    "read_only": true
  }
]
```

## Role behavior

| Role | Accounts | Nodes | Deployment visibility | Create Plex |
|---|---|---|---|---|
| Admin | Admin, reseller, user | Full | All | For any account |
| Reseller | Direct users | None | Own + direct users | For self or direct users |
| User | None | None | Own only | For self |

## Claim workflow

1. The customer signs in to their Plex account and generates a fresh claim token.
2. The user creates a deployment and pastes the token into GalaxyPlexManager.
3. The manager clones the master template and removes master identity/account attributes.
4. Plex starts with the customer token.
5. GalaxyPlexManager watches the clone's `Preferences.xml` for successful ownership.
6. The Plex container is recreated without `PLEX_CLAIM`, removing the short-lived token from Docker inspection.

## Safety boundaries in this MVP

- The Docker socket is mounted only into the trusted manager backend.
- Users never receive Docker API access.
- Every action is authorized against deployment ownership.
- Customer media mounts default to read-only.
- Config deletion is constrained to the node's deployment directory.
- Remote Docker nodes are not fully supported yet because safe remote file cloning requires a dedicated node agent.

## Next milestones

1. Background job queue with live cloning progress.
2. Dedicated authenticated node agent for remote servers.
3. Template versions and master capture from the web panel.
4. Node health, CPU, RAM, disk, GPU, and stream statistics.
5. Scheduled backups and restore points.
6. Move deployments between nodes.
7. Optional CPU/RAM/transcode limits per reseller and user.

## Development

```bash
cp .env.example .env
docker compose up -d --build
```

Health check:

```bash
curl http://127.0.0.1:8080/health
```
