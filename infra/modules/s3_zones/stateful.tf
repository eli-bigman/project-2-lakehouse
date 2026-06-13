# FILE 2: infra/modules/s3_zones/stateful.tf
# Explicit resource blocks for the four stateful S3 zones.
# ADR-018: Each stateful bucket is declared individually (not for_each) so that
#          Terraform can apply `lifecycle { prevent_destroy = true }`.
#          force_destroy = !var.protect_stateful lets CI/CD teardown empty buckets
#          once the operator has followed the documented teardown procedure.
# ADR-021: Every bucket policy enforces HTTPS-only and KMS encryption.
# ADR-016: No Glacier transitions — small-file 128 KB minimum penalty outweighs savings.
# ADR-017: dev uses aws/s3 managed key; prod uses CMK (var.use_cmk).

locals {
  # Resolve the effective KMS key ID for SSE configuration.
  # null → Terraform omits the attribute → AWS uses the service-managed key (aws/s3).
  s3_kms_key_id = var.use_cmk ? var.kms_key_arn : null
}

# ─────────────────────────────────────────────────────────────────────────────
# RAW ZONE  (ecom-lakehouse-raw-{env})
# Purpose: immutable landing zone for original xlsx/csv drops.
# Layout:  s3://.../orders/2025/04/orders_apr_2025.xlsx
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "raw" {
  bucket = "${var.prefix}-raw-${var.env}"

  # force_destroy empties the bucket before Terraform deletes it.
  # Combined with prevent_destroy=true below, teardown requires the documented procedure.
  force_destroy = !var.protect_stateful

  tags = {
    Name = "${var.prefix}-raw-${var.env}"
    Zone = "raw"
  }

  lifecycle {
    # TEARDOWN OVERRIDE: comment out this block + set protect_stateful=false.
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket = aws_s3_bucket.raw.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    # bucket_key_enabled=true reduces KMS API calls by ~99% (ADR-017).
    bucket_key_enabled = true

    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = local.s3_kms_key_id
    }
  }
}

