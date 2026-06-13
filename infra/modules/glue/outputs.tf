# FILE 10b: infra/modules/glue/outputs.tf

output "ingest_job_name" {
  description = "Name of the Glue ingest Spark job."
  value       = aws_glue_job.ingest.name
}

output "optimize_job_name" {
  description = "Name of the Glue optimize Spark job."
  value       = aws_glue_job.optimize.name
}

output "database_name" {
  description = "Name of the Glue Catalog database."
  value       = aws_glue_catalog_database.main.name
}

output "dim_products_table_name" {
  description = "Glue catalog table name for dim_products."
  value       = aws_glue_catalog_table.dim_products.name
}

output "fct_orders_table_name" {
  description = "Glue catalog table name for fct_orders."
  value       = aws_glue_catalog_table.fct_orders.name
}

output "fct_order_items_table_name" {
  description = "Glue catalog table name for fct_order_items."
  value       = aws_glue_catalog_table.fct_order_items.name
}
