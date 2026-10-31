#!/usr/bin/env python3
"""Create small, deterministic Parquet fixtures and table metadata documents.

The real data directory lives on the Linux server, so this utility is intended
to be run there after PySpark is installed. The records are deliberately small
and synthetic: they exercise joins and date filtering without copying any real
customer information into the development environment.
"""

import argparse
from datetime import datetime, timedelta, timezone
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
    first_names = ["Ava", "Liam", "Mia", "Noah", "Zoe", "Ethan",
                   "Ivy", "Leo", "Nora", "Owen", "Ruby", "Theo"]
    last_names = ["Nguyen", "Patel", "Brown", "Kim", "Wilson", "Taylor",
                  "Singh", "Martin", "Chen", "Davis", "Jones", "Smith"]
    customers = spark.createDataFrame([
        (
            f"C{number:03d}",
            first_names[number - 1],
            last_names[number - 1],
            f"{number} Example Street",
            f"P{number:03d}",
            f"2025-{((number - 1) % 6) + 1:02d}-{((number - 1) % 25) + 1:02d}T09:00:00Z",
        )
        for number in range(1, 13)
    ], customer_schema)

    product_schema = StructType([
        StructField("product_id", StringType(), False),
        StructField("product_description", StringType(), False),
        StructField("product_category", StringType(), False),
    ])
    product_names = [
        ("Laptop stand", "Office"),
        ("Wireless keyboard", "Office"),
        ("Travel backpack", "Travel"),
        ("Noise cancelling headphones", "Audio"),
        ("USB-C dock", "Office"),
        ("Packing cubes", "Travel"),
        ("Web camera", "Office"),
        ("Portable speaker", "Audio"),
        ("Desk lamp", "Office"),
        ("Travel adapter", "Travel"),
        ("Microphone", "Audio"),
        ("Notebook set", "Stationery"),
    ]
    products = spark.createDataFrame([
        (f"P{number:03d}", description, category)
        for number, (description, category) in enumerate(product_names, 1)
    ], product_schema)

    transaction_schema = StructType([
        StructField("transaction_id", StringType(), False),
        StructField("customer_id", StringType(), False),
        StructField("product_id", StringType(), False),
        StructField("datetime", StringType(), False),
    ])
    transaction_start = datetime(2025, 4, 1, 9, 10, tzinfo=timezone.utc)
    # Five transactions per customer gives 60 rows and exercises repeated joins
    # across customers and products without introducing random test outcomes.
    transactions = spark.createDataFrame([
        (
            f"T{((customer_number - 1) * 5) + purchase_number:03d}",
            f"C{customer_number:03d}",
            f"P{(((customer_number + purchase_number - 2) % 12) + 1):03d}",
            (transaction_start + timedelta(
                days=((customer_number - 1) * 2) + purchase_number,
                minutes=customer_number * 7,
            )).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        for customer_number in range(1, 13)
        for purchase_number in range(1, 6)
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