# Raw zone: no expiry (original files are immutable and retained indefinitely).
# No Glacier transitions (ADR-016).
resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "raw_policy" {
  # ADR-021 DENY #1: Reject non-HTTPS requests.
  statement {
    sid    = "EnforceHTTPS"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions   = ["s3:*"]
    resources = [aws_s3_bucket.raw.arn, "${aws_s3_bucket.raw.arn}/*"]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  # ADR-021 DENY #2: Reject PutObject without KMS server-side encryption.
  # Note: we only enforce the algorithm (aws:kms), NOT a specific key ARN,
  # so that dev (aws/s3 managed key) and prod (CMK) both pass.
  statement {
    sid    = "EnforceKMSEncryption"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.raw.arn}/*"]

    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }
}

resource "aws_s3_bucket_policy" "raw" {
  bucket = aws_s3_bucket.raw.id
  policy = data.aws_iam_policy_document.raw_policy.json

  depends_on = [aws_s3_bucket_public_access_block.raw]
}

# ─────────────────────────────────────────────────────────────────────────────
# DWH ZONE  (ecom-lakehouse-dwh-{env})
# Purpose: curated Delta Lake tables (Silver layer). ACID, versioned.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "dwh" {
  bucket = "${var.prefix}-dwh-${var.env}"

  force_destroy = !var.protect_stateful

  tags = {
    Name = "${var.prefix}-dwh-${var.env}"
    Zone = "dwh"
  }

  lifecycle {
    # TEARDOWN OVERRIDE: comment out this block + set protect_stateful=false.
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "dwh" {
  bucket = aws_s3_bucket.dwh.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "dwh" {
  bucket = aws_s3_bucket.dwh.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dwh" {
  bucket = aws_s3_bucket.dwh.id

  rule {
    bucket_key_enabled = true

    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = local.s3_kms_key_id
    }
  }
}

# DWH: indefinite retention, no expiry, no Glacier.
resource "aws_s3_bucket_lifecycle_configuration" "dwh" {
  bucket = aws_s3_bucket.dwh.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "dwh_policy" {
  statement {
    sid    = "EnforceHTTPS"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions   = ["s3:*"]
    resources = [aws_s3_bucket.dwh.arn, "${aws_s3_bucket.dwh.arn}/*"]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid    = "EnforceKMSEncryption"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.dwh.arn}/*"]

    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }
}

resource "aws_s3_bucket_policy" "dwh" {
  bucket = aws_s3_bucket.dwh.id
  policy = data.aws_iam_policy_document.dwh_policy.json

  depends_on = [aws_s3_bucket_public_access_block.dwh]
}

# ─────────────────────────────────────────────────────────────────────────────
# ARCHIVE ZONE  (ecom-lakehouse-archive-{env})
# Purpose: post-ingest originals stored long-term (S3 Standard).
# Layout:  s3://.../orders/2025-04-01-abc123/ (batch_id keyed)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "archive" {
  bucket = "${var.prefix}-archive-${var.env}"

  force_destroy = !var.protect_stateful

  tags = {
    Name = "${var.prefix}-archive-${var.env}"
    Zone = "archive"
  }

  lifecycle {
    # TEARDOWN OVERRIDE: comment out this block + set protect_stateful=false.
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "archive" {
  bucket = aws_s3_bucket.archive.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "archive" {
  bucket = aws_s3_bucket.archive.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "archive" {
  bucket = aws_s3_bucket.archive.id

  rule {
    bucket_key_enabled = true

    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = local.s3_kms_key_id
    }
  }
}

# Archive: indefinite retention, no expiry, no Glacier (ADR-016).
resource "aws_s3_bucket_lifecycle_configuration" "archive" {
  bucket = aws_s3_bucket.archive.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "archive_policy" {
  statement {
    sid    = "EnforceHTTPS"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions   = ["s3:*"]
    resources = [aws_s3_bucket.archive.arn, "${aws_s3_bucket.archive.arn}/*"]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid    = "EnforceKMSEncryption"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.archive.arn}/*"]

    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }
}

resource "aws_s3_bucket_policy" "archive" {
  bucket = aws_s3_bucket.archive.id
  policy = data.aws_iam_policy_document.archive_policy.json

  depends_on = [aws_s3_bucket_public_access_block.archive]
}

# ─────────────────────────────────────────────────────────────────────────────
# QUARANTINE ZONE  (ecom-lakehouse-quarantine-{env})
# Purpose: rejected records + reject reason. Auto-expires after 180 days.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "quarantine" {
  bucket = "${var.prefix}-quarantine-${var.env}"

  force_destroy = !var.protect_stateful

  tags = {
    Name = "${var.prefix}-quarantine-${var.env}"
    Zone = "quarantine"
  }

  lifecycle {
    # TEARDOWN OVERRIDE: comment out this block + set protect_stateful=false.
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id

  rule {
    bucket_key_enabled = true

    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = local.s3_kms_key_id
    }
  }
}

# Quarantine: expire objects after 180 days (enough time for investigation).
resource "aws_s3_bucket_lifecycle_configuration" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id

  rule {
    id     = "expire-quarantine-records"
    status = "Enabled"

    filter {}

    expiration {
      days = 180
    }

    # Expire non-current versions after 30 days to reclaim space.
    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "quarantine_policy" {
  statement {
    sid    = "EnforceHTTPS"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions   = ["s3:*"]
    resources = [aws_s3_bucket.quarantine.arn, "${aws_s3_bucket.quarantine.arn}/*"]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid    = "EnforceKMSEncryption"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.quarantine.arn}/*"]

    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }
}

resource "aws_s3_bucket_policy" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id
  policy = data.aws_iam_policy_document.quarantine_policy.json

  depends_on = [aws_s3_bucket_public_access_block.quarantine]
}
