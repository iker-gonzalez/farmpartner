# Durable raw-data cache (NASA POWER, MODIS, CropScape, SSURGO, forecasts). Cloud Run's /tmp is memory and is
# lost on every restart, so the app keeps a local copy as L1 and this bucket as the shared, durable L2.
# Only the service account can read or write it; nothing is public.
resource "google_storage_bucket" "cache" {
  name                        = "farmpartner-cache-${var.env}-01"
  project                     = var.project_id
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true # a cache: everything in it can be re-downloaded
  labels                      = local.labels

  # Short-lived entries (forecasts, NASA POWER windows) live under ttl/ and are cleaned up automatically.
  lifecycle_rule {
    condition {
      age            = 30
      matches_prefix = ["ttl/"]
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "cache_rw" {
  bucket = google_storage_bucket.cache.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.run.email}"
}
