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
  github_subject      = "repo:${var.github_owner}/${var.github_repo}:ref:refs/heads/${var.github_branch}"
  deployment_app_name = "gh-${var.project_name}-deploy"
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

  access_policy {
    tenant_id = data.azurerm_client_config.current.tenant_id
    object_id = data.azurerm_client_config.current.object_id

    secret_permissions = [
      "Delete",
      "Get",
      "List",
      "Purge",
      "Recover",
      "Set"
    ]
  }
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

  site_config {
    always_on        = false
    app_command_line = "gunicorn --bind=0.0.0.0 --timeout 600 app:app"

    application_stack {
      python_version = "3.12"
    }
  }

  app_settings = {
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.this.connection_string
    COSMOS_DB_CONTAINER                   = azurerm_cosmosdb_sql_container.this.name
    COSMOS_DB_DATABASE                    = azurerm_cosmosdb_sql_database.this.name
    COSMOS_DB_KEY                         = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.cosmos_key.versionless_id})"
    COSMOS_DB_URI                         = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.cosmos_uri.versionless_id})"
    SCM_DO_BUILD_DURING_DEPLOYMENT        = "true"
    WEBSITE_RUN_FROM_PACKAGE              = "1"
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

resource "azuread_application" "github_deploy" {
  display_name = local.deployment_app_name
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
