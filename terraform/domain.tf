# Cloud Run domain mappings: Google provisions and renews the TLS certificate once the DNS records in
# output "dns_records" point at Google. DNS for farmpartner.space lives at the registrar (Namecheap).
locals {
  domains = var.custom_domain == "" ? [] : concat([var.custom_domain], var.custom_domain_www ? ["www.${var.custom_domain}"] : [])
}

resource "google_cloud_run_domain_mapping" "app" {
  for_each = toset(local.domains)
  project  = var.project_id
  location = var.region
  name     = each.key

  metadata {
    namespace = var.project_id
    labels    = local.labels
  }

  spec {
    route_name = google_cloud_run_v2_service.app.name
  }
}
