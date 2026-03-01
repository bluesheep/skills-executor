output "AZURE_LOCATION" {
  value = var.location
}

output "AZURE_TENANT_ID" {
  value = data.azurerm_client_config.current.tenant_id
}

output "AZURE_RESOURCE_GROUP" {
  value = azurerm_resource_group.rg.name
}

output "AZURE_CONTAINER_REGISTRY_ENDPOINT" {
  value = azurerm_container_registry.acr.login_server
}

output "API_BASE_URL" {
  value = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "AZURE_OPENAI_ENDPOINT" {
  value = azurerm_cognitive_account.openai.endpoint
}

output "AZURE_OPENAI_DEPLOYMENT_NAME" {
  value = azurerm_cognitive_deployment.gpt.name
}

output "SERVICE_API_RESOURCE_NAME" {
  value = azurerm_container_app.api.name
}
