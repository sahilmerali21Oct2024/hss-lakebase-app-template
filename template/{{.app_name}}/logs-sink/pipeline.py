"""Lakeflow SDP that lands app audit logs into a single Delta table.

Source: system.access.app_request_logs  (Databricks Apps request logs)
Sink:   ${AUDIT_CATALOG}.audit.app_events

The apps emit one JSON object per request via AuditLoggerMiddleware. We
parse it out of the `message` column and project it into a typed table.
"""

from __future__ import annotations

import dlt
from pyspark.sql import functions as F
from pyspark.sql.types import (
    StructType,
    StructField,
    StringType,
    IntegerType,
    DoubleType,
    TimestampType,
)

EVENT_SCHEMA = StructType(
    [
        StructField("ts", StringType()),
        StructField("app", StringType()),
        StructField("user", StringType()),
        StructField("method", StringType()),
        StructField("path", StringType()),
        StructField("status", IntegerType()),
        StructField("ms", DoubleType()),
        StructField("type", StringType()),
        StructField("error", StringType()),
    ]
)


@dlt.table(
    name="app_events_raw",
    comment="Raw app request logs tailed from system.access.app_request_logs.",
)
def app_events_raw():
    return (
        spark.readStream.table("system.access.app_request_logs")
        .where(F.col("message").isNotNull())
        .where(F.col("message").startswith("{"))
    )


@dlt.table(
    name="app_events",
    comment="Structured per-app audit events, parsed from JSON middleware output.",
    table_properties={"delta.enableChangeDataFeed": "true"},
)
@dlt.expect_or_drop("valid_app", "app IS NOT NULL")
@dlt.expect_or_drop("valid_ts", "ts IS NOT NULL")
def app_events():
    parsed = (
        dlt.read_stream("app_events_raw")
        .withColumn("evt", F.from_json("message", EVENT_SCHEMA))
        .select(
            F.to_timestamp("evt.ts").alias("ts"),
            F.col("evt.app").alias("app"),
            F.col("evt.user").alias("user"),
            F.col("evt.method").alias("method"),
            F.col("evt.path").alias("path"),
            F.col("evt.status").alias("status"),
            F.col("evt.ms").alias("ms"),
            F.col("evt.type").alias("type"),
            F.col("evt.error").alias("error"),
            F.current_timestamp().alias("_ingest_ts"),
        )
    )
    return parsed


@dlt.table(
    name="permission_denies_15m",
    comment="Rolling 15-min count of permission_deny events per app — alert source.",
)
def permission_denies_15m():
    return (
        dlt.read_stream("app_events")
        .where(F.col("type") == "permission_deny")
        .withWatermark("ts", "30 minutes")
        .groupBy(F.window("ts", "15 minutes"), "app")
        .agg(F.count("*").alias("deny_count"))
        .select("window.start", "window.end", "app", "deny_count")
    )
