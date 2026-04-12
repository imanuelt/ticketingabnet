variable "subscription_id" {
  description = "Azure subscription ID."
  type        = string
}

variable "tenant_id" {
  description = "Azure Entra tenant ID."
  type        = string
}

variable "project_name" {
  description = "Short project name used for Azure resource naming."
  type        = string
  default     = "ticketabnet"
}

variable "location" {
  description = "Azure region for all resources."
  type        = string
  default     = "westeurope"
}

variable "resource_group_name" {
  description = "Name of the resource group."
  type        = string
  default     = "rg-ticketabnet-prod"
}

variable "app_service_plan_sku" {
  description = "App Service plan SKU."
  type        = string
  default     = "B1"
}

variable "github_owner" {
  description = "GitHub organization or username that owns the deployment repository."
  type        = string
  default     = "imanuelt"
}

variable "github_repo" {
  description = "GitHub repository name that deploys the app."
  type        = string
  default     = "ticketingabnet"
}

variable "github_branch" {
  description = "Git branch allowed to deploy through OIDC."
  type        = string
  default     = "master"
}

variable "web_app_name" {
  description = "Azure Web App name."
  type        = string
  default     = "ticketabnet"
}

variable "cosmos_account_name" {
  description = "Globally unique Azure Cosmos DB account name prefix."
  type        = string
  default     = "ticketabnetcosmos"
}

variable "cosmos_database_name" {
  description = "Cosmos DB SQL database name."
  type        = string
  default     = "ticketingdb"
}

variable "cosmos_container_name" {
  description = "Cosmos DB SQL container name."
  type        = string
  default     = "ticketingdbcont"
}

variable "key_vault_name" {
  description = "Globally unique Azure Key Vault name prefix."
  type        = string
  default     = "kvticketabnetprod"
}
