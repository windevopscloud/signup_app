# Signup demo (Flask + PostgreSQL)

Flow: register (name + e-mail) → 4-digit code by mail → set password → login →
"Thank you for Joining {Name}, {Mail ID}" → reset password → change e-mail
(one 4-digit code to the old address, another to the new one) → login shows the new e-mail.

## Run locally
    docker compose up --build
    open http://localhost:8000      # app
    open http://localhost:8025      # Mailpit inbox: the 4-digit codes arrive here

## Build and push to ECR
    aws ecr create-repository --repository-name signup-app
    aws ecr get-login-password | docker login --username AWS --password-stdin <ACCOUNT>.dkr.ecr.<REGION>.amazonaws.com
    docker build -t signup-app:1.0.0 .
    docker tag signup-app:1.0.0 <ACCOUNT>.dkr.ecr.<REGION>.amazonaws.com/signup-app:1.0.0
    docker push <ACCOUNT>.dkr.ecr.<REGION>.amazonaws.com/signup-app:1.0.0

## Deploy on EKS (Secrets Store CSI, no ESO)
Secrets are mounted as files under /mnt/secrets-store; the app reads them through
`*_FILE` variables (DB_USER_FILE, DB_PASSWORD_FILE, SMTP_USER_FILE, SMTP_PASSWORD_FILE, SECRET_KEY_FILE).

1. Terraform applied (EKS + add-on, RDS, `secrets` module, ECR repo `signup-app`).
2. Create the tables once, as the DB admin, from the tools instance:
       psql "host=<endpoint> dbname=signuprds user=signuprdsmaster sslmode=require" -f db/schema.sql
3. Secrets in Secrets Manager: `signup/poc/rds-app` (username, password, ...),
   `ses/poc/smtp` (SMTP_USER, SMTP_PASSWORD), `signup/poc/app` ({"SECRET_KEY": "<openssl rand -hex 32>"}).
4. Push the image (see above), fill the <placeholders> in k8s/*.yaml, then:
       kubectl apply -f k8s/00-serviceaccount.yaml -f k8s/10-secretproviderclass.yaml \
                     -f k8s/20-configmap.yaml -f k8s/30-deployment.yaml
5. Check:  kubectl get pods ; kubectl logs deploy/signup-app
   Pods stay in ContainerCreating if a secret cannot be mounted:  kubectl describe pod <pod>
6. k8s/40-ingress.yaml only after the AWS Load Balancer Controller is installed.

Rotation: the app opens a new DB connection per request and re-reads the files each time.
Enable the driver's rotation option so the files refresh after Secrets Manager rotates the password.

## Tests
    python -m unittest tests.test_flow     # uses a SQLite shim, no Postgres needed
