# Everything below was first created by hand (gcloud, Supabase dashboard). Terraform adopts it instead of
# recreating it, so the running service and the database keep their data. Safe to leave in place: an import
# block for an object already in state is a no-op.

import {
  to = google_service_account.run
  id = "projects/${var.project_id}/serviceAccounts/farmpartner-run@${var.project_id}.iam.gserviceaccount.com"
}

import {
  for_each = toset(["farmpartner-admin-token", "farmpartner-database-url"])
  to       = google_secret_manager_secret.app[each.key]
  id       = "projects/${var.project_id}/secrets/${each.key}"
}

import {
  for_each = toset(["farmpartner-admin-token", "farmpartner-database-url"])
  to       = google_secret_manager_secret_iam_member.run_access[each.key]
  id       = "projects/${var.project_id}/secrets/${each.key} roles/secretmanager.secretAccessor serviceAccount:farmpartner-run@${var.project_id}.iam.gserviceaccount.com"
}

import {
  to = google_cloud_run_v2_service.app
  id = "projects/${var.project_id}/locations/${var.region}/services/${var.service_name}"
}

import {
  to = google_cloud_run_v2_service_iam_member.public
  id = "projects/${var.project_id}/locations/${var.region}/services/${var.service_name} roles/run.invoker allUsers"
}

import {
  for_each = var.manage_supabase_project ? toset([var.supabase_project_ref]) : toset([])
  to       = supabase_project.db[0]
  id       = each.key
}
