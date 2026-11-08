variable "aws_account_id" {
  type        = string
  description = "Conta de destino explicitamente autorizada."
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Informe o ID de 12 dígitos da conta de destino."
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "poc-carteira"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,17}$", var.project_name))
    error_message = "Use de 3 a 18 caracteres minúsculos, números e hífens."
  }
}

variable "environment" {
  type    = string
  default = "dev"
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,7}$", var.environment))
    error_message = "Use de 2 a 8 caracteres minúsculos ou números."
  }
}

variable "release_id" {
  type        = string
  description = "SHA-256 do pacote de código."
  validation {
    condition     = can(regex("^[a-f0-9]{64}$", var.release_id))
    error_message = "Execute tools/package_aws.py antes de implantar."
  }
}

variable "max_source_bytes" {
  type    = number
  default = 67108864
}

variable "max_source_rows" {
  type    = number
  default = 250000
}

variable "glue_workers" {
  type    = number
  default = 2
  validation {
    condition     = var.glue_workers >= 2 && var.glue_workers <= 10
    error_message = "Configure de 2 a 10 workers G.1X."
  }
}

variable "shuffle_partitions" {
  type    = number
  default = 4
  validation {
    condition     = var.shuffle_partitions >= 1 && var.shuffle_partitions <= 200
    error_message = "Configure de 1 a 200 partições de shuffle."
  }
}

variable "output_partitions" {
  type    = number
  default = 2
  validation {
    condition     = var.output_partitions >= 1 && var.output_partitions <= 200
    error_message = "Configure de 1 a 200 partições de saída."
  }
}
