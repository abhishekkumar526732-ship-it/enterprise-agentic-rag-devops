output "cluster_id" {
  description = "EKS Cluster ID"
  value       = aws_eks_cluster.main.id
}

output "cluster_name" {
  description = "EKS Cluster Name"
  value       = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  description = "Kubernetes API endpoint for the EKS Cluster"
  value       = aws_eks_cluster.main.endpoint
}

output "cluster_version" {
  description = "Kubernetes version running on the cluster"
  value       = aws_eks_cluster.main.version
}

output "vpc_id" {
  description = "ID of the VPC created for the EKS Cluster"
  value       = aws_vpc.eks_vpc.id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets (used for Ingress ALB)"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "IDs of the private subnets (used for worker nodes)"
  value       = aws_subnet.private[*].id
}

output "ecr_repository_url" {
  description = "Amazon ECR repository URI for pushing the copilot image"
  value       = aws_ecr_repository.copilot.repository_url
}

output "alb_controller_role_arn" {
  description = "IAM Role ARN created for AWS Load Balancer Controller"
  value       = aws_iam_role.alb_controller.arn
}

output "configure_kubectl_command" {
  description = "Command to connect local kubectl to this new EKS cluster"
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${aws_eks_cluster.main.name}"
}

output "helm_install_alb_controller_command" {
  description = "Helm command to install AWS Load Balancer Controller with the provisioned IAM role"
  value       = "helm install aws-load-balancer-controller eks/aws-load-balancer-controller -n kube-system --set clusterName=${aws_eks_cluster.main.name} --set serviceAccount.create=true --set serviceAccount.name=aws-load-balancer-controller --set serviceAccount.annotations.\"eks\\.amazonaws\\.com/role-arn\"=${aws_iam_role.alb_controller.arn}"
}
