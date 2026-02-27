output "services" {
  description = "Deployed microservices and their current sizing."
  value = {
    for k, v in var.services : k => {
      namespace = v.namespace
      replicas  = v.replicas
      endpoint  = "http://192.168.1.187:${v.node_port}"
      resources = v.resources
    }
  }
}
