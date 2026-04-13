data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

locals {
  app_insights_name   = "appi-${var.project_name}-prod"
  service_plan_name   = "asp-${var.project_name}-prod"
  key_vault_name      = substr("${var.key_vault_name}${random_string.suffix.result}", 0, 24)
  cosmos_account_name = substr("${var.cosmos_account_name}${random_string.suffix.result}", 0, 44)
  github_subject      = "repo:${var.github_owner}/${var.github_repo}:environment:${var.github_environment}"
  deployment_app_name = "gh-${var.project_name}-deploy"
  auth_app_name       = "Mano's Tasks Management"
  auth_callback_url   = "https://${var.web_app_name}.azurewebsites.net/.auth/login/aad/callback"
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_service_plan" "this" {
  name                = local.service_plan_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  os_type             = "Linux"
  sku_name            = var.app_service_plan_sku
}

resource "azurerm_application_insights" "this" {
  name                = local.app_insights_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  application_type    = "web"
}

resource "azurerm_key_vault" "this" {
  name                       = local.key_vault_name
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = false
  soft_delete_retention_days = 7
}

resource "azurerm_cosmosdb_account" "this" {
  name                = local.cosmos_account_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.this.location
    failover_priority = 0
  }

  capabilities {
    name = "EnableServerless"
  }
}

resource "azurerm_cosmosdb_sql_database" "this" {
  name                = var.cosmos_database_name
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
}

resource "azurerm_cosmosdb_sql_container" "this" {
  name                = var.cosmos_container_name
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  database_name       = azurerm_cosmosdb_sql_database.this.name
  partition_key_paths = ["/id"]
}

resource "azurerm_key_vault_secret" "cosmos_uri" {
  name         = "cosmos-db-uri"
  value        = azurerm_cosmosdb_account.this.endpoint
  key_vault_id = azurerm_key_vault.this.id
}

resource "azurerm_key_vault_secret" "cosmos_key" {
  name         = "cosmos-db-key"
  value        = azurerm_cosmosdb_account.this.primary_key
  key_vault_id = azurerm_key_vault.this.id
}

resource "azurerm_linux_web_app" "this" {
  name                = var.web_app_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  service_plan_id     = azurerm_service_plan.this.id
  https_only          = true

  identity {
    type = "SystemAssigned"
  }

  auth_settings_v2 {
    auth_enabled           = true
    default_provider       = "azureactivedirectory"
    excluded_paths         = ["/health"]
    require_authentication = true
    require_https          = true
    unauthenticated_action = "RedirectToLoginPage"

    active_directory_v2 {
      client_id                  = azuread_application.web_auth.client_id
      client_secret_setting_name = "MICROSOFT_PROVIDER_AUTHENTICATION_SECRET"
      tenant_auth_endpoint       = "https://login.microsoftonline.com/${var.tenant_id}/v2.0"
      allowed_applications       = [azuread_application.web_auth.client_id]
      allowed_audiences = [
        azuread_application.web_auth.client_id,
        "api://${azuread_application.web_auth.client_id}",
      ]
    }

    login {
      token_store_enabled = true
    }
  }

  site_config {
    always_on = false

    application_stack {
      python_version = "3.12"
    }
  }

  app_settings = {
    APPLICATIONINSIGHTS_CONNECTION_STRING    = azurerm_application_insights.this.connection_string
    ALLOWED_TENANT_ID                        = var.tenant_id
    AUTH_REQUIRED                            = "true"
    COSMOS_DB_CONTAINER                      = azurerm_cosmosdb_sql_container.this.name
    COSMOS_DB_DATABASE                       = azurerm_cosmosdb_sql_database.this.name
    COSMOS_DB_KEY                            = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.cosmos_key.versionless_id})"
    COSMOS_DB_URI                            = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.cosmos_uri.versionless_id})"
    ENABLE_ORYX_BUILD                        = "true"
    MICROSOFT_PROVIDER_AUTHENTICATION_SECRET = azuread_application_password.web_auth.value
    REQUIRED_APP_ROLE                        = var.required_app_role
    SCM_DO_BUILD_DURING_DEPLOYMENT           = "true"
  }
}

resource "azurerm_key_vault_access_policy" "web_app" {
  key_vault_id = azurerm_key_vault.this.id
  tenant_id    = azurerm_linux_web_app.this.identity[0].tenant_id
  object_id    = azurerm_linux_web_app.this.identity[0].principal_id

  secret_permissions = [
    "Get",
    "List"
  ]
}

resource "azurerm_key_vault_access_policy" "current_user" {
  key_vault_id = azurerm_key_vault.this.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = data.azurerm_client_config.current.object_id

  secret_permissions = [
    "Delete",
    "Get",
    "List",
    "Purge",
    "Recover",
    "Set"
  ]
}

resource "azuread_application" "github_deploy" {
  display_name = local.deployment_app_name
}

resource "random_uuid" "task_user_role_id" {}

resource "azuread_application" "web_auth" {
  display_name     = local.auth_app_name
  sign_in_audience = "AzureADMyOrg"

  web {
    homepage_url  = "https://${var.web_app_name}.azurewebsites.net"
    redirect_uris = [local.auth_callback_url]

    implicit_grant {
      access_token_issuance_enabled = false
      id_token_issuance_enabled     = true
    }
  }

  app_role {
    allowed_member_types = ["User"]
    description          = "Grants access to Mano's Tasks Management."
    display_name         = var.required_app_role
    enabled              = true
    id                   = random_uuid.task_user_role_id.result
    value                = var.required_app_role
  }

  optional_claims {
    id_token {
      name = "roles"
    }
    access_token {
      name = "roles"
    }
  }
}

resource "azuread_application_password" "web_auth" {
  application_id = azuread_application.web_auth.id
  display_name   = "app-service-easy-auth"
}

resource "azuread_service_principal" "web_auth" {
  client_id                    = azuread_application.web_auth.client_id
  app_role_assignment_required = true
}

resource "azuread_service_principal" "github_deploy" {
  client_id = azuread_application.github_deploy.client_id
}

resource "azuread_application_federated_identity_credential" "github_actions" {
  application_id = azuread_application.github_deploy.id
  display_name   = "github-${var.github_owner}-${var.github_repo}-${var.github_branch}"
  description    = "Allows GitHub Actions to deploy ${var.github_repo} to Azure."
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://token.actions.githubusercontent.com"
  subject        = local.github_subject
}

resource "azurerm_role_assignment" "github_deploy_rg" {
  scope                = azurerm_resource_group.this.id
  role_definition_name = "Contributor"
  principal_id         = azuread_service_principal.github_deploy.object_id
}
