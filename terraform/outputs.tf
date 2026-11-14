output "url" {
  value = google_cloud_run_v2_service.app.uri
}

output "public_base_url" {
  value = local.public_url
}

output "simulator" {
  value = "${local.public_url}/"
}

output "twilio_webhook" {
  value = "${local.public_url}/twilio/webhook"
}

output "service_account" {
  value = google_service_account.run.email
}
