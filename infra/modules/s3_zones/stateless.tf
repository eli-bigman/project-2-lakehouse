# FILE 3: infra/modules/s3_zones/stateless.tf
# Stateless S3 zones managed via for_each — safe to recreate, no prevent_destroy.
# Zones: staging (7d expiry), athena-results (30d expiry), artifacts (no expiry, versioned).
# ADR-021: Same EnforceHTTPS + EnforceKMSEncryption policies as stateful zones.

locals {
  # Zone definitions: name suffix → expiry_days (0 = no expiry rule).
  stateless_zones = {
    "staging"        = { expiry_days = 7 }
    "athena-results" = { expiry_days = 30 }
    "artifacts"      = { expiry_days = 0 }
  }
}

resource "aws_s3_bucket" "stateless" {
  for_each = local.stateless_zones

  bucket = "${var.prefix}-${each.key}-${var.env}"

  # Stateless zones can be safely recreated — no force_destroy guard needed.
  force_destroy = true

  tags = {
    Name = "${var.prefix}-${each.key}-${var.env}"
    Zone = each.key
  }

  # No lifecycle prevent_destroy — these zones are ephemeral by design.
}

resource "aws_s3_bucket_public_access_block" "stateless" {
  for_each = local.stateless_zones

  bucket = aws_s3_bucket.stateless[each.key].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "stateless" {
  for_each = local.stateless_zones

  bucket = aws_s3_bucket.stateless[each.key].id

  versioning_configuration {
    # artifacts bucket is versioned for script history; others enabled for auditability.
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "stateless" {
  for_each = local.stateless_zones

  bucket = aws_s3_bucket.stateless[each.key].id

  rule {
    bucket_key_enabled = true

    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = local.s3_kms_key_id
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "stateless" {
  # Only create lifecycle configs for zones that have an expiry rule.
  for_each = { for k, v in local.stateless_zones : k => v if v.expiry_days > 0 }

  bucket = aws_s3_bucket.stateless[each.key].id

  rule {
    id     = "expire-objects"
    status = "Enabled"

    filter {}

    expiration {
      days = each.value.expiry_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 7
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 3
    }
  }
}

# ── Bucket policies (EnforceHTTPS + EnforceKMSEncryption) ─────────────────────
# for_each on aws_iam_policy_document is not supported, so we use a single
# data source with dynamic blocks — one statement-set per bucket.
# Instead, we use separate data source + resource pairs via for_each on
# aws_s3_bucket_policy, referencing a templatefile-style inline document.

data "aws_iam_policy_document" "stateless_policy" {
  for_each = local.stateless_zones

  statement {
    sid    = "EnforceHTTPS"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.stateless[each.key].arn,
      "${aws_s3_bucket.stateless[each.key].arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  # EnforceKMSEncryption only when use_cmk=true (prod CMK setup).
  # In dev (use_cmk=false), S3 default bucket encryption handles SSE-KMS
  # automatically without requiring the caller to pass the header explicitly.
  dynamic "statement" {
    for_each = var.use_cmk ? [1] : []
    content {
      sid    = "EnforceKMSEncryption"
      effect = "Deny"

      principals {
        type        = "*"
        identifiers = ["*"]
      }

      actions   = ["s3:PutObject"]
      resources = ["${aws_s3_bucket.stateless[each.key].arn}/*"]

      condition {
        test     = "StringNotEquals"
        variable = "s3:x-amz-server-side-encryption"
        values   = ["aws:kms"]
      }
    }
  }
}

resource "aws_s3_bucket_policy" "stateless" {
  for_each = local.stateless_zones

  bucket = aws_s3_bucket.stateless[each.key].id
  policy = data.aws_iam_policy_document.stateless_policy[each.key].json

  depends_on = [aws_s3_bucket_public_access_block.stateless]
}
