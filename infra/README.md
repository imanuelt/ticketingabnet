# Azure infrastructure

This Terraform stack provisions the Azure resources required for this app:

- Resource group
- Linux App Service plan on Basic B1
- Linux Web App for Python 3.12
- Application Insights
- Azure Cosmos DB for NoSQL
- Azure Key Vault for application secrets
- Azure Entra application and federated credential for GitHub Actions OIDC deployment

## Required inputs

Copy `terraform.tfvars.example` to `terraform.tfvars` and provide:

- `subscription_id`
- `tenant_id`

You can optionally override names and location.

## What Terraform creates for deployment

After `terraform apply`, use these outputs as GitHub repository secrets:

- `github_actions_client_id` -> `AZURE_CLIENT_ID`
- `github_actions_tenant_id` -> `AZURE_TENANT_ID`
- `github_actions_subscription_id` -> `AZURE_SUBSCRIPTION_ID`

The GitHub workflow in `.github/workflows/master_ticketabnet.yml` will then deploy the app to the provisioned Web App on pushes to `master`.

Because the workflow deploys through the `Production` GitHub environment, the Azure federated identity trusts the GitHub OIDC environment subject rather than a plain branch ref.

## Apply

```powershell
terraform init
terraform plan
terraform apply
```
