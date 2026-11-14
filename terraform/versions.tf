terraform {
  required_version = ">= 1.7.0" # import blocks with for_each

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    supabase = {
      source  = "supabase/supabase"
      version = "~> 1.11"
    }
  }

  backend "gcs" {
    bucket = "tf-state-polynode-root-01"
    prefix = "dev/launchpad-all-dev-01/farmpartner"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Only called when manage_supabase_project = true; the placeholder just satisfies provider configuration.
provider "supabase" {
  access_token = var.supabase_access_token != "" ? var.supabase_access_token : "sbp_unset"
}
