# FILE 18: infra/envs/dev/backend.tf
# Remote state backend — S3 + DynamoDB lock.
#
# BOOTSTRAP REQUIRED (one-time, before first terraform init):
#   These resources must exist before Terraform can use this backend.
#   Create them manually with the personal AWS profile:
#
#   aws s3api create-bucket \
#     --bucket ecom-lakehouse-tf-state-647594457599 \
#     --region us-east-1 \
#     --profile personal
#
#   aws s3api put-bucket-versioning \
#     --bucket ecom-lakehouse-tf-state-647594457599 \
#     --versioning-configuration Status=Enabled \
#     --profile personal
#
#   aws s3api put-bucket-encryption \
#     --bucket ecom-lakehouse-tf-state-647594457599 \
#     --server-side-encryption-configuration \
#       '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"},"BucketKeyEnabled":true}]}' \
#     --profile personal
#
#   aws dynamodb create-table \
#     --table-name ecom-lakehouse-tf-locks \
#     --attribute-definitions AttributeName=LockID,AttributeType=S \
#     --key-schema AttributeName=LockID,KeyType=HASH \
#     --billing-mode PAY_PER_REQUEST \
#     --region us-east-1 \
#     --profile personal
#
# After bootstrap: terraform init -backend-config=backend.tf

terraform {
  backend "s3" {
    bucket         = "ecom-lakehouse-tf-state-970547336735"
    key            = "env/dev/terraform.tfstate"
    region         = "eu-west-1"
    dynamodb_table = "ecom-lakehouse-tf-locks"
    encrypt        = true
  }
}
