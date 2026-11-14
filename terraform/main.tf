data "google_project" "this" {
  project_id = var.project_id
}

locals {
  # run.app URLs are deterministic, so the app knows its public URL (report links, Twilio signatures)
  # without a dependency cycle.
  run_url = "https://${var.service_name}-${data.google_project.this.number}.${var.region}.run.app"
  # Report links and Twilio signature checks use the custom domain once it is set.
  public_url = var.custom_domain != "" ? "https://${var.custom_domain}" : local.run_url
  labels     = { product = "farmpartner", env = var.env }

  # Secret Manager secrets the service reads. has_value = Terraform writes a version from a variable;
  # otherwise the existing version is kept (e.g. the database URL stored by scripts/set-db-secret.sh).
  secret_values = {
    "farmpartner-admin-token"        = random_password.admin_token.result
    "farmpartner-database-url"       = var.database_url
    "farmpartner-anthropic-api-key"  = var.anthropic_api_key
    "farmpartner-twilio-account-sid" = var.twilio_account_sid
    "farmpartner-twilio-auth-token"  = var.twilio_auth_token
  }
  secrets = {
    "farmpartner-admin-token"        = { env = "ADMIN_TOKEN", has_value = true, mount = true }
    "farmpartner-database-url"       = { env = "DATABASE_URL", has_value = nonsensitive(var.database_url != ""), mount = true }
    "farmpartner-anthropic-api-key"  = { env = "ANTHROPIC_API_KEY", has_value = nonsensitive(var.anthropic_api_key != ""), mount = nonsensitive(var.anthropic_api_key != "") }
    "farmpartner-twilio-account-sid" = { env = "TWILIO_ACCOUNT_SID", has_value = nonsensitive(var.twilio_account_sid != ""), mount = nonsensitive(var.twilio_account_sid != "") }
    "farmpartner-twilio-auth-token"  = { env = "TWILIO_AUTH_TOKEN", has_value = nonsensitive(var.twilio_auth_token != ""), mount = nonsensitive(var.twilio_auth_token != "") }
  }
  mounted   = { for k, v in local.secrets : k => v if v.mount }
  versioned = toset([for k, v in local.secrets : k if v.has_value])

  plain_env = merge({
    PUBLIC_BASE_URL      = local.public_url
    ANTHROPIC_EFFORT     = "low"
    TWILIO_WHATSAPP_FROM = var.twilio_whatsapp_from
    TWILIO_SMS_FROM      = var.twilio_sms_from
    WHATSAPP_PREFILL     = var.whatsapp_prefill
    CACHE_BUCKET         = google_storage_bucket.cache.name
    SCHEDULER_SA_EMAIL   = "farmpartner-scheduler@${var.project_id}.iam.gserviceaccount.com"
    SCHEDULER_AUDIENCE   = "${local.run_url}/internal/daily"
    }, { for k, v in {
      ANTHROPIC_FEDERATION_RULE_ID = var.anthropic_federation_rule_id
      ANTHROPIC_ORGANIZATION_ID    = var.anthropic_organization_id
      ANTHROPIC_SERVICE_ACCOUNT_ID = var.anthropic_service_account_id
      ANTHROPIC_WORKSPACE_ID       = var.anthropic_workspace_id
  } : k => v if v != "" })
}

# --- Runtime identity --------------------------------------------------------------------------------
# The project's default compute service account is disabled, so the service runs as its own account with
# no project-wide roles: it can only read its own secrets.
resource "google_service_account" "run" {
  project      = var.project_id
  account_id   = "farmpartner-run"
  display_name = "FarmPartner Cloud Run SA"
}

# --- Secrets -----------------------------------------------------------------------------------------
resource "random_password" "admin_token" {
  length  = 32
  special = false
}

resource "google_secret_manager_secret" "app" {
  for_each  = local.secrets
  project   = var.project_id
  secret_id = each.key
  labels    = local.labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "app" {
  for_each    = local.versioned
  secret      = google_secret_manager_secret.app[each.key].id
  secret_data = local.secret_values[each.key]
}

resource "google_secret_manager_secret_iam_member" "run_access" {
  for_each  = local.secrets
  project   = var.project_id
  secret_id = google_secret_manager_secret.app[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run.email}"
}

# --- Cloud Run ---------------------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "app" {
  name                = var.service_name
  project             = var.project_id
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL" # Twilio webhooks and report links must reach it
  deletion_protection = false
  labels              = local.labels

  template {
    service_account                  = google_service_account.run.email
    timeout                          = "300s"
    max_instance_request_concurrency = 40
    labels                           = local.labels

    scaling {
      min_instance_count = 0 # costs nothing while idle
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        # CPU stays allocated after the response so background NASA fetches and Twilio pushes finish.
        cpu_idle          = false
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = local.plain_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.mounted
        content {
          name = env.value.env
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app[env.key].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [client, client_version]
  }

  depends_on = [
    google_storage_bucket_iam_member.cache_rw,
    google_secret_manager_secret_iam_member.run_access,
    google_secret_manager_secret_version.app,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
