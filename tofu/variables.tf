variable "services" {
  description = "Sizing and configuration for each microservice."

  type = map(object({
    namespace = string
    replicas  = number
    image     = string
    port      = number
    node_port = number

    probes = object({
      path                  = string
      liveness_initial_delay  = number
      liveness_period         = number
      readiness_initial_delay = number
      readiness_period        = number
    })

    resources = object({
      requests = object({ cpu = string, memory = string })
      limits   = object({ cpu = string, memory = string })
    })

    image_pull_secret    = optional(string, "ghcr-secret")
    spread_across_nodes  = optional(bool, false)
  }))
}
