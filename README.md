# argo-rust

A Rust REST API deployed via a full GitOps loop: GitHub Actions builds and pushes a Docker image to ghcr.io, updates the Kubernetes manifest in-repo, and ArgoCD detects the change and deploys it to MicroK8s automatically.

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/hello` | Returns a greeting JSON |
| GET | `/fibonacci/{n}` | Returns the nth Fibonacci number |

```
$ curl http://192.168.1.171:30800/hello
{"message":"Hello from argo-rust!"}

$ curl http://192.168.1.242:30800/fibonacci/10
{"n":10,"result":55}
```

## Stack

- **Runtime**: axum 0.8 + tokio (async Rust HTTP server)
- **Image registry**: ghcr.io/voloshko/argo-rust
- **Kubernetes**: MicroK8s (2-node cluster: k8plus `192.168.1.171`, dell `192.168.1.242`)
- **Namespace**: `argo-rust`, NodePort 30800
- **GitOps**: ArgoCD with automated sync and HA (2 replicas)
- **Ingress**: MetalLB VIP `192.168.1.200` + nginx Ingress Controller

## Repository layout

```
argo-rust/
├── src/main.rs                   # axum routes
├── Cargo.toml                    # dependencies + release profile
├── Cargo.lock                    # committed (binary crate)
├── Dockerfile                    # multi-stage build
├── .github/workflows/deploy.yml  # CI/CD pipeline
└── k8s/
    ├── namespace.yaml
    ├── deployment.yaml           # image tag updated by CI on every push
    └── service.yaml
```

**Infrastructure management** (sizing, ingress, replicas) is handled by the central **k8s-infra** repository.

See: https://github.com/voloshko/k8s-infra

---

## Codex code review

**File**: `.github/workflows/codex-review.yml`
**Trigger**: pull requests opened, updated, or reopened against `master`, `main`, or `develop`

The workflow runs a static analysis pass over every PR and posts the findings as a comment, then sets a commit status (`codex-review`) so the result is visible in the PR checks bar.

### How it works

```
PR opened / updated
        │
        ▼
1. Checkout with full history (fetch-depth: 0)
        │
        ▼
2. Get changed files
   git diff origin/<base>...<sha>
   filter: .rs .go .js .ts .py .java .cpp .c .cs .rb .php
        │
        ▼
3. Build diff → pr_diff.txt
        │
        ▼
4. Run .github/scripts/codex-review.py
   --diff pr_diff.txt --format json --output codex-review.json
        │
        ▼
5. Post / update PR comment  ── "🔍 Codex Code Review"
        │
        ▼
6. Set commit status
   success / failure / warning  (based on issue severity)
```

### Permissions

```yaml
permissions:
  pull-requests: write   # post and update the review comment
  contents: read         # checkout and read the diff
