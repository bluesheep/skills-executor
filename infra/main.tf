locals {
  tags = {
    "azd-env-name" = var.environment_name
  }
  sha            = base64encode(sha256("${var.environment_name}${var.location}${data.azurerm_client_config.current.subscription_id}"))
  resource_token = substr(replace(lower(local.sha), "[^a-z0-9]", ""), 0, 13)
}

# ============================================================
# Resource Group
# ============================================================

resource "azurecaf_name" "rg" {
  name          = var.environment_name
  resource_type = "azurerm_resource_group"
  random_length = 0
  clean_input   = true
}

resource "azurerm_resource_group" "rg" {
  name     = azurecaf_name.rg.result
  location = var.location
  tags     = local.tags
}

# ============================================================
# User-Assigned Managed Identity
# ============================================================

resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${var.environment_name}-${local.resource_token}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  tags                = local.tags
}

# ============================================================
# Azure Container Registry
# ============================================================

resource "azurerm_container_registry" "acr" {
  name                = "acr${replace(var.environment_name, "-", "")}${local.resource_token}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# ============================================================
# Log Analytics + Container Apps Environment
# ============================================================

resource "azurerm_log_analytics_workspace" "law" {
  name                = "law-${var.environment_name}-${local.resource_token}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_container_app_environment" "env" {
  name                       = "cae-${var.environment_name}-${local.resource_token}"
  resource_group_name        = azurerm_resource_group.rg.name
  location                   = azurerm_resource_group.rg.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.law.id
  tags                       = local.tags
}

# ============================================================
# Container App
# ============================================================

resource "azurerm_container_app" "api" {
  name                         = "ca-${var.environment_name}-${local.resource_token}"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = azurerm_resource_group.rg.name
  revision_mode                = "Single"

  tags = merge(local.tags, {
    "azd-service-name" = "api"
  })

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  template {
    min_replicas = 0
    max_replicas = 3

    container {
      name   = "skills-executor"
      image  = "${azurerm_container_registry.acr.login_server}/skills-executor:latest"
      cpu    = 1.0
      memory = "2Gi"

      env {
        name  = "LLM_PROVIDER"
        value = "azure_openai"
      }
      env {
        name  = "AZURE_AI_PROJECT_ENDPOINT"
        value = azurerm_cognitive_account.openai.endpoint
      }
      env {
        name  = "AZURE_DEPLOYMENT_NAME"
        value = azurerm_cognitive_deployment.gpt.name
      }
      env {
        name  = "LLM_MODEL"
        value = var.openai_model_name
      }
      env {
        name  = "AZURE_API_VERSION"
        value = "2024-12-01-preview"
      }
      env {
        name  = "SANDBOX_MODE"
        value = "subprocess"
      }
      env {
        name  = "SKILL_PATHS"
        value = "/app/skills"
      }
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.app.client_id
      }
      env {
        name  = "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"
        value = azurerm_cognitive_account.document_intelligence.endpoint
      }

      dynamic "env" {
        for_each = var.api_key != "" ? [1] : []
        content {
          name  = "API_KEY"
          value = var.api_key
        }
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
}

# ============================================================
# Azure OpenAI
# ============================================================

resource "azurerm_cognitive_account" "openai" {
  name                  = "oai-${var.environment_name}-${local.resource_token}"
  resource_group_name   = azurerm_resource_group.rg.name
  location              = var.location
  kind                  = "AIServices"
  sku_name              = "S0"
  custom_subdomain_name = "oai-${var.environment_name}-${local.resource_token}"
  tags                  = local.tags
}

resource "azurerm_cognitive_deployment" "gpt" {
  name                 = var.openai_model_name
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI-OSS"
    name    = var.openai_model_name
    version = var.openai_model_version
  }

  sku {
    name     = var.openai_model_sku
    capacity = var.openai_model_capacity
  }
}

# ============================================================
# Role Assignment: Managed Identity -> OpenAI
# ============================================================

resource "azurerm_role_assignment" "openai_user" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# ============================================================
# Azure Document Intelligence
# ============================================================

resource "azurerm_cognitive_account" "document_intelligence" {
  name                  = "di-${var.environment_name}-${local.resource_token}"
  resource_group_name   = azurerm_resource_group.rg.name
  location              = var.location
  kind                  = "FormRecognizer"
  sku_name              = "S0"
  custom_subdomain_name = "di-${var.environment_name}-${local.resource_token}"
  tags                  = local.tags
}

resource "azurerm_role_assignment" "document_intelligence_user" {
  scope                = azurerm_cognitive_account.document_intelligence.id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}
