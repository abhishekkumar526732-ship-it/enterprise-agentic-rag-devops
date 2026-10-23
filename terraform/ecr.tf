# --- Amazon ECR Private Repository ---
resource "aws_ecr_repository" "copilot" {
  name                 = var.ecr_repository_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Name = var.ecr_repository_name
  }
}

# --- ECR Lifecycle Policy (Keep last 30 images, purge old artifacts) ---
resource "aws_ecr_lifecycle_policy" "copilot_cleanup" {
  repository = aws_ecr_repository.copilot.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 30 tagged container images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 30
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
