#!/bin/bash
set -euo pipefail
export HOME=/root
export KUBECONFIG=/root/.kube/config

# Safety: refuse to apply if any token was not replaced
if grep -rq "__[A-Z_]*__" k8s/overlays/poc; then
  echo "Unreplaced tokens found"; exit 1
fi

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
