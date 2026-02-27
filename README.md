# argo-rust

A Rust REST API deployed via a full GitOps loop: GitHub Actions builds and pushes a Docker image to ghcr.io, updates the Kubernetes manifest in-repo, and ArgoCD detects the change and deploys it to MicroK8s automatically.

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/hello` | Returns a greeting JSON |
| GET | `/fibonacci/{n}` | Returns the nth Fibonacci number |

```
$ curl http://192.168.1.187:30800/hello
{"message":"Hello from argo-rust!"}

$ curl http://192.168.1.187:30800/fibonacci/10
{"n":10,"result":55}
```

## Stack

- **Runtime**: axum 0.8 + tokio (async Rust HTTP server)
- **Image registry**: ghcr.io/voloshko/argo-rust
- **Kubernetes**: MicroK8s, namespace `argo-rust`, NodePort 30800
- **GitOps**: ArgoCD with automated sync

## Repository layout

```
argo-rust/
├── src/main.rs                   # axum routes
├── Cargo.toml                    # dependencies + release profile
├── Cargo.lock                    # committed (binary crate)
├── Dockerfile                    # multi-stage build
├── .github/workflows/deploy.yml  # CI/CD pipeline
├── k8s/
│   ├── namespace.yaml
│   ├── deployment.yaml           # image tag updated by CI on every push
│   └── service.yaml
└── tofu/
    ├── main.tf                   # kubernetes provider
    ├── variables.tf              # services type definition
    ├── microservices.tf          # Deployment, Service, Ingress resources
    ├── outputs.tf
    └── terraform.tfvars          # per-service sizing + hostnames
```

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

ArgoCD is running in the `argocd` namespace on the same MicroK8s cluster.
UI: `https://192.168.1.187:32505`

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

---

## Gateway and load balancing

Traffic enters from Cloudflare and is distributed across two physical nodes (**k8plus** and **dell**) via MetalLB + nginx Ingress.

```
Internet
    │  HTTPS
    ▼
Cloudflare (rust.voloshko.org)
    │  Cloudflare Tunnel (cloudflared on k8plus)
    ▼
MetalLB VIP  192.168.1.200:80
    │  L2 advertisement on LAN — either node can own the VIP
    ▼
nginx Ingress Controller (DaemonSet — one pod per node)
    │  routes Host: rust.voloshko.org → argo-rust Service
    ▼
argo-rust ClusterIP Service
    │  kube-proxy round-robins across healthy endpoints
    ├──▶ pod on k8plus  (192.168.1.171)
    └──▶ pod on dell    (192.168.1.187)
```

### Components

| Component | Kind | Namespace | Notes |
|-----------|------|-----------|-------|
| MetalLB controller + speaker | DaemonSet | `metallb-system` | Installed via `microk8s enable metallb:192.168.1.200-192.168.1.210` |
| `default-addresspool` | IPAddressPool | `metallb-system` | Range 192.168.1.200–192.168.1.210 |
| `ingress-lb` | Service (LoadBalancer) | `ingress` | MetalLB VIP 192.168.1.200, targets nginx DaemonSet |
| nginx Ingress controller | DaemonSet | `ingress` | Installed via `microk8s enable ingress`; runs on every node |
| `argo-rust` Ingress | Ingress | `argo-rust` | `ingressClassName: public`, routes rust.voloshko.org → service:80; managed by OpenTofu |
| argo-rust pods | Deployment (2 replicas) | `argo-rust` | Pod anti-affinity ensures one pod per node |

### MetalLB — how VIP failover works

MetalLB uses L2 mode: one node "owns" the VIP at any moment and responds to ARP requests for 192.168.1.200. If that node goes down, MetalLB re-announces the VIP from another node within a few seconds. Because the nginx Ingress DaemonSet runs on every node, the new VIP owner already has a healthy ingress pod.

### Ingress resource

Ingress resources are managed by OpenTofu (not ArgoCD). Add a `hostname` field to a service entry in `tofu/terraform.tfvars` and run `tofu apply` — OpenTofu creates (or updates) the Ingress automatically:

```hcl
"argo-rust" = {
  hostname  = "rust.voloshko.org"   # ← nginx Ingress created automatically
  # ... other fields
}
```

The generated Ingress uses `ingressClassName: public` and routes all paths (`/`) to the service on port 80. It also carries an `argocd.argoproj.io/sync-options: Prune=false` annotation so ArgoCD ignores it.

### Pod spreading

OpenTofu sets `replicas: 2` and a `podAntiAffinity` rule with `topologyKey: kubernetes.io/hostname`. This tells the scheduler to prefer placing pods on different nodes. The rule is `preferredDuringScheduling` so the deployment still starts on a single-node cluster.

