# The Supabase project already exists and holds FarmPartner's data (farmer, field, observation, ...).
# When manage_supabase_project = true it is imported, never created: prevent_destroy blocks any plan that
# would delete it, and the password is ignored because the API can't read it back.
# The app enables row-level security on its tables at startup (app/db.py), which closes Supabase's public
# Data API; the app itself connects as the table owner through the session pooler.
resource "supabase_project" "db" {
  count             = var.manage_supabase_project ? 1 : 0
  organization_id   = var.supabase_organization_id
  name              = "farmpartner-db-${var.env}-01"
  database_password = var.supabase_db_password
  region            = var.supabase_region

  lifecycle {
    ignore_changes  = [database_password, name, organization_id, region, instance_size]
    prevent_destroy = true
  }
}
