import os
import sys
import time
import boto3

PROFILE = os.environ.get("AWS_PROFILE", "sandbox-lakehouse-dev")
REGION = os.environ.get("AWS_REGION", "eu-west-1")
ENV = os.environ.get("ENV", "dev")

# Validation queries
QUERIES = {
    "products-count": "SELECT COUNT(*) as total_products FROM dim_products;",
    "orders-count": "SELECT COUNT(*) as total_orders FROM fct_orders;",
    "order-items-count": "SELECT COUNT(*) as total_order_items FROM fct_order_items;",
    "products-sample": "SELECT product_id, department, product_name FROM dim_products LIMIT 5;",
    "orders-sample": "SELECT order_id, user_id, total_amount FROM fct_orders LIMIT 5;",
    "order-items-sample": "SELECT id, order_id, product_id FROM fct_order_items LIMIT 5;"
}

def get_session():
    try:
        return boto3.Session(profile_name=PROFILE, region_name=REGION)
    except Exception:
        return boto3.Session(region_name=REGION)

def main():
    session = get_session()
    athena = session.client("athena")
    
    database = f"ecom_lakehouse_db_{ENV}"
    output_location = f"s3://ecom-lakehouse-athena-results-{ENV}/"
    
    print("=" * 60)
    print(f"  ATHENA QUERY TOOL (DB: {database})")
    print(f"  Profile: {PROFILE} | Region: {REGION}")
    print("=" * 60)
    
    # Check if a custom query is passed
    if len(sys.argv) > 1:
        query_key = sys.argv[1]
        if query_key in QUERIES:
            queries_to_run = {query_key: QUERIES[query_key]}
        else:
            # Run custom query string
            queries_to_run = {"custom": query_key}
    else:
        # Run all verification counts
        queries_to_run = {
            "products-count": QUERIES["products-count"],
            "orders-count": QUERIES["orders-count"],
            "order-items-count": QUERIES["order-items-count"]
        }

    for name, sql in queries_to_run.items():
        print(f"\n[*] Running [{name}]: {sql}")
        try:
            response = athena.start_query_execution(
                QueryString=sql,
                QueryExecutionContext={"Database": database},
                ResultConfiguration={"OutputLocation": output_location}
            )
            execution_id = response["QueryExecutionId"]
            
            # Poll status
            while True:
                status = athena.get_query_execution(QueryExecutionId=execution_id)
                state = status["QueryExecution"]["Status"]["State"]
                if state in ["SUCCEEDED", "FAILED", "CANCELLED"]:
                    break
                time.sleep(1)
                
            if state != "SUCCEEDED":
                reason = status["QueryExecution"]["Status"].get("StateChangeReason", "Unknown error")
                print(f"  [ERROR] Query failed in state {state}: {reason}")
                continue
                
            # Get results
            results = athena.get_query_results(QueryExecutionId=execution_id)
            rows = results["ResultSet"]["Rows"]
            
            # Print tabular results
            if len(rows) > 0:
                # Header
                headers = [col.get("VarCharValue", "NULL") for col in rows[0]["Data"]]
                print("  | " + " | ".join(headers) + " |")
                print("  |-" + "-|-".join(["-" * len(h) for h in headers]) + "-|")
                # Data
                for row in rows[1:]:
                    vals = [col.get("VarCharValue", "NULL") for col in row["Data"]]
                    print("  | " + " | ".join(vals) + " |")
            else:
                print("  [OK] Query completed, no rows returned.")
        except Exception as e:
            print(f"  [ERROR] Query failed: {e}")

if __name__ == "__main__":
    main()
