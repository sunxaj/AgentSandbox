#!/usr/bin/env python3
"""Create small, deterministic Parquet fixtures and table metadata documents.

The real data directory lives on the Linux server, so this utility is intended
to be run there after PySpark is installed. The records are deliberately small
and synthetic: they exercise joins and date filtering without copying any real
customer information into the development environment.
"""

import argparse
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql.types import (
    StringType,
    StructField,
    StructType,
)


def build_tables(spark):
    """Return the three fixture DataFrames with stable names and schemas."""
    customer_schema = StructType([
        StructField("customer_id", StringType(), False),
        StructField("first_name", StringType(), False),
        StructField("last_name", StringType(), False),
        StructField("address", StringType(), False),
        StructField("product_id", StringType(), False),
        StructField("registered_time", StringType(), False),
    ])
    customers = spark.createDataFrame([
        ("C001", "Ava", "Nguyen", "1 Example Street", "P001", "2025-01-15T09:00:00Z"),
        ("C002", "Liam", "Patel", "2 Example Street", "P002", "2025-02-03T10:30:00Z"),
        ("C003", "Mia", "Brown", "3 Example Street", "P003", "2025-02-18T14:15:00Z"),
        ("C004", "Noah", "Kim", "4 Example Street", "P001", "2025-03-01T08:45:00Z"),
        ("C005", "Zoe", "Wilson", "5 Example Street", "P004", "2025-03-12T16:20:00Z"),
    ], customer_schema)

    product_schema = StructType([
        StructField("product_id", StringType(), False),
        StructField("product_description", StringType(), False),
        StructField("product_category", StringType(), False),
    ])
    products = spark.createDataFrame([
        ("P001", "Laptop stand", "Office"),
        ("P002", "Wireless keyboard", "Office"),
        ("P003", "Travel backpack", "Travel"),
        ("P004", "Noise cancelling headphones", "Audio"),
    ], product_schema)

    transaction_schema = StructType([
        StructField("transaction_id", StringType(), False),
        StructField("customer_id", StringType(), False),
        StructField("product_id", StringType(), False),
        StructField("datetime", StringType(), False),
    ])
    transactions = spark.createDataFrame([
        ("T001", "C001", "P001", "2025-04-01T09:10:00Z"),
        ("T002", "C001", "P002", "2025-04-03T11:20:00Z"),
        ("T003", "C002", "P002", "2025-04-04T13:05:00Z"),
        ("T004", "C003", "P003", "2025-04-08T15:40:00Z"),
        ("T005", "C004", "P001", "2025-04-09T10:00:00Z"),
        ("T006", "C005", "P004", "2025-04-12T17:25:00Z"),
        ("T007", "C002", "P003", "2025-04-15T12:15:00Z"),
        ("T008", "C003", "P004", "2025-04-20T18:30:00Z"),
        ("T009", "C004", "P002", "2025-04-21T09:45:00Z"),
        ("T010", "C005", "P001", "2025-04-23T14:10:00Z"),
        ("T011", "C001", "P004", "2025-04-26T16:55:00Z"),
        ("T012", "C002", "P001", "2025-04-28T08:35:00Z"),
    ], transaction_schema)
    return {
        "customer": (customers, customer_schema),
        "products": (products, product_schema),
        "transactions": (transactions, transaction_schema),
    }


def metadata_markdown(table_name, dataframe, schema, parquet_path):
    """Render searchable table metadata without including full table contents."""
    columns = "\n".join(
        f"- `{field.name}`: {field.dataType.simpleString()}"
        for field in schema.fields
    )
    sample_rows = dataframe.limit(3).collect()
    samples = "\n".join(f"- `{row.asDict()}`" for row in sample_rows)
    category = {
        "customer": "Customer data",
        "products": "Product catalog",
        "transactions": "Transaction data",
    }[table_name]
    return f"""---
table_name: {table_name}
category: {category}
parquet_path: {parquet_path}
---

# {table_name}

Category: {category}
Parquet path: `{parquet_path}`

## Columns

{columns}

## Example values

{samples}
"""


def main():
    """Write fixtures atomically enough for repeatable local development runs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet-dir", default="/home/xs/Documents/data/parquet")
    parser.add_argument("--metadata-dir", default="/home/xs/Documents/data/metadata")
    args = parser.parse_args()

    parquet_dir = Path(args.parquet_dir).expanduser().resolve()
    metadata_dir = Path(args.metadata_dir).expanduser().resolve()
    parquet_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    spark = (SparkSession.builder
             .appName("create-agent-fixtures")
             .master("local[*]")
             .config("spark.ui.enabled", "false")
             .getOrCreate())
    try:
        for table_name, (dataframe, schema) in build_tables(spark).items():
            output = parquet_dir / f"{table_name}.parquet"
            dataframe.write.mode("overwrite").parquet(str(output))
            metadata_path = metadata_dir / f"{table_name}.md"
            metadata_path.write_text(
                metadata_markdown(table_name, dataframe, schema, output),
                encoding="utf-8",
            )
            print(f"Wrote {table_name}: {dataframe.count()} rows -> {output}")
            print(f"Wrote metadata -> {metadata_path}")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
