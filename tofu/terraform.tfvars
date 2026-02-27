services = {
  "argo-rust" = {
    namespace = "argo-rust"
    replicas  = 2
    # image is set here only for first apply; CI updates it via ArgoCD after that
    image     = "ghcr.io/voloshko/argo-rust:latest"
    port      = 8080
    node_port = 30800

    probes = {
      path                    = "/hello"
      liveness_initial_delay  = 5
      liveness_period         = 10
      readiness_initial_delay = 3
      readiness_period        = 5
    }

    resources = {
      requests = { cpu = "50m",  memory = "32Mi" }
      limits   = { cpu = "200m", memory = "64Mi" }
    }

    image_pull_secret   = "ghcr-secret"
    spread_across_nodes = true
  }
}
