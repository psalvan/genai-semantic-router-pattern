provider "aws" {}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_ami" "ubuntu_noble_arm64" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-noble-24.04-arm64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_s3_bucket" "semantic_router_artifacts" {
  bucket = "${var.environment}-demo-semantic-router-artifacts-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name        = "${var.environment}-demo-semantic-router-artifacts"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_public_access_block" "semantic_router_artifacts" {
  bucket = aws_s3_bucket.semantic_router_artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls        = true
  restrict_public_buckets = true
}

resource "aws_iam_role" "semantic_router_ec2" {
  name = "${var.environment}-demo-semantic-router-ec2"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Name        = "${var.environment}-demo-semantic-router-ec2"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy_attachment" "semantic_router_ssm" {
  role       = aws_iam_role.semantic_router_ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "semantic_router_s3_artifacts" {
  name = "SemanticRouterReadArtifacts"
  role = aws_iam_role.semantic_router_ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ListSemanticRouterPrefix"
        Effect = "Allow"
        Action = ["s3:ListBucket"]
        Resource = [
          aws_s3_bucket.semantic_router_artifacts.arn
        ]
      },
      {
        Sid      = "GetSemanticRouterObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${aws_s3_bucket.semantic_router_artifacts.arn}/semantic-router/*"]
      },
      {
        Sid    = "GetSmartRouterKeyParameter"
        Effect = "Allow"
        Action = ["ssm:GetParameter"]
        Resource = [
          "arn:aws:ssm:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:parameter/${var.environment}/nvidia-demo/SMART_ROUTER_KEY"
        ]
      },
      {
        Sid      = "KmsDecryptSecureStringParameters"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = ["*"]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "semantic_router_ec2" {
  name = "${var.environment}-demo-semantic-router-ec2"
  role = aws_iam_role.semantic_router_ec2.name
}

resource "aws_security_group" "semantic_router" {
  name        = "${var.environment}-demo-semantic-router-sg"
  description = "${var.environment} demo semantic-router — SSH + 8000 (API)"
  vpc_id      = var.vpc_id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_ingress_cidr]
  }

  ingress {
    description = "FastAPI — restringir na app com SMART_ROUTER_KEY"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.environment}-demo-semantic-router-sg"
    Environment = var.environment
  }
}

resource "aws_eip" "semantic_router" {
  domain = "vpc"

  tags = {
    Name        = "${var.environment}-demo-semantic-router-eip"
    Environment = var.environment
  }
}

resource "aws_instance" "semantic_router" {
  ami                         = data.aws_ami.ubuntu_noble_arm64.id
  instance_type               = var.instance_type
  subnet_id                   = var.public_subnet_id
  vpc_security_group_ids      = [aws_security_group.semantic_router.id]
  associate_public_ip_address = true
  iam_instance_profile        = aws_iam_instance_profile.semantic_router_ec2.name
  key_name                    = var.key_name

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
  }

  user_data = base64encode(templatefile("${path.module}/user-data-terraform.tpl", {
    bucket      = aws_s3_bucket.semantic_router_artifacts.id
    region      = data.aws_region.current.id
    environment = var.environment
  }))

  tags = {
    Name        = "${var.environment}-demo-semantic-router"
    Environment = var.environment
  }

  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_eip_association" "semantic_router" {
  instance_id   = aws_instance.semantic_router.id
  allocation_id = aws_eip.semantic_router.id
}