```

No extra secrets needed — the workflow uses the built-in `GITHUB_TOKEN`.

### Review script — `.github/scripts/codex-review.py`

A self-contained Python 3 script with no dependencies. It parses the unified diff, walks added lines (`+` prefix) only, and runs regex patterns per language.

**Rust checks**

| Category | Pattern | Message |
|----------|---------|---------|
| best_practices | `unwrap()` | Use proper error handling instead of unwrap() |
| best_practices | `println!` | Prefer logging over println! for production code |
| best_practices | `Vec::new().push(` | Use Vec::with_capacity() when size is known |
| best_practices | `.clone()` | Avoid unnecessary clones, consider references |
| bugs | `panic!` | Avoid panic! in production code, use Result<T,E> |
| bugs | `expect(` | Replace expect() with proper error handling |
| bugs | `unsafe ` | Unsafe block detected — ensure it's necessary and safe |
| security | `println!(…password` | Don't log passwords or sensitive data |
| security | `dbg!(…password` | Don't include passwords in debug output |

**Go checks** cover `fmt.Print*`, `defer` without error handling, and password leaks in formatted strings.

### Comment format

The bot posts a markdown table grouped by severity and updates the same comment on subsequent pushes (searches for an existing comment from `type=Bot` containing `🔍 Codex Code Review`).

```
## 🔍 Codex Code Review

Repository: voloshko/argo-rust
PR: #N
Commit: `sha`

### 📋 Review Summary

| Severity | Category      | File        | Line | Issue                              |
|----------|---------------|-------------|------|------------------------------------|
| warning  | best_practices| src/main.rs | 12   | Consider using proper error handling |
```

### Commit status

After the review, a commit status named `codex-review` is written:

| Issues found | Status | Description |
|---|---|---|
| Any `error` severity | `failure` | Critical issues found |
| Any `warning`, no errors | `warning` | Warnings found |
| None | `success` | No issues found |

### Customising checks with AGENTS.md

Place an `AGENTS.md` at the repo root (or deeper for per-package rules) with a `## Review guidelines` section:

```markdown
## Review guidelines

- Do not log PII or tokens at any log level.
- Every HTTP handler must be wrapped by the auth middleware.
- Flag all bare unwrap() calls in src/ as errors, not warnings.
```

Codex reads the `AGENTS.md` closest to each changed file. More-specific files deeper in the tree override the root one for their subtree.

---

## GitHub Actions pipeline

**File**: `.github/workflows/deploy.yml`
**Trigger**: every push to `master`

### Permissions

```yaml
permissions:
  contents: write   # to commit the updated deployment.yaml back to the repo
  packages: write   # to push the image to ghcr.io
```

No extra secrets are needed. Authentication to ghcr.io uses the built-in `GITHUB_TOKEN`.

### Steps

```
push to master
    │
    ▼
1. Checkout — actions/checkout@v4
    │
    ▼
2. Login to ghcr.io — docker/login-action@v3
   username: github.actor
   password: secrets.GITHUB_TOKEN
    │
    ▼
3. Set up Docker Buildx — docker/setup-buildx-action@v3
    │
    ▼
4. Extract metadata — docker/metadata-action@v5
   tag strategy: sha-<7-char commit SHA>   e.g. sha-b920bd3
    │
    ▼
5. Build & push — docker/build-push-action@v6
   cache-from/to: GitHub Actions cache (type=gha)
   tags:  ghcr.io/voloshko/argo-rust:sha-<SHA>
    │
    ▼
6. Update k8s/deployment.yaml
   sed replaces the image tag line with the new sha- tag
    │
    ▼
7. Commit & push manifest
   committer: github-actions[bot]
   message:   "ci: update image to sha-<SHA>"
   (skipped if deployment.yaml is unchanged)
```

### Docker build — dependency-cache layer trick

The Dockerfile uses a two-pass build inside the builder stage so that Cargo dependencies are cached as a separate Docker layer and only rebuilt when `Cargo.toml` or `Cargo.lock` change:

```dockerfile
# Stage 1: Build
FROM rust:1-slim AS builder
WORKDIR /app

# Pass 1 — compile a stub binary to warm the dependency cache
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs && \
    cargo build --release && \
    rm -rf src

# Pass 2 — compile the real source (dependencies already compiled)
COPY src ./src
RUN touch src/main.rs && cargo build --release

# Stage 2: Runtime — minimal Debian image, no Rust toolchain
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y ca-certificates && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /app/target/release/argo-rust .
EXPOSE 8080
CMD ["./argo-rust"]
```

`debian:bookworm-slim` is used instead of Alpine to avoid musl libc complexity.

### Release profile

```toml
[profile.release]
opt-level = "z"   # optimise for size
lto = true        # link-time optimisation
strip = true      # strip debug symbols from binary
```

---

## ArgoCD configuration

ArgoCD is running in the `argocd` namespace on the same MicroK8s cluster with **high availability** (2 replicas spread across both nodes).

| Access method | URL |
|---------------|-----|
| Public (Cloudflare) | `https://argo.voloshko.org` |
| LAN NodePort (k8plus) | `http://192.168.1.171:32505` |
| LAN NodePort (dell) | `http://192.168.1.242:32505` |

> **Note**: Local access is HTTP (not HTTPS) because the server runs in insecure mode. Use `https://argo.voloshko.org` for production access.

### Cluster nodes

| Node | LAN IP | Role | ArgoCD Server Pod |
|------|--------|------|-------------------|
| k8plus | `192.168.1.171` | Control plane + worker | ✅ Running |
| dell | `192.168.1.242` | Worker | ✅ Running |

Both nodes use direct LAN connectivity (no VPN) to avoid VXLAN MTU issues.

### Public access via nginx Ingress

ArgoCD is exposed through the same MetalLB VIP + nginx Ingress used by other services. The server runs in **insecure mode** (HTTP only) so nginx can proxy it without TLS re-encryption; Cloudflare provides the public HTTPS termination.

The full set of user-supplied Helm values (insecure mode + NodePort preservation):

```yaml
configs:
  params:
    server.insecure: true
server:
  service:
    type: NodePort
    nodePortHttp: 32505
    nodePortHttps: 31340
```

Applied with (or re-applied after any chart upgrade):

```bash
microk8s helm3 upgrade argo-cd argo-cd \
  --repo https://argoproj.github.io/argo-helm \
  --namespace argocd \
  --reuse-values \
  -f - <<'EOF'
configs:
  params:
    server.insecure: true
server:
  service:
    type: NodePort
    nodePortHttp: 32505
    nodePortHttps: 31340
EOF
```

The Ingress is created once and not managed by ArgoCD or k8s-infra (ArgoCD can't manage its own Ingress; k8s-infra's `services` map targets app deployments, not cluster infrastructure):

```bash
microk8s kubectl apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: argocd-server
  namespace: argocd
  annotations:
    nginx.ingress.kubernetes.io/proxy-read-timeout: "300"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "300"
    nginx.ingress.kubernetes.io/proxy-buffer-size: "128k"
    argocd.argoproj.io/sync-options: "Prune=false"
spec:
  ingressClassName: public
  rules:
    - host: argo.voloshko.org
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: argo-cd-argocd-server
                port:
                  number: 80
EOF
```

The 300 s proxy timeouts accommodate slow ArgoCD sync operations. `Prune=false` prevents ArgoCD from deleting its own Ingress.

The Cloudflare tunnel has a public hostname entry: `argo.voloshko.org` → `http://192.168.1.200`.

### Application manifest

Applied once with `microk8s kubectl apply -f -`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: argo-rust
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/voloshko/argo-rust.git
    targetRevision: master
    path: k8s                 # ArgoCD watches only the k8s/ directory
  destination:
    server: https://kubernetes.default.svc
    namespace: argo-rust
  syncPolicy:
    automated:
      prune: true             # delete resources removed from git
      selfHeal: true          # revert manual changes in the cluster
    syncOptions:
      - CreateNamespace=true  # creates argo-rust namespace if missing
```

### How the GitOps loop works

```
developer pushes code
        │
        ▼
GitHub Actions builds image
        │  tags: ghcr.io/voloshko/argo-rust:sha-XXXXXXX
        ▼
CI commits updated k8s/deployment.yaml to master
        │  image: ghcr.io/voloshko/argo-rust:sha-XXXXXXX
        ▼
ArgoCD polls GitHub every ~3 minutes, detects diff
        │
        ▼
ArgoCD applies the changed manifest to MicroK8s
        │
        ▼
Kubernetes pulls new image from ghcr.io, rolls out new pod
        │  imagePullSecrets: ghcr-secret
        ▼
Liveness + readiness probes on GET /hello confirm health
```

ArgoCD never needs write access to the cluster beyond the `argo-rust` namespace because the Application CR points to `https://kubernetes.default.svc` (in-cluster).

### Sync policy details

| Setting | Value | Effect |
|---------|-------|--------|
| `automated` | enabled | ArgoCD syncs without manual approval |
| `prune` | true | Resources deleted from `k8s/` are also deleted from the cluster |
| `selfHeal` | true | Manual `kubectl` edits are reverted on next sync cycle |
| Poll interval | ~3 min | Default ArgoCD polling frequency |

### Forcing an immediate sync

```bash
# Annotate the Application to trigger an out-of-band refresh
microk8s kubectl annotate application argo-rust \
  -n argocd argocd.argoproj.io/refresh=normal

# Or via argocd CLI
argocd app sync argo-rust
```

### High availability configuration

ArgoCD server runs with 2 replicas using pod anti-affinity to ensure pods are spread across both nodes:

```yaml
server:
  replicas: 2
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        - labelSelector:
            matchLabels:
              app.kubernetes.io/name: argocd-server
          topologyKey: kubernetes.io/hostname
```

The deployment was patched to remove the default `nodeSelector` that was pinning pods to a single node:

```bash
# Remove nodeSelector to allow scheduling on any node
microk8s kubectl patch deployment -n argocd argo-cd-argocd-server \
  -p '{"spec":{"template":{"spec":{"nodeSelector":null}}}}'
```

### Storage architecture

ArgoCD does **not** use persistent volumes. All state is either:

| Component | Storage | Behavior |
|-----------|---------|----------|
| argocd-server | `emptyDir` | Stateless - serves UI/API only |
| repo-server | `emptyDir` | Clones git on startup - rebuildable |
| application-controller | None | Reconciles from git + k8s API |
| redis | `emptyDir` | Cache only - rebuilt on restart |

**No PVCs are required** - the source of truth is the git repository and the Kubernetes cluster itself. If a pod dies, it restarts and resyncs from git.

### Troubleshooting

#### Browser shows "Connection refused" but incognito works

This is a browser HSTS cache issue. The browser remembers an old HTTPS redirect.

**Fix**: Clear HSTS data for the IP/domain
```
Chrome/Edge: chrome://net-internals/#hsts → Delete domain security policies
Firefox: Clear Site Data from address bar lock icon
```

Or use the public HTTPS URL: `https://argo.voloshko.org`

#### ArgoCD pods stuck on one node

If pods don't spread across nodes, check for leftover `nodeSelector`:

```bash
# Check for node selector
microk8s kubectl get deployment -n argocd argo-cd-argocd-server \
  -o jsonpath='{.spec.template.spec.nodeSelector}'

# Remove it if present
microk8s kubectl patch deployment -n argocd argo-cd-argocd-server \
  -p '{"spec":{"template":{"spec":{"nodeSelector":null}}}}'

# Delete pods to reschedule
microk8s kubectl delete pod -n argocd -l app.kubernetes.io/name=argocd-server
```

#### Node IPs showing VPN addresses instead of LAN

If `kubectl get nodes -o wide` shows VPN IPs (e.g., `100.124.x.x`) instead of LAN IPs:

```bash
# On each node, stop MicroK8s and add LAN IP to kubelet
sudo microk8s stop
echo "--node-ip=<LAN_IP>" | sudo tee -a /var/snap/microk8s/current/args/kubelet
sudo microk8s start
```

Replace `<LAN_IP>` with the actual LAN IP (e.g., `192.168.1.171` for k8plus).

---

## Gateway and load balancing

Traffic enters from Cloudflare and is distributed across two physical nodes (**k8plus** and **dell**) via MetalLB + nginx Ingress.

```
Internet
    │  HTTPS
    ▼
Cloudflare (argo.voloshko.org, rust.voloshko.org)
    │  Cloudflare Tunnel (cloudflared on k8plus)
    ▼
MetalLB VIP  192.168.1.200:80
    │  L2 advertisement on LAN — either node can own the VIP
    ▼
nginx Ingress Controller (DaemonSet — one pod per node)
    │  routes by Host header → respective Service
    ▼
┌─────────────────────────────────────────────────────────────┐
│  ArgoCD: argo-cd-argocd-server (2 replicas, HA)             │
│  App:    argo-rust (2 replicas, pod anti-affinity)          │
└─────────────────────────────────────────────────────────────┘
    │
    ├──▶ pod on k8plus  (192.168.1.171)
    └──▶ pod on dell    (192.168.1.242)
```

**Important**: Both nodes use direct LAN connectivity. VPN (Tailscale/WireGuard) has been disabled to avoid VXLAN MTU issues.

### Components

| Component | Kind | Namespace | Notes |
|-----------|------|-----------|-------|
| MetalLB controller + speaker | DaemonSet | `metallb-system` | Installed via `microk8s enable metallb:192.168.1.200-192.168.1.210` |
| `default-addresspool` | IPAddressPool | `metallb-system` | Range 192.168.1.200–192.168.1.210 |
| `ingress-lb` | Service (LoadBalancer) | `ingress` | MetalLB VIP 192.168.1.200, targets nginx DaemonSet |
| nginx Ingress controller | DaemonSet | `ingress` | Installed via `microk8s enable ingress`; runs on every node |
| `argo-rust` Ingress | Ingress | `argo-rust` | `ingressClassName: public`, routes rust.voloshko.org → service:80; managed by k8s-infra |
| argo-rust pods | Deployment (2 replicas) | `argo-rust` | Pod anti-affinity ensures one pod per node |

### MetalLB — how VIP failover works

MetalLB uses L2 mode: one node "owns" the VIP at any moment and responds to ARP requests for 192.168.1.200. If that node goes down, MetalLB re-announces the VIP from another node within a few seconds. Because the nginx Ingress DaemonSet runs on every node, the new VIP owner already has a healthy ingress pod.

### Ingress resource

Ingress resources are managed by k8s-infra (not ArgoCD). Add a `hostname` field to a service entry in `k8s-infra/terraform.tfvars` and run `tofu apply` — k8s-infra creates (or updates) the Ingress automatically:

```hcl
"argo-rust" = {
  hostname  = "rust.voloshko.org"   # ← nginx Ingress created automatically
  # ... other fields
}
```

The generated Ingress uses `ingressClassName: public` and routes all paths (`/`) to the service on port 80. It also carries an `argocd.argoproj.io/sync-options: Prune=false` annotation so ArgoCD ignores it.

### Pod spreading

k8s-infra sets `replicas: 2` and a `podAntiAffinity` rule with `topologyKey: kubernetes.io/hostname`. This tells the scheduler to prefer placing pods on different nodes. The rule is `preferredDuringScheduling` so the deployment still starts on a single-node cluster.

### Cloudflare Tunnel — origin update

The tunnel uses token mode (configured in the Cloudflare Zero Trust dashboard). After adding MetalLB, update the public hostname origins:

1. Go to [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → **Networks → Tunnels**
2. Find the tunnel running on k8plus → **Edit**
3. On the **Public Hostnames** tab, configure:

| Hostname | Service (origin) |
|----------|------------------|
| `argo.voloshko.org` | `http://192.168.1.200` |
| `rust.voloshko.org` | `http://192.168.1.200` |

4. Save — traffic now routes through the MetalLB VIP

The nginx Ingress controller routes traffic to the correct service based on the `Host` header. NodePorts remain available for direct LAN access if needed.

### Infrastructure setup (one-time)

These commands are run once on the k8plus node and are **not** managed by ArgoCD:

```bash
# 1. Install MetalLB with LAN IP pool
microk8s enable metallb:192.168.1.200-192.168.1.210

# 2. Install nginx Ingress (DaemonSet on every node)
microk8s enable ingress

# 3. Create LoadBalancer Service so MetalLB assigns a VIP to the ingress
microk8s kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Service
metadata:
  name: ingress-lb
  namespace: ingress
  annotations:
    metallb.universe.tf/loadBalancerIPs: 192.168.1.200
spec:
  type: LoadBalancer
  selector:
    name: nginx-ingress-microk8s
  externalTrafficPolicy: Local
  ports:
    - { name: http,  port: 80,  targetPort: 80  }
    - { name: https, port: 443, targetPort: 443 }
EOF
```

Everything in `k8s/` is managed by ArgoCD and applied automatically on every push to master. Ingress resources are managed separately by k8s-infra (see the Infrastructure Management section).

---

## Kubernetes resources

### Namespace

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: argo-rust
```

### Deployment

```yaml
resources:
  requests:
    cpu: 50m
    memory: 32Mi
  limits:
    cpu: 200m
    memory: 64Mi

livenessProbe:
  httpGet: { path: /hello, port: 8080 }
  initialDelaySeconds: 5
  periodSeconds: 10

readinessProbe:
  httpGet: { path: /hello, port: 8080 }
  initialDelaySeconds: 3
  periodSeconds: 5
```

The pod uses `imagePullSecrets: [ghcr-secret]` to authenticate against ghcr.io.

### Service

```yaml
type: NodePort
port: 80 → targetPort: 8080 → nodePort: 30800
```

### Image pull secret

Created once per cluster:

```bash
microk8s kubectl create secret docker-registry ghcr-secret \
  --namespace=argo-rust \
  --docker-server=ghcr.io \
  --docker-username=voloshko \
  --docker-password=$(gh auth token) \
  --docker-email=voloshko@users.noreply.github.com
```

The password is a GitHub personal access token (or `gh auth token`) with `read:packages` scope. It is stored only in the cluster secret, not in the repo.

---

## Infrastructure Management

**Sizing, replicas, ingress, and NodePort configuration** are managed in the central **k8s-infra** repository:

📁 **Repository**: https://github.com/voloshko/k8s-infra

### What's Managed Where

| Resource | Managed By | Location |
|----------|------------|----------|
| Image tag | CI (GitHub Actions) | `k8s/deployment.yaml` (this repo) |
| Replicas | k8s-infra (OpenTofu) | `k8s-infra/terraform.tfvars` |
| CPU/memory | k8s-infra (OpenTofu) | `k8s-infra/terraform.tfvars` |
| Namespace | k8s-infra (OpenTofu) | `k8s-infra/terraform.tfvars` |
| Service | k8s-infra (OpenTofu) | `k8s-infra/terraform.tfvars` |
| Ingress | k8s-infra (OpenTofu) | `k8s-infra/terraform.tfvars` |

### Changing Sizing

To scale the service or change resource limits:

```bash
cd ~/projects/k8s-infra
vim terraform.tfvars    # Edit replicas, resources, etc.
tofu apply
```

### Adding a New Service

See the [k8s-infra README](https://github.com/voloshko/k8s-infra) for complete documentation on adding new microservices.

---

## Running locally

```bash
cargo build --release
./target/release/argo-rust &
curl localhost:8080/hello
curl localhost:8080/fibonacci/10
kill %1
```

```bash
docker build -t argo-rust:local .
docker run --rm -p 8080:8080 argo-rust:local
```

## Verifying a deployed rollout

```bash
# Watch the rollout
microk8s kubectl rollout status deployment/argo-rust -n argo-rust

# Check the running image tag
microk8s kubectl get deployment argo-rust -n argo-rust \
  -o jsonpath='{.spec.template.spec.containers[0].image}'

# Hit the live endpoint (via VIP, dell, or k8plus)
curl http://192.168.1.200/hello
curl http://192.168.1.242:30800/fibonacci/42
curl http://192.168.1.171:30800/hello

# Verify ArgoCD access
curl -I https://argo.voloshko.org/
```
