variable "project_id" {
  type    = string
  default = "launchpad-all-dev-01"
}

variable "region" {
  type    = string
  default = "europe-west1"
}

variable "env" {
  type    = string
  default = "dev"
}

variable "service_name" {
  type    = string
  default = "farmpartner-run-dev-01"
}

variable "artifact_repository" {
  description = "Existing shared Docker repository in the project."
  type        = string
  default     = "launchpad-all-docker-ar-dev-01"
}

variable "image" {
  description = "Full image reference to deploy. scripts/deploy.sh builds, pushes and passes it."
  type        = string
}

variable "max_instances" {
  description = "Keep at 1: the web simulator's /api/poll outbox lives in process memory."
  type        = number
  default     = 1
}

# --- Secrets -------------------------------------------------------------------------------------------
# Leave a value empty to keep whatever version already exists in Secret Manager (e.g. the database URL
# stored by scripts/set-db-secret.sh, so the Supabase password never enters Terraform state).

variable "database_url" {
  description = "Supabase session-pooler URI. Empty = keep the existing secret version."
  type        = string
  default     = ""
  sensitive   = true
}

variable "anthropic_api_key" {
  description = "Claude API key. Empty = not mounted; the bot falls back to keyword matching."
  type        = string
  default     = ""
  sensitive   = true
}

variable "twilio_account_sid" {
  type      = string
  default   = ""
  sensitive = true
}

variable "twilio_auth_token" {
  type      = string
  default   = ""
  sensitive = true
}

variable "twilio_whatsapp_from" {
  type    = string
  default = "whatsapp:+14155238886" # Twilio WhatsApp sandbox
}

variable "twilio_sms_from" {
  type    = string
  default = ""
}

# --- Supabase project (optional) ---------------------------------------------------------------------
# The database already exists and holds the app's data. Setting manage_supabase_project = true imports it
# into state with prevent_destroy, so Terraform can describe it but never recreate or delete it.

variable "manage_supabase_project" {
  type    = bool
  default = false
}

variable "supabase_project_ref" {
  type    = string
  default = "pjrwkpuiudqmqlmoshkl"
}

variable "supabase_access_token" {
  description = "Supabase personal access token (sbp_...). Only needed when manage_supabase_project = true."
  type        = string
  default     = ""
  sensitive   = true
}

variable "supabase_organization_id" {
  type    = string
  default = ""
}

variable "supabase_db_password" {
  description = "Required by the provider schema; ignored after import (lifecycle.ignore_changes)."
  type        = string
  default     = "unused-after-import"
  sensitive   = true
}

variable "supabase_region" {
  type    = string
  default = "eu-west-1"
}

# --- Claude via Workload Identity Federation (no API key) ----------------------------------------------
# IDs from Claude Console → Settings → Workload identity, rule for serviceAccount farmpartner-run.
# They are identifiers, not secrets. Leave empty to fall back to anthropic_api_key (or keyword matching).

variable "anthropic_federation_rule_id" {
  type    = string
  default = ""
}

variable "anthropic_organization_id" {
  type    = string
  default = ""
}

variable "anthropic_service_account_id" {
  type    = string
  default = ""
}

variable "anthropic_workspace_id" {
  type    = string
  default = ""
}
