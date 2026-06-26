"""Smoke test the Redis URL used by ARQ and job status checks."""

import asyncio
import os
import sys
from urllib.parse import urlparse
from uuid import uuid4

import redis.asyncio as redis
from arq.connections import RedisSettings
from dotenv import load_dotenv


async def main() -> int:
    load_dotenv()
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        print("REDIS_URL is not set")
        return 1

    parsed = urlparse(redis_url)
    scheme = parsed.scheme or "<missing>"
    host = parsed.hostname or "<missing>"
    print(f"REDIS_URL scheme: {scheme}")
    print(f"REDIS_URL host: {host}")

    try:
        arq_settings = RedisSettings.from_dsn(redis_url)
    except Exception as exc:
        print(f"ARQ could not parse REDIS_URL: {exc}")
        return 1
    print(f"ARQ parse ok: ssl={bool(arq_settings.ssl)}")

    client = redis.from_url(redis_url)
    key = f"smartdigest:smoke:{uuid4().hex}"
    value = "ok"
    try:
        pong = await client.ping()
        if pong is not True:
            print("Redis PING failed")
            return 1
        await client.set(key, value, ex=30)
        actual = await client.get(key)
        if actual != value.encode("utf-8"):
            print("Redis SET/GET failed")
            return 1
        ttl = await client.ttl(key)
        print(f"Redis PING ok; SET/GET ok; smoke key ttl={ttl}s")
    finally:
        await client.delete(key)
        await client.aclose()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