### Cloudflare Tunnel — origin update

The tunnel uses token mode (configured in the Cloudflare Zero Trust dashboard). After adding MetalLB, update the public hostname origin:

1. Go to [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → **Networks → Tunnels**
2. Find the tunnel running on k8plus → **Edit**
3. On the **Public Hostnames** tab, find `rust.voloshko.org`
4. Change the **Service** (origin) from `http://192.168.1.187:30800` to `http://192.168.1.200`
5. Save — traffic now routes through the MetalLB VIP

The NodePort 30800 remains available for direct LAN access.

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

Everything in `k8s/` is managed by ArgoCD and applied automatically on every push to master. Ingress resources are managed separately by OpenTofu (see the OpenTofu section below).

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

## OpenTofu — microservice sizing

Sizing (CPU/memory requests + limits, replica count, NodePort) is declared in `tofu/terraform.tfvars` and applied directly to the cluster via the Kubernetes provider. ArgoCD is configured with `ignoreDifferences` for those fields so the two tools don't fight.

### Install OpenTofu

```bash
VERSION=1.11.5
curl -fsSL "https://github.com/opentofu/opentofu/releases/download/v${VERSION}/tofu_${VERSION}_linux_amd64.tar.gz" \
  | tar -xz -C ~/bin tofu
```

### Workflow

```bash
cd tofu
tofu init          # first time only — downloads kubernetes provider
tofu plan          # preview changes
tofu apply         # apply sizing to the cluster
tofu output        # show current sizing and endpoints
```

### Changing sizing

Edit `tofu/terraform.tfvars`, then run `tofu apply`:

```hcl
services = {
  "argo-rust" = {
    replicas = 2                                         # scale out
    resources = {
      requests = { cpu = "100m", memory = "64Mi" }       # bump requests
      limits   = { cpu = "500m", memory = "128Mi" }      # bump limits
    }
    # ... other fields unchanged
  }
}
```

### Adding a new microservice

Add a new entry to the `services` map in `terraform.tfvars`:

```hcl
  "my-service" = {
    namespace = "my-service"
    hostname  = "api.voloshko.org"    # optional — omit if no public hostname needed
    replicas  = 1
    image     = "ghcr.io/voloshko/my-service:latest"
    port      = 8080
    node_port = 30801
    probes    = { path = "/health", liveness_initial_delay = 5, liveness_period = 10,
                  readiness_initial_delay = 3, readiness_period = 5 }
    resources = {
      requests = { cpu = "50m",  memory = "32Mi" }
      limits   = { cpu = "200m", memory = "64Mi" }
    }
    image_pull_secret = "ghcr-secret"
  }
```

Then create its pull secret and apply:

```bash
microk8s kubectl create secret docker-registry ghcr-secret \
  --namespace=my-service --docker-server=ghcr.io \
  --docker-username=voloshko --docker-password=$(gh auth token)
tofu apply
```

When `hostname` is set, `tofu apply` creates a `kubernetes_ingress_v1` that routes `api.voloshko.org` through the nginx Ingress (MetalLB VIP 192.168.1.200) to the service on port 80. The `tofu output` command will show `https://api.voloshko.org` as the endpoint.

To expose the hostname publicly, add a **Public Hostname** entry in the Cloudflare Zero Trust tunnel config:

| Hostname | Service (origin) |
|----------|-----------------|
| `api.voloshko.org` | `http://192.168.1.200` |

No NodePort or DNS changes needed — nginx routes by the `Host` header.

### Ownership split

| What | Owner | How |
|------|-------|-----|
| Image tag | CI (GitHub Actions) | `sed` + git commit → ArgoCD sync |
| Replicas | OpenTofu | `terraform.tfvars` + `tofu apply` |
| CPU / memory | OpenTofu | `terraform.tfvars` + `tofu apply` |
| Namespace, Service | OpenTofu | managed resources |
| Ingress (public hostname) | OpenTofu | `hostname` field in `terraform.tfvars` + `tofu apply` |

The ArgoCD Application has `ignoreDifferences` on `/spec/replicas` and `/spec/template/spec/containers/0/resources` so it never reverts what OpenTofu sets. Tofu-managed Ingress resources carry `argocd.argoproj.io/sync-options: Prune=false` so ArgoCD never deletes them.

### State file

`terraform.tfstate` is excluded from git (`.gitignore`) because it can contain sensitive data. It lives only on the machine where `tofu apply` is run. For team use, configure a remote backend (e.g., S3, GCS, or Terraform Cloud) in `main.tf`.

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

# Hit the live endpoint
curl http://192.168.1.187:30800/hello
curl http://192.168.1.187:30800/fibonacci/42
```
