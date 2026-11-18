#!/bin/bash
set -euo pipefail
export HOME=/root
export KUBECONFIG=/root/.kube/config

# Safety: refuse to apply if any token was not replaced
if grep -rq "__[A-Z_]*__" k8s/overlays/poc; then
  echo "Unreplaced tokens found"; exit 1
fi

kubectl apply -k k8s/overlays/poc
kubectl rollout status deployment/signup-app -n default --timeout=300s
kubectl get pods -n default -l app=signup-app