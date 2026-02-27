terraform {
  required_version = ">= 1.8"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.35"
    }
  }
}

provider "kubernetes" {
  config_path    = pathexpand("~/.kube/config")
  config_context = "microk8s"
}

# ── Namespaces ───────────────────────────────────────────────────────────────
resource "kubernetes_namespace_v1" "service" {
  for_each = var.services

  metadata {
    name = each.value.namespace
  }
}
