# 🏗️ Production AWS EKS with Terraform (IaC)

This directory contains production-ready **Terraform** configuration to automatically provision the entire AWS infrastructure for the **Enterprise HR Policy Agentic RAG Copilot**.

---

## 🏛️ Infrastructure Architecture Provisioned

1. **Custom Multi-AZ VPC (`10.0.0.0/16`)**:
   - **2 Public Subnets**: Used by AWS Application Load Balancers (ALB) to expose the app to the internet with tag `kubernetes.io/role/elb = 1`.
   - **2 Private Subnets**: Dedicated to EKS Worker Nodes with tag `kubernetes.io/role/internal-elb = 1` for zero direct exposure to the public internet.
   - **NAT Gateway & Elastic IP**: Enables private worker nodes to download models, packages, and connect to Pinecone/OpenAI/Gemini APIs safely.
2. **AWS EKS Control Plane v1.30**:
   - Managed Kubernetes master nodes with CloudWatch audit logging enabled.
   - Public & Private API endpoints.
3. **EKS Managed Node Group (`t3.large`)**:
   - Autoscaling worker nodes (2 desired, min 2, max 6).
   - Deployed strictly in private subnets with least-privilege IAM policies.
4. **Amazon ECR Private Repository**:
   - Repository `enterprise-hr-copilot` with `scan_on_push = true` and automated 30-image lifecycle cleanup.
5. **IAM Roles for Service Accounts (IRSA)**:
   - Automated OIDC Identity Provider setup.
   - Dedicated IAM Role and official IAM Policy for **AWS Load Balancer Controller**.
   - Official AWS EBS CSI Driver addon for Persistent Volume Claims (PVC).

---

## 📋 Prerequisites

1. **AWS CLI v2** installed and configured:
   ```bash
   aws configure
   ```
2. **Terraform CLI (>= 1.5.0)** installed (`terraform -v`).
3. **kubectl** installed.
4. **Helm v3** installed.

---

## 🚀 Step-by-Step Deployment

### 1. Initialize Terraform
Navigate to the `terraform/` directory and initialize provider plugins:
```bash
cd terraform
terraform init
```

### 2. Review the Execution Plan
```bash
terraform plan
```
This will outline ~25 AWS resources to be provisioned (VPC, Subnets, NAT GW, EKS Cluster, Node Group, ECR, IAM Roles).

### 3. Apply Infrastructure
```bash
terraform apply -auto-approve
```
*Note: EKS cluster and managed node creation typically takes ~10-12 minutes on AWS.*

---

## 🔗 Post-Provisioning Steps

### 1. Connect `kubectl` to your new EKS Cluster
Run the command shown in Terraform outputs:
```bash
aws eks update-kubeconfig --region us-east-1 --name enterprise-hr-eks-cluster
```

Verify nodes are healthy and running:
```bash
kubectl get nodes -o wide
```

### 2. Install AWS Load Balancer Controller via Helm
Run Helm using the IAM Role ARN provisioned by Terraform:
```bash
helm repo add eks https://aws.github.io/eks-charts
helm repo update

# Use the exact command outputted by terraform output helm_install_alb_controller_command
helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=enterprise-hr-eks-cluster \
  --set serviceAccount.create=true \
  --set serviceAccount.name=aws-load-balancer-controller \
  --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"=$(terraform output -raw alb_controller_role_arn)
```

Verify controller is running:
```bash
kubectl get deployment -n kube-system aws-load-balancer-controller
```

### 3. Push Docker Image to Amazon ECR
```bash
cd ..
# 1. Log in to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $(terraform -chdir=terraform output -raw ecr_repository_url)

# 2. Build and Tag
docker build -t enterprise-hr-copilot:latest .
docker tag enterprise-hr-copilot:latest $(terraform -chdir=terraform output -raw ecr_repository_url):latest

# 3. Push to ECR
docker push $(terraform -chdir=terraform output -raw ecr_repository_url):latest
```

### 4. Deploy Kubernetes Manifests
Update `k8s/secret.yaml` with your API keys, then apply the manifests:
```bash
kubectl apply -k k8s/
```

Check rollout status:
```bash
kubectl rollout status deployment/hr-copilot-deployment -n enterprise-hr
kubectl get ingress -n enterprise-hr
```

The Ingress will display the public DNS of the Application Load Balancer!

---

## 🧹 Teardown / Cleanup
To destroy all provisioned infrastructure and avoid AWS costs when done:
```bash
# 1. Delete Kubernetes resources first to release ALBs
kubectl delete -k ../k8s/

# 2. Destroy AWS Infrastructure
terraform destroy -auto-approve
```
