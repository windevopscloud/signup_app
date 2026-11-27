#!/bin/bash
set -euo pipefail
export HOME=/root
export KUBECONFIG=/root/.kube/config

# Safety: refuse to apply if any token was not replaced
if grep -rq "__[A-Z_]*__" k8s/overlays/poc; then
  echo "Unreplaced tokens found"; exit 1
fi

# Create or sync the dedicated DB user (password comes from Secrets Manager)
bash deploy/db-bootstrap.sh

# The Ingress and Service are checked by the AWS Load Balancer Controller's webhooks,
# so the controller must be running before we apply.
echo "Waiting for the AWS Load Balancer Controller..."
if ! kubectl rollout status deployment/aws-load-balancer-controller -n kube-system --timeout=300s; then
  echo "Load Balancer Controller is not ready. Check the image mirror and run: kubectl get pods -n kube-system"
  exit 1
fi

# Apply, and retry a few times in case the webhook is still starting up
applied=0
for attempt in 1 2 3 4 5; do
  if kubectl apply -k k8s/overlays/poc; then
    applied=1; break
  fi
  echo "Apply failed (attempt $attempt of 5), retrying in 15s..."
  sleep 15
done
[ "$applied" = "1" ] || { echo "kubectl apply failed after 5 attempts"; exit 1; }

kubectl rollout status deployment/signup-app -n default --timeout=300s
kubectl get pods -n default -l app=signup-app

# The ALB address for the DNS record (shown at the end of the deploy job log)
echo "Waiting for the ALB address..."
ALB=""
for i in $(seq 1 30); do
  ALB=$(kubectl get ingress signup-app -n default -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
  [ -n "$ALB" ] && break
  sleep 10
done
if [ -n "$ALB" ]; then
  echo "=============================================================="
  echo "DNS: create CNAME  signup.windevopscloud.com  ->  ${ALB}"
  echo "=============================================================="
else
  echo "ALB address not ready yet. Run: kubectl get ingress signup-app -n default"
fi
