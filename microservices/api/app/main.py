import json
import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Any

import pymysql
import redis
from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, Field
from pymysql.cursors import DictCursor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "mysql"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.environ["MYSQL_USER"],
    "password": os.environ["MYSQL_PASSWORD"],
    "database": os.environ["MYSQL_DATABASE"],
    "connect_timeout": 3,
    "cursorclass": DictCursor,
    "autocommit": True,
}
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "30"))
cache = redis.Redis(
    host=os.getenv("REDIS_HOST", "redis"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    password=os.environ["REDIS_PASSWORD"],
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)
app = FastAPI(title="Items API", version="1.0.0")


class ItemInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)


def database_connection():
    return pymysql.connect(**DB_CONFIG)


def as_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "price": str(row["price"]),
        "created_at": row["created_at"].isoformat()
        if isinstance(row["created_at"], datetime)
        else row["created_at"],
    }


def read_cache(key: str) -> Any | None:
    try:
        value = cache.get(key)
        return json.loads(value) if value is not None else None
    except (redis.RedisError, json.JSONDecodeError):
        logger.warning("Redis cache read failed for %s", key)
        return None


def write_cache(key: str, value: Any) -> None:
    try:
        cache.setex(key, CACHE_TTL_SECONDS, json.dumps(value))
    except redis.RedisError:
        logger.warning("Redis cache write failed for %s", key)


def invalidate_cache(item_id: int | None = None) -> None:
    keys = ["items:all"]
    if item_id is not None:
        keys.append(f"item:{item_id}")
    try:
        cache.delete(*keys)
    except redis.RedisError:
        logger.warning("Redis cache invalidation failed")


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "items-api", "docs": "/docs"}


@app.get("/health")
def health() -> dict[str, str]:
    checks = {"mysql": "ok", "redis": "ok"}
    try:
        with database_connection() as connection:
            connection.ping(reconnect=True)
    except pymysql.MySQLError:
        logger.exception("MySQL health check failed")
        checks["mysql"] = "unavailable"
    try:
        cache.ping()
    except redis.RedisError:
        logger.exception("Redis health check failed")
        checks["redis"] = "unavailable"
    if "unavailable" in checks.values():
        raise HTTPException(status_code=503, detail=checks)
    return checks


@app.get("/items")
def list_items() -> dict[str, Any]:
    cached_items = read_cache("items:all")
    if cached_items is not None:
        return {"items": cached_items, "source": "cache"}
    try:
        with database_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, name, description, price, created_at "
                    "FROM items ORDER BY id"
                )
                items = [as_item(row) for row in cursor.fetchall()]
    except pymysql.MySQLError as exc:
        logger.exception("Failed to read items from MySQL")
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    write_cache("items:all", items)
    return {"items": items, "source": "db"}


@app.get("/items/{item_id}")
def get_item(item_id: int) -> dict[str, Any]:
    key = f"item:{item_id}"
    cached_item = read_cache(key)
    if cached_item is not None:
        return {"item": cached_item, "source": "cache"}
    try:
        with database_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, name, description, price, created_at "
                    "FROM items WHERE id = %s",
                    (item_id,),
                )
                row = cursor.fetchone()
    except pymysql.MySQLError as exc:
        logger.exception("Failed to read item from MySQL")
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Item not found")
    item = as_item(row)
    write_cache(key, item)
    return {"item": item, "source": "db"}


@app.post("/items", status_code=status.HTTP_201_CREATED)
def create_item(payload: ItemInput) -> dict[str, Any]:
    try:
        with database_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO items (name, description, price) VALUES (%s, %s, %s)",
                    (payload.name, payload.description, payload.price),
                )
                item_id = cursor.lastrowid
                cursor.execute(
                    "SELECT id, name, description, price, created_at "
                    "FROM items WHERE id = %s",
                    (item_id,),
                )
                row = cursor.fetchone()
    except pymysql.MySQLError as exc:
        logger.exception("Failed to create item in MySQL")
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    invalidate_cache(item_id)
    return as_item(row)


@app.put("/items/{item_id}")
def update_item(item_id: int, payload: ItemInput) -> dict[str, Any]:
    try:
        with database_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE items SET name = %s, description = %s, price = %s "
                    "WHERE id = %s",
                    (payload.name, payload.description, payload.price, item_id),
                )
                cursor.execute(
                    "SELECT id, name, description, price, created_at "
                    "FROM items WHERE id = %s",
                    (item_id,),
                )
                row = cursor.fetchone()
    except pymysql.MySQLError as exc:
        logger.exception("Failed to update item in MySQL")
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Item not found")
    invalidate_cache(item_id)
    return as_item(row)


@app.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int) -> Response:
    try:
        with database_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM items WHERE id = %s", (item_id,))
                deleted = cursor.rowcount
    except pymysql.MySQLError as exc:
        logger.exception("Failed to delete item from MySQL")
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Item not found")
    invalidate_cache(item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
