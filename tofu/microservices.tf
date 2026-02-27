# ── Deployments ──────────────────────────────────────────────────────────────
resource "kubernetes_deployment_v1" "service" {
  for_each = var.services

  depends_on = [kubernetes_namespace_v1.service]

  metadata {
    name      = each.key
    namespace = each.value.namespace
    labels    = { app = each.key }
    annotations = {
      "tofu/managed-fields" = "replicas,resources"
    }
  }

  spec {
    replicas = each.value.replicas

    selector {
      match_labels = { app = each.key }
    }

    template {
      metadata {
        labels = { app = each.key }
      }

      spec {
        dynamic "image_pull_secrets" {
          for_each = each.value.image_pull_secret != null ? [1] : []
          content {
            name = each.value.image_pull_secret
          }
        }

        container {
          name  = each.key
          image = each.value.image

          port {
            container_port = each.value.port
          }

          resources {
            requests = {
              cpu    = each.value.resources.requests.cpu
              memory = each.value.resources.requests.memory
            }
            limits = {
              cpu    = each.value.resources.limits.cpu
              memory = each.value.resources.limits.memory
            }
          }

          liveness_probe {
            http_get {
              path = each.value.probes.path
              port = each.value.port
            }
            initial_delay_seconds = each.value.probes.liveness_initial_delay
            period_seconds        = each.value.probes.liveness_period
          }

          readiness_probe {
            http_get {
              path = each.value.probes.path
              port = each.value.port
            }
            initial_delay_seconds = each.value.probes.readiness_initial_delay
            period_seconds        = each.value.probes.readiness_period
          }
        }
      }
    }
  }

  # CI owns the image tag — ArgoCD updates it via git commit.
  # OpenTofu owns everything else (replicas, resource sizing).
  lifecycle {
    ignore_changes = [
      spec[0].template[0].spec[0].container[0].image,
    ]
  }
}

# ── Services (NodePort) ───────────────────────────────────────────────────────
resource "kubernetes_service_v1" "service" {
  for_each = var.services

  depends_on = [kubernetes_namespace_v1.service]

  metadata {
    name      = each.key
    namespace = each.value.namespace
  }

  spec {
    type     = "NodePort"
    selector = { app = each.key }

    port {
      port        = 80
      target_port = each.value.port
      node_port   = each.value.node_port
    }
  }
}

# ── Image pull secrets ────────────────────────────────────────────────────────
# The ghcr-secret is created once out-of-band and NOT managed by OpenTofu.
# Reading it via a data source would expose the token in terraform.tfstate.
# Create it manually with:
#
#   microk8s kubectl create secret docker-registry ghcr-secret \
#     --namespace=<ns> --docker-server=ghcr.io \
#     --docker-username=voloshko --docker-password=$(gh auth token)
