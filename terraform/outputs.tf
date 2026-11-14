output "url" {
  value = google_cloud_run_v2_service.app.uri
}

output "public_base_url" {
  value = local.public_url
}

output "simulator" {
  value = "${local.public_url}/chat"
}

output "landing" {
  value = "${local.public_url}/"
}

output "twilio_webhook" {
  value = "${local.public_url}/twilio/webhook"
}

output "service_account" {
  value = google_service_account.run.email
}

output "run_url" {
  value = local.run_url
}

output "dns_records" {
  description = "Records to create at the DNS provider for each mapped domain."
  value = {
    for d, m in google_cloud_run_domain_mapping.app : d => [
      for r in try(m.status[0].resource_records, []) : { type = r.type, host = r.name == "" ? "@" : r.name, value = r.rrdata }
    ]
  }
}
