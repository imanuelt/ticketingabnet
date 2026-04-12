output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "web_app_name" {
  value = azurerm_linux_web_app.this.name
}

output "web_app_default_hostname" {
  value = azurerm_linux_web_app.this.default_hostname
}

output "github_actions_client_id" {
  value = azuread_application.github_deploy.client_id
}

output "github_actions_tenant_id" {
  value = var.tenant_id
}

output "github_actions_subscription_id" {
  value = var.subscription_id
}

output "cosmos_db_uri" {
  value = azurerm_cosmosdb_account.this.endpoint
}

output "key_vault_name" {
  value = azurerm_key_vault.this.name
}
