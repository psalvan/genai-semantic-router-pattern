variable "environment" {
  description = "Prefixo de ambiente (dev / uat / prod), alinhado com o parâmetro Environment do stack SAM."
  type        = string
}

variable "vpc_id" {
  description = "VPC onde criar o security group (ex. default VPC ou a da app)."
  type        = string
}

variable "public_subnet_id" {
  description = "Subnet pública (rota 0.0.0.0/0 → Internet Gateway) para associar a instância e o EIP."
  type        = string
}

variable "ssh_ingress_cidr" {
  description = "CIDR permitido para SSH (ex. o teu IP /32). Evita 0.0.0.0/0 em produção."
  type        = string
  default     = "0.0.0.0/0"
}

variable "instance_type" {
  description = "Tipo de instância Graviton (ARM64) para o semantic-router."
  type        = string
  default     = "t4g.small"
}

variable "root_volume_gb" {
  description = "Tamanho do volume raiz gp3 (GiB)."
  type        = number
  default     = 20
}

variable "key_name" {
  description = "Nome opcional do key pair EC2 para SSH; null usa só SSM Session Manager."
  type        = string
  default     = null
}
