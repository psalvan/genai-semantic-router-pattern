output "artifact_bucket_name" {
  description = "Bucket S3 (prefix semantic-router/) — mesmo padrão que o stack SAM; usar com scripts/publish_semantic_router.sh."
  value       = aws_s3_bucket.semantic_router_artifacts.id
}

output "elastic_ip" {
  description = "IP público fixo (Elastic IP)."
  value       = aws_eip.semantic_router.public_ip
}

output "instance_id" {
  description = "ID da instância (SEMANTIC_ROUTER_EC2_INSTANCE_ID no .env)."
  value       = aws_instance.semantic_router.id
}

output "smart_router_url_for_ssm" {
  description = "Valor para /{environment}/nvidia-demo/SMART_ROUTER_URL (se gerires SSM fora do SAM)."
  value       = "http://${aws_eip.semantic_router.public_ip}:8000/check-intent"
}
