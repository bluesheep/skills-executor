variable "environment_name" {
  description = "The name of the azd environment"
  type        = string
}

variable "location" {
  description = "The Azure region for all resources"
  type        = string
}

variable "openai_model_name" {
  description = "The OpenAI model to deploy"
  type        = string
  default     = "gpt-oss-120b"
}

variable "openai_model_version" {
  description = "The version of the OpenAI model"
  type        = string
  default     = "1"
}

variable "openai_model_sku" {
  description = "The SKU name for the OpenAI model deployment"
  type        = string
  default     = "GlobalStandard"
}

variable "openai_model_capacity" {
  description = "Capacity in thousands of tokens per minute"
  type        = number
  default     = 100
}

variable "api_key" {
  description = "API key for authenticating requests to the skills executor"
  type        = string
  sensitive   = true
  default     = ""
}
