# Daily job: frost/heat/irrigation alerts + refresh of week-old NASA field snapshots.
# Cloud Scheduler signs each call with a Google OIDC token for its own service account; the app verifies the
# token's audience and email (app/main.py _authorized_job), so no shared secret lives in the job config.
locals {
  daily_url = "${local.run_url}/internal/daily" # run.app URL: works independently of the custom domain
}

resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = "farmpartner-scheduler"
  display_name = "FarmPartner Cloud Scheduler (daily alerts + refresh)"
}

resource "google_cloud_scheduler_job" "daily" {
  project          = var.project_id
  region           = var.region
  name             = "farmpartner-daily-${var.env}-01"
  description      = "FarmPartner: frost/heat/irrigation alerts and weekly refresh of field data"
  schedule         = var.daily_schedule
  time_zone        = "America/Chicago"
  attempt_deadline = "320s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = local.daily_url

    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = local.daily_url
    }
  }

  depends_on = [google_cloud_run_v2_service.app]
}
