from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from pydantic import TypeAdapter, ValidationError
from typing_extensions import TypedDict
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
import asyncio
import bcrypt
import contextlib
import hashlib
import httpx
import json
import logging
import os
from math import asin, cos, radians, sin, sqrt

try:
    import redis.asyncio as redis
except ImportError:
    redis = None

try:
    import asyncpg
except ImportError:
    asyncpg = None

app = FastAPI(title="MyUber API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SECRET_KEY = "myuber-dev-secret-key-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  
INSTANCE_ID = str(uuid4())
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
REDIS_CHANNEL = os.getenv("REDIS_CHANNEL", "myuber:realtime")
REDIS_CACHE_PREFIX = os.getenv("REDIS_CACHE_PREFIX", "myuber:cache")
REDIS_CACHE_TTL_SECONDS = int(os.getenv("REDIS_CACHE_TTL_SECONDS", "900"))
DATABASE_STATE_KEY = "runtime"
DEFAULT_ADMIN_EMAIL = os.getenv("MYUBER_ADMIN_EMAIL", "admin@myuber.local")
DEFAULT_ADMIN_PASSWORD = os.getenv("MYUBER_ADMIN_PASSWORD", "admin123")
PLATFORM_FEE_RATE = 0.20
FARE_CURRENCY = "USD"
BASE_FARE = 3.50
PER_MILE_RATE = 1.65
PER_MINUTE_RATE = 0.28
MINIMUM_FARE = 7.00
SURGE_MAX_MULTIPLIER = 2.25
SURGE_RESPONSE_FACTOR = 0.35

logger = logging.getLogger("myuber")

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/riders/login", auto_error=False)

riders: dict[str, dict] = {}
drivers: dict[str, dict] = {}
admins: dict[str, dict] = {}
rides: dict[str, dict] = {}
issue_reports: dict[str, dict] = {}
notifications: dict[str, dict] = {}
trip_shares: dict[str, dict] = {}
ride_connections: dict[str, list[WebSocket]] = {}
driver_connections: dict[str, list[WebSocket]] = {}
share_connections: dict[str, list[WebSocket]] = {}
DEFAULT_DRIVER_LOCATION = {"lat": 51.5074, "lng": -0.1278}
redis_client = None
redis_subscriber_task: asyncio.Task | None = None
database_pool = None
database_available = False
postgis_available = False

RIDE_STATUSES = {
    "matching",
    "pending_driver",
    "accepted",
    "arrived",
    "in_progress",
    "completed",
    "cancelled",
    "no_drivers_available",
}
TERMINAL_RIDE_STATUSES = {"completed", "cancelled"}
DRIVER_PROGRESS_STATUSES = {"arrived", "in_progress", "completed"}
RIDER_CANCELABLE_STATUSES = {
    "matching",
    "pending_driver",
    "accepted",
    "arrived",
    "no_drivers_available",
}
DRIVER_CANCELABLE_STATUSES = {"accepted", "arrived"}
ALLOWED_RIDE_TRANSITIONS = {
    "matching": {"pending_driver", "no_drivers_available", "cancelled"},
    "no_drivers_available": {"matching", "pending_driver", "cancelled"},
    "pending_driver": {"matching", "accepted", "no_drivers_available", "cancelled"},
    "accepted": {"arrived", "cancelled"},
    "arrived": {"in_progress", "cancelled"},
    "in_progress": {"completed"},
    "completed": set(),
    "cancelled": set(),
}
RIDE_STATUS_TIMESTAMPS = {
    "pending_driver": "matched_at",
    "accepted": "accepted_at",
    "arrived": "arrived_at",
    "in_progress": "started_at",
    "completed": "completed_at",
    "cancelled": "cancelled_at",
    "no_drivers_available": "no_drivers_available_at",
}
ACCOUNT_STATUSES = {"active", "suspended"}

Location = TypedDict("Location", {"lat": float, "lng": float})

RiderSignup = TypedDict(
    "RiderSignup",
    {"email": str, "password": str, "name": str, "phone": str},
)

DriverSignup = TypedDict(
    "DriverSignup",
    {
        "email": str,
        "password": str,
        "name": str,
        "phone": str,
        "vehicle_make": str,
        "vehicle_model": str,
        "vehicle_year": int,
        "vehicle_color": str,
        "vehicle_plate": str,
        "license_number": str,
    },
)

LoginRequest = TypedDict("LoginRequest", {"email": str, "password": str})

RideRequest = TypedDict(
    "RideRequest",
    {"rider_id": str, "pickup": Location, "destination": Location},
)

RouteEstimateRequest = TypedDict(
    "RouteEstimateRequest",
    {"pickup": Location, "destination": Location},
)

DriverLocationRequest = TypedDict("DriverLocationRequest", {"location": Location})
DriverAvailabilityRequest = TypedDict("DriverAvailabilityRequest", {"online": bool})
RiderLocationRequest = TypedDict("RiderLocationRequest", {"location": Location})
RideStatusUpdateRequest = TypedDict("RideStatusUpdateRequest", {"status": str})
RideRatingRequest = TypedDict("RideRatingRequest", {"score": int, "comment": str})
RideIssueReportRequest = TypedDict(
    "RideIssueReportRequest",
    {"category": str, "description": str},
)
NotificationReadRequest = TypedDict("NotificationReadRequest", {"read": bool})
AdminUserStatusRequest = TypedDict("AdminUserStatusRequest", {"status": str})

location_adapter = TypeAdapter(Location)

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_default_admin() -> None:
    if admins:
        return

    admin_id = "admin_default"
    admins[admin_id] = {
        "id": admin_id,
        "email": DEFAULT_ADMIN_EMAIL,
        "password_hash": hash_password(DEFAULT_ADMIN_PASSWORD),
        "name": "MyUber Admin",
        "phone": "",
        "role": "admin",
        "account_status": "active",
        "created_at": now_iso(),
    }


ensure_default_admin()


def runtime_state_payload() -> dict:
    return {
        "riders": riders,
        "drivers": drivers,
        "admins": admins,
        "rides": rides,
        "issue_reports": issue_reports,
        "notifications": notifications,
        "trip_shares": trip_shares,
    }


def restore_runtime_state(payload: dict) -> None:
    stores = {
        "riders": riders,
        "drivers": drivers,
        "admins": admins,
        "rides": rides,
        "issue_reports": issue_reports,
        "notifications": notifications,
        "trip_shares": trip_shares,
    }

    for key, store in stores.items():
        restored = payload.get(key)
        if isinstance(restored, dict):
            store.clear()
            store.update(restored)

    ensure_default_admin()


async def initialize_database() -> None:
    global database_pool, database_available, postgis_available

    if not DATABASE_URL:
        return
    if asyncpg is None:
        logger.warning("DATABASE_URL is set but asyncpg is not installed")
        return

    try:
        database_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        async with database_pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS myuber_runtime_state (
                    id TEXT PRIMARY KEY,
                    data JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

            try:
                await connection.execute("CREATE EXTENSION IF NOT EXISTS postgis")
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS myuber_driver_locations (
                        driver_id TEXT PRIMARY KEY,
                        location GEOGRAPHY(Point, 4326) NOT NULL,
                        lat DOUBLE PRECISION NOT NULL,
                        lng DOUBLE PRECISION NOT NULL,
                        availability TEXT NOT NULL,
                        current_ride_id TEXT,
                        account_status TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                await connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS myuber_driver_locations_geo_idx
                    ON myuber_driver_locations
                    USING GIST (location)
                    """
                )
                postgis_available = True
            except Exception:
                postgis_available = False
                logger.exception("PostGIS setup failed; using in-memory distance sorting")

        database_available = True
        await load_runtime_state_from_database()
        await persist_runtime_state()
    except Exception:
        database_pool = None
        database_available = False
        postgis_available = False
        logger.exception("Could not connect to PostgreSQL; using in-memory state")


async def load_runtime_state_from_database() -> None:
    if not database_pool:
        return

    async with database_pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT data FROM myuber_runtime_state WHERE id = $1",
            DATABASE_STATE_KEY,
        )

    if not row:
        return

    data = row["data"]
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict):
        restore_runtime_state(data)


async def sync_driver_location_index(connection=None) -> None:
    if not postgis_available:
        return

    rows = []
    for driver in drivers.values():
        location = parse_location_payload(driver.get("location"))
        if not location:
            continue

        rows.append(
            (
                driver["id"],
                float(location["lng"]),
                float(location["lat"]),
                driver.get("availability") or "offline",
                driver.get("current_ride_id"),
                normalized_account_status(driver),
                driver.get("last_location_at") or driver.get("updated_at") or now_iso(),
            )
        )

    async def write_rows(active_connection) -> None:
        await active_connection.execute("DELETE FROM myuber_driver_locations")
        if rows:
            await active_connection.executemany(
                """
                INSERT INTO myuber_driver_locations (
                    driver_id,
                    location,
                    lng,
                    lat,
                    availability,
                    current_ride_id,
                    account_status,
                    updated_at
                )
                VALUES (
                    $1,
                    ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography,
                    $2,
                    $3,
                    $4,
                    $5,
                    $6,
                    $7::timestamptz
                )
                ON CONFLICT (driver_id) DO UPDATE SET
                    location = EXCLUDED.location,
                    lng = EXCLUDED.lng,
                    lat = EXCLUDED.lat,
                    availability = EXCLUDED.availability,
                    current_ride_id = EXCLUDED.current_ride_id,
                    account_status = EXCLUDED.account_status,
                    updated_at = EXCLUDED.updated_at
                """,
                rows,
            )

    if connection:
        await write_rows(connection)
        return

    if database_pool:
        async with database_pool.acquire() as active_connection:
            await write_rows(active_connection)


async def persist_runtime_state() -> None:
    if not database_pool:
        return

    try:
        async with database_pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO myuber_runtime_state (id, data, updated_at)
                VALUES ($1, $2::jsonb, now())
                ON CONFLICT (id) DO UPDATE SET
                    data = EXCLUDED.data,
                    updated_at = now()
                """,
                DATABASE_STATE_KEY,
                json.dumps(runtime_state_payload()),
            )
            await sync_driver_location_index(connection)
    except Exception:
        logger.exception("Failed to persist runtime state to PostgreSQL")


async def close_database() -> None:
    global database_pool, database_available, postgis_available

    if database_pool:
        await database_pool.close()
    database_pool = None
    database_available = False
    postgis_available = False


def cache_key(namespace: str, payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"{REDIS_CACHE_PREFIX}:{namespace}:{digest}"


async def cache_get_json(key: str) -> dict | list | None:
    if not redis_client:
        return None

    try:
        cached = await redis_client.get(key)
    except Exception:
        logger.exception("Redis cache read failed")
        return None

    if not cached:
        return None

    try:
        return json.loads(cached)
    except json.JSONDecodeError:
        return None


async def cache_set_json(key: str, value: dict | list, ttl_seconds: int = REDIS_CACHE_TTL_SECONDS) -> None:
    if not redis_client:
        return

    try:
        await redis_client.set(key, json.dumps(value), ex=ttl_seconds)
    except Exception:
        logger.exception("Redis cache write failed")


def storage_status() -> dict:
    return {
        "postgres": {
            "configured": bool(DATABASE_URL),
            "connected": database_available,
        },
        "postgis": {
            "enabled": postgis_available,
        },
        "redis": {
            "configured": bool(REDIS_URL),
            "connected": redis_client is not None,
            "cache_ttl_seconds": REDIS_CACHE_TTL_SECONDS,
        },
    }


def normalized_account_status(user: dict | None) -> str:
    if not user:
        return "active"
    status = user.get("account_status") or "active"
    if status not in ACCOUNT_STATUSES:
        status = "active"
    user["account_status"] = status
    return status


def is_account_active(user: dict | None) -> bool:
    return normalized_account_status(user) == "active"


def ensure_account_active(user: dict | None) -> None:
    if not is_account_active(user):
        raise HTTPException(status_code=403, detail="Account is suspended")


def format_status(status: str | None) -> str:
    if not status:
        return "unknown"
    return status.replace("_", " ")


def assert_valid_ride_status(status: str) -> None:
    if status not in RIDE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown ride status: {status}")


def set_ride_status(
    ride: dict,
    next_status: str,
    actor: str,
    *,
    allow_same: bool = False,
) -> bool:
    assert_valid_ride_status(next_status)
    previous_status = ride.get("status")

    if previous_status == next_status:
        if allow_same:
            ride["updated_at"] = now_iso()
            return False
        raise HTTPException(
            status_code=409,
            detail=f"Ride is already {format_status(next_status)}",
        )

    allowed = ALLOWED_RIDE_TRANSITIONS.get(previous_status, set())
    if next_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot transition ride from "
                f"{format_status(previous_status)} to {format_status(next_status)}"
            ),
        )

    changed_at = now_iso()
    ride["status"] = next_status
    ride["updated_at"] = changed_at

    timestamp_field = RIDE_STATUS_TIMESTAMPS.get(next_status)
    if timestamp_field:
        ride[timestamp_field] = changed_at

    ride.setdefault("status_history", []).append(
        {
            "from": previous_status,
            "status": next_status,
            "actor": actor,
            "at": changed_at,
        }
    )
    return True


def release_driver_for_ride(ride: dict, availability: str = "available") -> None:
    driver_id = ride.get("driver_id")
    driver = drivers.get(driver_id) if driver_id else None
    if not driver:
        return
    if driver.get("current_ride_id") == ride.get("id"):
        driver["current_ride_id"] = None
    if driver.get("availability") != "offline":
        driver["availability"] = availability


def create_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_auth_token(token: str | None) -> dict:
    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        role: str = payload.get("role")
        if user_id is None or role is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return {"user_id": user_id, "role": role}
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(token: str = Depends(oauth2_scheme)):
    return decode_auth_token(token)


async def authenticate_socket(websocket: WebSocket, token: str | None) -> dict | None:
    try:
        return decode_auth_token(token)
    except HTTPException:
        await websocket.accept()
        await websocket.close(code=1008)
        return None


def haversine_km(start: Location, end: Location) -> float:
    earth_radius_km = 6371.0
    lat1 = radians(start["lat"])
    lng1 = radians(start["lng"])
    lat2 = radians(end["lat"])
    lng2 = radians(end["lng"])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * earth_radius_km * asin(sqrt(a))


def active_demand_count() -> int:
    return sum(
        1
        for ride in rides.values()
        if ride.get("status") in {"matching", "pending_driver", "no_drivers_available"}
    )


def available_driver_count() -> int:
    return sum(
        1
        for driver in drivers.values()
        if is_account_active(driver)
        and driver.get("availability") == "available"
        and not driver.get("current_ride_id")
        and parse_location_payload(driver.get("location"))
    )


def demand_level_for(multiplier: float) -> str:
    if multiplier >= 1.75:
        return "peak"
    if multiplier >= 1.35:
        return "busy"
    if multiplier > 1:
        return "elevated"
    return "normal"


def surge_pricing_state() -> dict:
    demand = active_demand_count()
    supply = available_driver_count()
    projected_demand = demand + 1

    if supply == 0 and demand == 0:
        multiplier = 1.0
    else:
        demand_ratio = projected_demand / max(supply, 1)
        multiplier = 1 + max(0, demand_ratio - 1) * SURGE_RESPONSE_FACTOR
        multiplier = min(SURGE_MAX_MULTIPLIER, multiplier)
        multiplier = round(multiplier * 20) / 20

    multiplier = round(max(1.0, multiplier), 2)
    return {
        "surge_multiplier": multiplier,
        "demand_level": demand_level_for(multiplier),
        "surge_reason": (
            "More ride requests than available drivers"
            if multiplier > 1
            else "Standard demand"
        ),
        "active_demand": demand,
        "available_drivers": supply,
    }


def fare_breakdown_for(distance_km: float, duration_min: float) -> dict:
    distance_miles = max(0, distance_km) * 0.621371
    billable_minutes = max(0, duration_min)
    distance_charge = round(distance_miles * PER_MILE_RATE, 2)
    time_charge = round(billable_minutes * PER_MINUTE_RATE, 2)
    subtotal = round(BASE_FARE + distance_charge + time_charge, 2)
    surge = surge_pricing_state()
    surge_multiplier = surge["surge_multiplier"]
    surge_charge = round(subtotal * (surge_multiplier - 1), 2)
    surged_subtotal = round(subtotal + surge_charge, 2)
    minimum_adjustment = round(max(MINIMUM_FARE - surged_subtotal, 0), 2)
    total = round(surged_subtotal + minimum_adjustment, 2)
    return {
        "currency": FARE_CURRENCY,
        "base_fare": BASE_FARE,
        "distance_charge": distance_charge,
        "time_charge": time_charge,
        "subtotal": subtotal,
        "surge_multiplier": surge_multiplier,
        "surge_charge": surge_charge,
        "surge_reason": surge["surge_reason"],
        "demand_level": surge["demand_level"],
        "active_demand": surge["active_demand"],
        "available_drivers": surge["available_drivers"],
        "minimum_adjustment": minimum_adjustment,
        "total": total,
        "distance_miles": round(distance_miles, 2),
        "duration_min": max(1, round(billable_minutes)),
        "distance_rate_per_mile": PER_MILE_RATE,
        "time_rate_per_minute": PER_MINUTE_RATE,
        "minimum_fare": MINIMUM_FARE,
    }


def fare_for(distance_km: float, duration_min: float) -> float:
    return fare_breakdown_for(distance_km, duration_min)["total"]


def create_mock_payment(
    ride_id: str,
    amount: float,
    created_at: str,
    fare_breakdown: dict | None = None,
) -> dict:
    payment_id = f"mock_{uuid4().hex[:12]}"
    return {
        "id": payment_id,
        "ride_id": ride_id,
        "amount": amount,
        "currency": FARE_CURRENCY,
        "method": "Mock Visa ending 4242",
        "status": "authorized",
        "authorization_code": f"AUTH-{uuid4().hex[:8].upper()}",
        "authorized_at": created_at,
        "captured_at": None,
        "voided_at": None,
        "refunded_at": None,
        "receipt_number": None,
        "fare_breakdown": fare_breakdown,
    }


def capture_mock_payment(ride: dict) -> None:
    payment = ride.get("payment")
    if not payment or payment.get("status") == "paid":
        return

    captured_at = now_iso()
    payment["status"] = "paid"
    payment["captured_at"] = captured_at
    payment["receipt_number"] = f"RCPT-{uuid4().hex[:10].upper()}"
    payment["voided_at"] = None
    ride["payment"] = payment
    ride["updated_at"] = captured_at


def void_mock_payment(ride: dict, reason: str) -> None:
    payment = ride.get("payment")
    if not payment or payment.get("status") in {"paid", "voided", "refunded"}:
        return

    voided_at = now_iso()
    payment["status"] = "voided"
    payment["voided_at"] = voided_at
    payment["void_reason"] = reason
    ride["payment"] = payment
    ride["updated_at"] = voided_at


def refund_mock_payment(ride: dict, reason: str) -> bool:
    payment = ride.get("payment")
    if not payment:
        raise HTTPException(status_code=409, detail="Ride does not have a payment")
    if payment.get("status") == "refunded":
        return False
    if payment.get("status") != "paid":
        raise HTTPException(status_code=409, detail="Only paid rides can be refunded")

    refunded_at = now_iso()
    refund = {
        "id": f"rfnd_{uuid4().hex[:12]}",
        "ride_id": ride["id"],
        "payment_id": payment["id"],
        "amount": round(float(payment.get("amount") or 0), 2),
        "currency": payment.get("currency", FARE_CURRENCY),
        "status": "succeeded",
        "reason": reason,
        "created_at": refunded_at,
    }
    payment["status"] = "refunded"
    payment["refunded_at"] = refunded_at
    payment["refund"] = refund
    ride["payment"] = payment
    ride["refund"] = refund
    ride["updated_at"] = refunded_at
    return True


def build_route_estimate(
    distance_km: float,
    duration_min: float,
    route: list[dict],
    source: str,
    steps: list[dict] | None = None,
) -> dict:
    fare_breakdown = fare_breakdown_for(distance_km, duration_min)
    return {
        "distance_km": round(distance_km, 2),
        "duration_min": max(1, round(duration_min)),
        "currency": FARE_CURRENCY,
        "fare": fare_breakdown["total"],
        "fare_breakdown": fare_breakdown,
        "route": route,
        "steps": steps or [],
        "source": source,
    }


def fallback_route_estimate(pickup: Location, destination: Location) -> dict:
    straight_line_km = haversine_km(pickup, destination)
    driving_distance_km = straight_line_km * 1.28
    duration_min = (driving_distance_km / 32) * 60
    route = [
        {"lat": pickup["lat"], "lng": pickup["lng"]},
        {"lat": destination["lat"], "lng": destination["lng"]},
    ]
    return build_route_estimate(
        driving_distance_km,
        duration_min,
        route,
        "fallback",
        [
            {
                "instruction": "Head toward the destination",
                "distance_km": round(driving_distance_km, 2),
                "duration_min": max(1, round(duration_min)),
                "location": route[0],
            },
            {
                "instruction": "Arrive at the destination",
                "distance_km": 0,
                "duration_min": 0,
                "location": route[-1],
            },
        ],
    )


def rounded_location_for_cache(location: Location) -> dict:
    return {
        "lat": round(float(location["lat"]), 5),
        "lng": round(float(location["lng"]), 5),
    }


def cached_route_payload(estimate: dict) -> dict:
    return {
        "distance_km": estimate["distance_km"],
        "duration_min": estimate["duration_min"],
        "route": estimate["route"],
        "source": estimate["source"],
        "steps": estimate.get("steps", []),
    }


def route_estimate_from_cached_payload(payload: dict) -> dict | None:
    try:
        return build_route_estimate(
            float(payload["distance_km"]),
            float(payload["duration_min"]),
            list(payload["route"]),
            str(payload.get("source") or "cache"),
            list(payload.get("steps") or []),
        )
    except (KeyError, TypeError, ValueError):
        return None


def route_point_from_osrm_location(location: list | tuple | None) -> dict | None:
    if not isinstance(location, (list, tuple)) or len(location) < 2:
        return None
    lng, lat = location[:2]
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return None
    return {"lat": lat, "lng": lng}


def road_suffix(name: str | None) -> str:
    clean_name = (name or "").strip()
    return f" onto {clean_name}" if clean_name else ""


def direction_phrase(modifier: str | None) -> str:
    if not modifier:
        return ""
    return modifier.replace("slight ", "slightly ")


def instruction_for_osrm_step(step: dict) -> str:
    maneuver = step.get("maneuver") or {}
    step_type = maneuver.get("type")
    modifier = direction_phrase(maneuver.get("modifier"))
    road = road_suffix(step.get("name"))

    if step_type == "depart":
        direction = f" {modifier}" if modifier else ""
        return f"Head{direction}{road}".strip()
    if step_type == "arrive":
        return "Arrive at the destination"
    if step_type == "turn":
        direction = modifier or "ahead"
        return f"Turn {direction}{road}"
    if step_type in {"new name", "continue"}:
        return f"Continue{road}" if road else "Continue straight"
    if step_type == "merge":
        direction = f" {modifier}" if modifier else ""
        return f"Merge{direction}{road}".strip()
    if step_type in {"on ramp", "off ramp"}:
        direction = f" {modifier}" if modifier else ""
        ramp = "Take the ramp" if step_type == "on ramp" else "Take the exit"
        return f"{ramp}{direction}{road}".strip()
    if step_type == "fork":
        direction = modifier or "ahead"
        return f"Keep {direction}{road}"
    if step_type == "end of road":
        direction = modifier or "ahead"
        return f"At the end of the road, turn {direction}{road}"
    if step_type in {"roundabout", "rotary"}:
        exit_number = maneuver.get("exit")
        if exit_number:
            return f"At the roundabout, take exit {exit_number}{road}"
        return f"Enter the roundabout{road}"
    if step_type == "notification":
        return f"Continue{road}" if road else "Continue"

    return f"Continue{road}" if road else "Proceed to the next step"


def steps_from_osrm_legs(legs: list) -> list[dict]:
    route_steps: list[dict] = []

    for leg in legs:
        for step in leg.get("steps", []):
            location = route_point_from_osrm_location((step.get("maneuver") or {}).get("location"))
            distance_km = (step.get("distance") or 0) / 1000
            duration_min = (step.get("duration") or 0) / 60
            route_steps.append(
                {
                    "instruction": instruction_for_osrm_step(step),
                    "distance_km": round(distance_km, 2),
                    "duration_min": max(0, round(duration_min)),
                    "location": location,
                }
            )

    return route_steps


def rating_summary_for_user(user_id: str) -> dict:
    scores: list[int] = []
    for ride in rides.values():
        for rating in (ride.get("ratings") or {}).values():
            if rating.get("to_user_id") == user_id:
                scores.append(int(rating.get("score", 0)))

    if not scores:
        return {"average_rating": None, "rating_count": 0}

    return {
        "average_rating": round(sum(scores) / len(scores), 1),
        "rating_count": len(scores),
    }


def public_driver(driver: dict | None) -> dict | None:
    if not driver:
        return None
    rating_summary = rating_summary_for_user(driver["id"])
    return {
        "id": driver["id"],
        "name": driver["name"],
        "phone": driver["phone"],
        "vehicle": driver.get("vehicle"),
        "location": driver.get("location"),
        **rating_summary,
    }


def public_ride(ride: dict) -> dict:
    driver = drivers.get(ride.get("driver_id"))
    serialized = {
        key: value
        for key, value in ride.items()
        if key != "declined_driver_ids"
    }
    serialized["driver"] = public_driver(driver)
    return serialized


def public_shared_driver(driver: dict | None) -> dict | None:
    if not driver:
        return None
    rating_summary = rating_summary_for_user(driver["id"])
    return {
        "name": driver["name"],
        "vehicle": driver.get("vehicle"),
        "location": driver.get("location"),
        **rating_summary,
    }


def public_shared_ride(ride: dict) -> dict:
    driver = drivers.get(ride.get("driver_id"))
    return {
        "id": ride["id"],
        "status": ride.get("status"),
        "pickup": ride.get("pickup"),
        "destination": ride.get("destination"),
        "distance_km": ride.get("distance_km"),
        "duration_min": ride.get("duration_min"),
        "driver_location": ride.get("driver_location"),
        "rider_location": ride.get("rider_location"),
        "driver": public_shared_driver(driver),
        "created_at": ride.get("created_at"),
        "updated_at": ride.get("updated_at"),
        "matched_at": ride.get("matched_at"),
        "accepted_at": ride.get("accepted_at"),
        "arrived_at": ride.get("arrived_at"),
        "started_at": ride.get("started_at"),
        "completed_at": ride.get("completed_at"),
        "cancelled_at": ride.get("cancelled_at"),
    }


def safe_user(user: dict) -> dict:
    normalized_account_status(user)
    serialized = {k: v for k, v in user.items() if k != "password_hash"}
    serialized.update(rating_summary_for_user(user["id"]))
    return serialized


def require_role(current_user: dict, role: str) -> str:
    if current_user["role"] != role:
        raise HTTPException(status_code=403, detail=f"{role.title()} account required")
    return current_user["user_id"]


def can_view_ride(current_user: dict, ride: dict) -> bool:
    if current_user["role"] == "admin":
        return True
    if current_user["role"] == "rider":
        return ride.get("rider_id") == current_user["user_id"]
    if current_user["role"] == "driver":
        return ride.get("driver_id") == current_user["user_id"]
    return False


def rating_target_for(current_user: dict, ride: dict) -> tuple[str, str]:
    actor_role = current_user["role"]
    actor_id = current_user["user_id"]

    if actor_role == "rider" and ride.get("rider_id") == actor_id:
        driver_id = ride.get("driver_id")
        if not driver_id:
            raise HTTPException(status_code=409, detail="Ride has no driver to rate")
        return driver_id, "driver"

    if actor_role == "driver" and ride.get("driver_id") == actor_id:
        return ride["rider_id"], "rider"

    raise HTTPException(status_code=403, detail="Cannot rate this ride")


def normalized_rating_score(value: int) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Rating score must be 1 to 5")
    if score < 1 or score > 5:
        raise HTTPException(status_code=400, detail="Rating score must be 1 to 5")
    return score


def normalized_feedback_text(value: str, field: str, max_length: int) -> str:
    text = (value or "").strip()
    if len(text) > max_length:
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be {max_length} characters or fewer",
        )
    return text


def ride_history_for_user(current_user: dict) -> list[dict]:
    user_id = current_user["user_id"]
    role = current_user["role"]
    if role == "rider":
        matching_rides = [ride for ride in rides.values() if ride.get("rider_id") == user_id]
    elif role == "driver":
        matching_rides = [ride for ride in rides.values() if ride.get("driver_id") == user_id]
    else:
        matching_rides = []

    return [
        public_ride(ride)
        for ride in sorted(
            matching_rides,
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )
    ]


def notification_for_user(notification: dict) -> dict:
    return {
        "id": notification["id"],
        "user_id": notification["user_id"],
        "role": notification["role"],
        "kind": notification["kind"],
        "title": notification["title"],
        "body": notification["body"],
        "ride_id": notification.get("ride_id"),
        "read_at": notification.get("read_at"),
        "created_at": notification["created_at"],
    }


def create_notification(
    user_id: str,
    role: str,
    title: str,
    body: str,
    kind: str,
    ride_id: str | None = None,
) -> dict:
    notification_id = f"note_{uuid4().hex[:12]}"
    notification = {
        "id": notification_id,
        "user_id": user_id,
        "role": role,
        "kind": kind,
        "title": title,
        "body": body,
        "ride_id": ride_id,
        "read_at": None,
        "created_at": now_iso(),
    }
    notifications[notification_id] = notification
    return notification


def notifications_for_current_user(current_user: dict) -> list[dict]:
    user_id = current_user["user_id"]
    role = current_user["role"]
    matching_notifications = [
        notification
        for notification in notifications.values()
        if notification.get("user_id") == user_id and notification.get("role") == role
    ]
    return [
        notification_for_user(notification)
        for notification in sorted(
            matching_notifications,
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )
    ]


def unread_notifications_count(user_id: str, role: str) -> int:
    return sum(
        1
        for notification in notifications.values()
        if notification.get("user_id") == user_id
        and notification.get("role") == role
        and not notification.get("read_at")
    )


async def notify_rider(ride: dict, title: str, body: str, kind: str) -> None:
    rider_id = ride.get("rider_id")
    if not rider_id:
        return
    notification = create_notification(rider_id, "rider", title, body, kind, ride.get("id"))
    await fanout_realtime_event(
        "ride",
        ride["id"],
        {
            "type": "notification",
            "notification": notification_for_user(notification),
            "unread_count": unread_notifications_count(rider_id, "rider"),
        },
    )


async def notify_driver(driver_id: str, title: str, body: str, kind: str, ride_id: str | None = None) -> None:
    notification = create_notification(driver_id, "driver", title, body, kind, ride_id)
    await broadcast_driver(
        driver_id,
        {
            "type": "notification",
            "notification": notification_for_user(notification),
            "unread_count": unread_notifications_count(driver_id, "driver"),
        },
    )


def rider_name_for_ride(ride: dict) -> str:
    rider = riders.get(ride.get("rider_id"))
    return (rider or {}).get("name", "Rider")


def user_summary(user: dict | None) -> dict | None:
    if not user:
        return None
    summary = safe_user(user)
    return {
        "id": summary["id"],
        "name": summary["name"],
        "email": summary["email"],
        "phone": summary.get("phone", ""),
        "role": summary["role"],
        "average_rating": summary.get("average_rating"),
        "rating_count": summary.get("rating_count"),
    }


def admin_ride_summary(ride: dict) -> dict:
    payment = ride.get("payment") or {}
    return {
        "id": ride["id"],
        "status": ride.get("status"),
        "rider": user_summary(riders.get(ride.get("rider_id"))),
        "driver": user_summary(drivers.get(ride.get("driver_id"))),
        "pickup": ride.get("pickup"),
        "destination": ride.get("destination"),
        "driver_location": ride.get("driver_location"),
        "rider_location": ride.get("rider_location"),
        "distance_km": ride.get("distance_km"),
        "duration_min": ride.get("duration_min"),
        "fare": ride.get("fare"),
        "currency": ride.get("currency", FARE_CURRENCY),
        "payment_status": payment.get("status"),
        "created_at": ride.get("created_at"),
        "updated_at": ride.get("updated_at"),
    }


def admin_driver_summary(driver: dict) -> dict:
    earnings = build_driver_earnings(driver["id"])
    rating_summary = rating_summary_for_user(driver["id"])
    return {
        "id": driver["id"],
        "name": driver["name"],
        "email": driver["email"],
        "phone": driver.get("phone", ""),
        "account_status": normalized_account_status(driver),
        "availability": driver.get("availability"),
        "current_ride_id": driver.get("current_ride_id"),
        "location": driver.get("location"),
        "vehicle": driver.get("vehicle"),
        "onboarding_status": driver.get("onboarding_status"),
        "acceptance_rate": earnings["acceptance_rate"],
        "today_net": earnings["today_net"],
        "completed_rides": earnings["completed_rides"],
        "average_rating": rating_summary.get("average_rating"),
        "rating_count": rating_summary.get("rating_count"),
        "created_at": driver.get("created_at"),
    }


def admin_user_summary(user: dict) -> dict:
    summary = safe_user(user)
    role = summary.get("role")
    result = {
        "id": summary["id"],
        "name": summary["name"],
        "email": summary["email"],
        "phone": summary.get("phone", ""),
        "role": role,
        "account_status": summary.get("account_status", "active"),
        "average_rating": summary.get("average_rating"),
        "rating_count": summary.get("rating_count"),
        "created_at": summary.get("created_at"),
    }

    if role == "driver":
        earnings = build_driver_earnings(summary["id"])
        result.update(
            {
                "availability": summary.get("availability"),
                "current_ride_id": summary.get("current_ride_id"),
                "vehicle": summary.get("vehicle"),
                "location": summary.get("location"),
                "onboarding_status": summary.get("onboarding_status"),
                "acceptance_rate": earnings["acceptance_rate"],
                "today_net": earnings["today_net"],
                "completed_rides": earnings["completed_rides"],
            }
        )
    elif role == "rider":
        user_rides = [ride for ride in rides.values() if ride.get("rider_id") == summary["id"]]
        result.update(
            {
                "total_rides": len(user_rides),
                "completed_rides": sum(1 for ride in user_rides if ride.get("status") == "completed"),
            }
        )

    return result


def admin_issue_summary(report: dict) -> dict:
    ride = rides.get(report.get("ride_id"))
    return {
        **report,
        "ride": admin_ride_summary(ride) if ride else None,
    }


def percentage(part: int | float, total: int | float) -> float:
    if not total:
        return 0.0
    return round((part / total) * 100, 1)


def build_admin_analytics(
    *,
    completed_rides: list[dict],
    active_rides: list[dict],
    payments: list[dict],
    open_issues: list[dict],
) -> dict:
    ride_count = len(rides)
    completed_count = len(completed_rides)
    cancelled_count = sum(1 for ride in rides.values() if ride.get("status") == "cancelled")
    no_driver_count = sum(1 for ride in rides.values() if ride.get("status") == "no_drivers_available")
    paid_count = sum(1 for payment in payments if payment.get("status") == "paid")
    refunded_count = sum(1 for payment in payments if payment.get("status") == "refunded")
    valued_payments = [float(payment.get("amount") or 0) for payment in payments if payment.get("amount") is not None]
    driver_decisions = [
        (driver.get("stats") or {}).get("accepted", 0) + (driver.get("stats") or {}).get("rejected", 0)
        for driver in drivers.values()
    ]
    accepted_decisions = [
        (driver.get("stats") or {}).get("accepted", 0)
        for driver in drivers.values()
    ]
    total_decisions = sum(driver_decisions)
    active_drivers = sum(1 for driver in drivers.values() if is_account_active(driver))
    online_drivers = sum(
        1
        for driver in drivers.values()
        if is_account_active(driver) and driver.get("availability") != "offline"
    )

    status_counts = {
        status: sum(1 for ride in rides.values() if ride.get("status") == status)
        for status in sorted(RIDE_STATUSES)
    }
    payment_status_counts = {
        status: sum(1 for payment in payments if payment.get("status") == status)
        for status in sorted({str(payment.get("status") or "pending") for payment in payments})
    }
    completed_distances = [
        float(ride.get("distance_km") or 0)
        for ride in completed_rides
        if ride.get("distance_km") is not None
    ]
    completed_durations = [
        float(ride.get("duration_min") or 0)
        for ride in completed_rides
        if ride.get("duration_min") is not None
    ]

    return {
        "completion_rate": percentage(completed_count, ride_count),
        "cancellation_rate": percentage(cancelled_count, ride_count),
        "no_driver_rate": percentage(no_driver_count, ride_count),
        "paid_conversion_rate": percentage(paid_count, ride_count),
        "refund_rate": percentage(refunded_count, max(paid_count + refunded_count, 1)),
        "driver_acceptance_rate": percentage(sum(accepted_decisions), total_decisions) if total_decisions else 100.0,
        "online_driver_rate": percentage(online_drivers, active_drivers),
        "issue_rate": percentage(len(open_issues), ride_count),
        "average_fare": round(sum(valued_payments) / len(valued_payments), 2) if valued_payments else 0,
        "average_trip_distance_km": round(sum(completed_distances) / len(completed_distances), 2) if completed_distances else 0,
        "average_trip_duration_min": round(sum(completed_durations) / len(completed_durations), 1) if completed_durations else 0,
        "active_supply_gap": max(0, len(active_rides) - available_driver_count()),
        "status_counts": status_counts,
        "payment_status_counts": payment_status_counts,
    }


def build_admin_dashboard() -> dict:
    completed_rides = [ride for ride in rides.values() if ride.get("status") == "completed"]
    active_rides = [
        ride
        for ride in rides.values()
        if ride.get("status") not in TERMINAL_RIDE_STATUSES
    ]
    payments = [ride.get("payment") or {} for ride in rides.values()]
    gross_total = round(
        sum(
            float(payment.get("amount") or 0)
            for payment in payments
            if payment.get("status") in {"paid", "authorized"}
        ),
        2,
    )
    paid_total = round(
        sum(float(payment.get("amount") or 0) for payment in payments if payment.get("status") == "paid"),
        2,
    )
    refund_total = round(
        sum(float((payment.get("refund") or {}).get("amount") or 0) for payment in payments),
        2,
    )
    recent_rides = sorted(rides.values(), key=lambda ride: ride.get("created_at", ""), reverse=True)[:8]
    open_issues = [
        report
        for report in issue_reports.values()
        if report.get("status") == "open"
    ]
    active_drivers = [
        driver
        for driver in drivers.values()
        if is_account_active(driver)
    ]

    return {
        "generated_at": now_iso(),
        "currency": FARE_CURRENCY,
        "totals": {
            "riders": len(riders),
            "drivers": len(drivers),
            "rides": len(rides),
            "active_rides": len(active_rides),
            "completed_rides": len(completed_rides),
            "cancelled_rides": sum(1 for ride in rides.values() if ride.get("status") == "cancelled"),
            "open_issues": len(open_issues),
            "online_drivers": sum(
                1
                for driver in active_drivers
                if driver.get("availability") != "offline"
            ),
        },
        "revenue": {
            "gross_total": gross_total,
            "paid_total": paid_total,
            "platform_fee_total": round(paid_total * PLATFORM_FEE_RATE, 2),
            "refund_total": refund_total,
            "platform_fee_rate": PLATFORM_FEE_RATE,
        },
        "demand": surge_pricing_state(),
        "analytics": build_admin_analytics(
            completed_rides=completed_rides,
            active_rides=active_rides,
            payments=payments,
            open_issues=open_issues,
        ),
        "active_rides": [
            admin_ride_summary(ride)
            for ride in sorted(active_rides, key=lambda item: item.get("updated_at", ""), reverse=True)
        ],
        "recent_rides": [admin_ride_summary(ride) for ride in recent_rides],
        "drivers": [
            admin_driver_summary(driver)
            for driver in sorted(drivers.values(), key=lambda item: item.get("created_at", ""), reverse=True)
        ],
        "users": [
            admin_user_summary(user)
            for user in sorted(
                list(riders.values()) + list(drivers.values()),
                key=lambda item: item.get("created_at", ""),
                reverse=True,
            )
        ],
        "issues": [
            admin_issue_summary(report)
            for report in sorted(open_issues, key=lambda item: item.get("created_at", ""), reverse=True)
        ],
    }


def driver_earning_for_ride(ride: dict) -> dict:
    payment = ride.get("payment") or {}
    payment_status = payment.get("status", "unknown")
    original_gross = round(float(payment.get("amount") or ride.get("fare") or 0), 2)
    refund = payment.get("refund") or {}
    refund_amount = round(float(refund.get("amount") or 0), 2)
    gross = 0 if payment_status in {"voided", "refunded"} else original_gross
    platform_fee = round(gross * PLATFORM_FEE_RATE, 2)
    net = round(gross - platform_fee, 2)
    return {
        "ride_id": ride["id"],
        "gross": gross,
        "original_gross": original_gross,
        "platform_fee": platform_fee,
        "platform_fee_rate": PLATFORM_FEE_RATE,
        "net": net,
        "currency": payment.get("currency", "USD"),
        "payment_status": payment_status,
        "refund_amount": refund_amount,
        "completed_at": ride.get("completed_at"),
        "pickup": ride.get("pickup"),
        "destination": ride.get("destination"),
    }


def is_today(value: str | None) -> bool:
    if not value:
        return False
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return False

    return timestamp.astimezone(timezone.utc).date() == datetime.now(timezone.utc).date()


def build_driver_earnings(driver_id: str) -> dict:
    driver = drivers.get(driver_id)
    completed_rides = [
        ride
        for ride in rides.values()
        if ride.get("driver_id") == driver_id and ride.get("status") == "completed"
    ]
    completed_rides.sort(key=lambda ride: ride.get("completed_at", ""), reverse=True)
    earning_rows = [driver_earning_for_ride(ride) for ride in completed_rides]

    gross_total = round(sum(row["gross"] for row in earning_rows), 2)
    platform_fee_total = round(sum(row["platform_fee"] for row in earning_rows), 2)
    net_total = round(sum(row["net"] for row in earning_rows), 2)
    today_rows = [row for row in earning_rows if is_today(row.get("completed_at"))]
    stats = (driver or {}).setdefault("stats", {"accepted": 0, "rejected": 0})
    decisions = stats.get("accepted", 0) + stats.get("rejected", 0)
    acceptance_rate = round((stats.get("accepted", 0) / decisions) * 100) if decisions else 100

    return {
        "currency": "USD",
        "platform_fee_rate": PLATFORM_FEE_RATE,
        "gross_total": gross_total,
        "platform_fee_total": platform_fee_total,
        "net_total": net_total,
        "today_net": round(sum(row["net"] for row in today_rows), 2),
        "today_rides": len(today_rows),
        "completed_rides": len(earning_rows),
        "acceptance_rate": acceptance_rate,
        "rides": earning_rows,
    }


def parse_location_payload(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    try:
        return location_adapter.validate_python(payload)
    except ValidationError:
        return None


def active_ride_for_driver(driver_id: str) -> dict | None:
    driver = drivers.get(driver_id)
    if not driver:
        return None
    ride_id = driver.get("current_ride_id")
    if not ride_id:
        return None
    ride = rides.get(ride_id)
    if not ride or ride.get("driver_id") != driver_id:
        return None
    if ride.get("status") in TERMINAL_RIDE_STATUSES:
        return None
    return ride


async def postgis_driver_candidates(ride: dict, pickup: Location) -> list[tuple[float, dict]] | None:
    if not database_pool or not postgis_available:
        return None

    declined = list(ride.get("declined_driver_ids", []))
    try:
        async with database_pool.acquire() as connection:
            rows = await connection.fetch(
                """
                WITH pickup AS (
                    SELECT ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography AS point
                )
                SELECT
                    driver_id,
                    ST_Distance(location, pickup.point) / 1000 AS distance_km
                FROM myuber_driver_locations, pickup
                WHERE availability = 'available'
                    AND current_ride_id IS NULL
                    AND account_status = 'active'
                    AND NOT (driver_id = ANY($3::text[]))
                ORDER BY location <-> pickup.point
                LIMIT 25
                """,
                float(pickup["lng"]),
                float(pickup["lat"]),
                declined,
            )
    except Exception:
        logger.exception("PostGIS nearest-driver query failed; using in-memory fallback")
        return None

    candidates: list[tuple[float, dict]] = []
    for row in rows:
        driver = drivers.get(row["driver_id"])
        if not driver:
            continue
        if not is_account_active(driver):
            continue
        if driver.get("availability") != "available" or driver.get("current_ride_id"):
            continue
        candidates.append((float(row["distance_km"]), driver))

    return candidates


async def available_driver_candidates(ride: dict) -> list[tuple[float, dict]]:
    pickup = parse_location_payload(ride.get("pickup"))
    if not pickup:
        return []

    postgis_candidates = await postgis_driver_candidates(ride, pickup)
    if postgis_candidates is not None:
        return postgis_candidates

    declined = set(ride.get("declined_driver_ids", []))
    candidates: list[tuple[float, dict]] = []

    for driver in drivers.values():
        if not is_account_active(driver):
            continue
        if driver["id"] in declined:
            continue
        if driver.get("availability") != "available":
            continue
        if driver.get("current_ride_id"):
            continue
        if not driver.get("location"):
            continue

        driver_location = parse_location_payload(driver.get("location"))
        if not driver_location:
            continue
        distance = haversine_km(driver_location, pickup)
        candidates.append((distance, driver))

    return sorted(candidates, key=lambda item: item[0])


async def send_to_connections(connections: list[WebSocket], message: dict) -> None:
    stale: list[WebSocket] = []
    for websocket in list(connections):
        try:
            await websocket.send_json(message)
        except Exception:
            stale.append(websocket)

    for websocket in stale:
        if websocket in connections:
            connections.remove(websocket)


async def dispatch_realtime_event(event: dict) -> None:
    target = event.get("target")
    target_id = event.get("target_id")
    message = event.get("message")

    if not isinstance(target_id, str) or not isinstance(message, dict):
        return

    if target == "ride":
        await send_to_connections(ride_connections.get(target_id, []), message)
    elif target == "driver":
        await send_to_connections(driver_connections.get(target_id, []), message)
    elif target == "share":
        await send_to_connections(share_connections.get(target_id, []), message)


async def fanout_realtime_event(target: str, target_id: str, message: dict) -> None:
    event = {
        "origin": INSTANCE_ID,
        "target": target,
        "target_id": target_id,
        "message": message,
    }

    await dispatch_realtime_event(event)

    if not redis_client:
        return

    try:
        await redis_client.publish(REDIS_CHANNEL, json.dumps(event))
    except Exception:
        logger.exception("Failed to publish realtime event to Redis")


async def listen_for_redis_events() -> None:
    while redis_client:
        pubsub = redis_client.pubsub()
        try:
            await pubsub.subscribe(REDIS_CHANNEL)
            async for raw_event in pubsub.listen():
                if raw_event.get("type") != "message":
                    continue

                try:
                    event = json.loads(raw_event.get("data", "{}"))
                except json.JSONDecodeError:
                    continue

                if event.get("origin") == INSTANCE_ID:
                    continue

                await dispatch_realtime_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Redis realtime subscriber crashed; reconnecting")
            await asyncio.sleep(2)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.close()


@app.on_event("startup")
async def start_realtime_pubsub() -> None:
    global redis_client, redis_subscriber_task

    await initialize_database()

    if not REDIS_URL:
        return
    if redis is None:
        logger.warning("REDIS_URL is set but redis package is not installed")
        return

    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        await redis_client.ping()
    except Exception:
        redis_client = None
        logger.exception("Could not connect to Redis; using local websocket fanout")
        return

    redis_subscriber_task = asyncio.create_task(listen_for_redis_events())


@app.on_event("shutdown")
async def stop_realtime_pubsub() -> None:
    global redis_client, redis_subscriber_task

    if redis_subscriber_task:
        redis_subscriber_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await redis_subscriber_task
        redis_subscriber_task = None

    if redis_client:
        await redis_client.aclose()
        redis_client = None

    await close_database()


async def broadcast_ride(ride: dict) -> None:
    await fanout_realtime_event(
        "ride",
        ride["id"],
        {"type": "ride_update", "ride": public_ride(ride)},
    )
    share_token = ride.get("share_token")
    if share_token:
        await fanout_realtime_event(
            "share",
            share_token,
            {"type": "share_update", "ride": public_shared_ride(ride)},
        )


async def broadcast_driver(driver_id: str, message: dict) -> None:
    await fanout_realtime_event("driver", driver_id, message)


async def assign_nearest_driver(ride: dict) -> dict | None:
    if ride.get("status") in TERMINAL_RIDE_STATUSES:
        return None

    candidates = await available_driver_candidates(ride)
    if not candidates:
        changed = set_ride_status(ride, "no_drivers_available", "system", allow_same=True)
        ride["driver_id"] = None
        ride["driver_distance_km"] = None
        ride["driver_location"] = None
        if changed:
            await notify_rider(
                ride,
                "No drivers available",
                "No nearby online drivers are available right now.",
                "ride_no_drivers",
            )
        await persist_runtime_state()
        return None

    distance, driver = candidates[0]
    driver["availability"] = "pending"
    driver["current_ride_id"] = ride["id"]

    matched_at = now_iso()
    set_ride_status(ride, "pending_driver", "system", allow_same=True)
    ride["driver_id"] = driver["id"]
    ride["driver_distance_km"] = round(distance, 2)
    ride["driver_location"] = driver.get("location")
    ride["matched_at"] = matched_at
    ride["updated_at"] = matched_at

    await broadcast_driver(
        driver["id"],
        {"type": "ride_request", "ride": public_ride(ride)},
    )
    await notify_rider(
        ride,
        "Driver matched",
        f"{driver['name']} is reviewing your ride request.",
        "ride_matched",
    )
    await notify_driver(
        driver["id"],
        "New ride request",
        f"{rider_name_for_ride(ride)} requested a nearby trip.",
        "ride_request",
        ride["id"],
    )
    await persist_runtime_state()
    return driver


async def assign_waiting_rides() -> None:
    for ride in rides.values():
        if ride.get("status") not in {"matching", "no_drivers_available"}:
            continue
        if ride.get("driver_id"):
            continue
        previous_status = ride["status"]
        await assign_nearest_driver(ride)
        if ride["status"] != previous_status:
            await broadcast_ride(ride)


async def update_driver_location_state(driver_id: str, location: dict) -> dict | None:
    driver = drivers.get(driver_id)
    if not driver:
        return None
    if not is_account_active(driver):
        driver["availability"] = "offline"
        driver["current_ride_id"] = None
        await persist_runtime_state()
        return driver

    now = datetime.now(timezone.utc).isoformat()
    driver["location"] = location
    driver["last_location_at"] = now

    active_ride = active_ride_for_driver(driver_id)
    if active_ride:
        active_ride["driver_location"] = location
        active_ride["updated_at"] = now
        await broadcast_ride(active_ride)
        await broadcast_driver(
            driver_id,
            {
                "type": "driver_location_update",
                "location": location,
                "ride": public_ride(active_ride),
            },
        )
        await persist_runtime_state()
        return driver

    if driver.get("availability") == "offline":
        await persist_runtime_state()
        return driver

    if driver.get("availability") != "busy":
        driver["availability"] = "available"
    await assign_waiting_rides()
    await persist_runtime_state()
    return driver


async def set_driver_availability_state(driver_id: str, online: bool) -> dict | None:
    driver = drivers.get(driver_id)
    if not driver:
        return None

    active_ride = active_ride_for_driver(driver_id)
    if not is_account_active(driver) and online:
        driver["availability"] = "offline"
        await persist_runtime_state()
        raise HTTPException(status_code=403, detail="Account is suspended")

    if not online:
        if active_ride:
            ride_status = active_ride.get("status")
            if ride_status != "pending_driver":
                raise HTTPException(
                    status_code=409,
                    detail="Complete or cancel the active ride before going offline",
                )

        driver["availability"] = "offline"
        driver["last_offline_at"] = now_iso()

        if active_ride:
            if driver_id not in active_ride["declined_driver_ids"]:
                active_ride["declined_driver_ids"].append(driver_id)
            driver["current_ride_id"] = None
            active_ride["driver_id"] = None
            active_ride["driver_distance_km"] = None
            active_ride["driver_location"] = None
            set_ride_status(active_ride, "matching", "driver")

            await broadcast_driver(driver_id, {"type": "ride_cleared", "ride_id": active_ride["id"]})
            await assign_nearest_driver(active_ride)
            await broadcast_ride(active_ride)
        else:
            driver["current_ride_id"] = None

        await broadcast_driver(
            driver_id,
            {"type": "availability_update", "availability": "offline", "online": False},
        )
        await persist_runtime_state()
        return driver

    driver["last_online_at"] = now_iso()
    if not driver.get("location"):
        driver["location"] = DEFAULT_DRIVER_LOCATION.copy()

    if active_ride:
        driver["availability"] = "pending" if active_ride.get("status") == "pending_driver" else "busy"
    else:
        driver["availability"] = "available"

    await broadcast_driver(
        driver_id,
        {
            "type": "availability_update",
            "availability": driver["availability"],
            "online": True,
        },
    )
    await assign_waiting_rides()
    await persist_runtime_state()
    return driver


async def update_rider_location_state(ride_id: str, rider_id: str, location: dict) -> dict:
    ride = rides.get(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != rider_id:
        raise HTTPException(status_code=403, detail="Cannot update this ride")
    if ride.get("status") in TERMINAL_RIDE_STATUSES:
        raise HTTPException(status_code=409, detail="Ride is closed")

    ride["rider_location"] = location
    ride["updated_at"] = now_iso()

    driver_id = ride.get("driver_id")
    if driver_id:
        await broadcast_driver(driver_id, {"type": "ride_update", "ride": public_ride(ride)})
    await broadcast_ride(ride)
    await persist_runtime_state()
    return ride

@app.get("/")
def root():
    return {"status": "MyUber API running", "storage": storage_status()}


@app.get("/health/storage")
def health_storage():
    return storage_status()

@app.post("/riders/signup")
async def rider_signup(payload: RiderSignup):
    for rider in riders.values():
        if rider["email"] == payload["email"]:
            raise HTTPException(status_code=400, detail="Email already registered")

    rider_id = str(uuid4())
    rider = {
        "id": rider_id,
        "email": payload["email"],
        "password_hash": hash_password(payload["password"]),
        "name": payload["name"],
        "phone": payload["phone"],
        "role": "rider",
        "account_status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    riders[rider_id] = rider
    await persist_runtime_state()
    return safe_user(rider)


@app.post("/riders/login")
def rider_login(payload: LoginRequest):
    for rider in riders.values():
        if rider["email"] == payload["email"]:
            if verify_password(payload["password"], rider["password_hash"]):
                ensure_account_active(rider)
                token = create_token({"sub": rider["id"], "role": "rider"})
                return {"token": token, "user": safe_user(rider)}
            else:
                raise HTTPException(status_code=401, detail="Invalid password")
    raise HTTPException(status_code=404, detail="Account not found")

@app.post("/drivers/signup")
async def driver_signup(payload: DriverSignup):
    for driver in drivers.values():
        if driver["email"] == payload["email"]:
            raise HTTPException(status_code=400, detail="Email already registered")

    driver_id = str(uuid4())
    driver = {
        "id": driver_id,
        "email": payload["email"],
        "password_hash": hash_password(payload["password"]),
        "name": payload["name"],
        "phone": payload["phone"],
        "role": "driver",
        "account_status": "active",
        "vehicle": {
            "make": payload["vehicle_make"],
            "model": payload["vehicle_model"],
            "year": payload["vehicle_year"],
            "color": payload["vehicle_color"],
            "plate": payload["vehicle_plate"],
        },
        "license_number": payload["license_number"],
        "onboarding_status": "complete",
        "availability": "offline",
        "location": None,
        "current_ride_id": None,
        "stats": {"accepted": 0, "rejected": 0},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    drivers[driver_id] = driver

    await persist_runtime_state()
    return safe_user(driver)


@app.post("/drivers/login")
def driver_login(payload: LoginRequest):
    for driver in drivers.values():
        if driver["email"] == payload["email"]:
            if verify_password(payload["password"], driver["password_hash"]):
                ensure_account_active(driver)
                token = create_token({"sub": driver["id"], "role": "driver"})
                return {"token": token, "user": safe_user(driver)}
            else:
                raise HTTPException(status_code=401, detail="Invalid password")
    raise HTTPException(status_code=404, detail="Account not found")


@app.post("/admin/login")
def admin_login(payload: LoginRequest):
    ensure_default_admin()
    for admin in admins.values():
        if admin["email"] == payload["email"]:
            if verify_password(payload["password"], admin["password_hash"]):
                ensure_account_active(admin)
                token = create_token({"sub": admin["id"], "role": "admin"})
                return {"token": token, "user": safe_user(admin)}
            raise HTTPException(status_code=401, detail="Invalid password")
    raise HTTPException(status_code=404, detail="Account not found")


@app.get("/auth/me")
def get_me(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    role = current_user["role"]

    if role == "rider":
        user = riders.get(user_id)
    elif role == "driver":
        user = drivers.get(user_id)
    elif role == "admin":
        ensure_default_admin()
        user = admins.get(user_id)
    else:
        user = None

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    ensure_account_active(user)
    return safe_user(user)


@app.get("/notifications")
def get_notifications(current_user: dict = Depends(get_current_user)):
    user_notifications = notifications_for_current_user(current_user)
    return {
        "unread_count": unread_notifications_count(current_user["user_id"], current_user["role"]),
        "notifications": user_notifications,
    }


@app.post("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, current_user: dict = Depends(get_current_user)):
    notification = notifications.get(notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    if (
        notification.get("user_id") != current_user["user_id"]
        or notification.get("role") != current_user["role"]
    ):
        raise HTTPException(status_code=403, detail="Cannot update this notification")

    if not notification.get("read_at"):
        notification["read_at"] = now_iso()
        await persist_runtime_state()
    return {
        "notification": notification_for_user(notification),
        "unread_count": unread_notifications_count(current_user["user_id"], current_user["role"]),
        "notifications": notifications_for_current_user(current_user),
    }


@app.post("/notifications/read-all")
async def mark_all_notifications_read(current_user: dict = Depends(get_current_user)):
    read_at = now_iso()
    changed = False
    for notification in notifications.values():
        if (
            notification.get("user_id") == current_user["user_id"]
            and notification.get("role") == current_user["role"]
            and not notification.get("read_at")
        ):
            notification["read_at"] = read_at
            changed = True
    if changed:
        await persist_runtime_state()
    return {
        "unread_count": unread_notifications_count(current_user["user_id"], current_user["role"]),
        "notifications": notifications_for_current_user(current_user),
    }


@app.get("/admin/dashboard")
def get_admin_dashboard(current_user: dict = Depends(get_current_user)):
    require_role(current_user, "admin")
    return build_admin_dashboard()


def admin_target_user(role: str, user_id: str) -> dict:
    if role == "rider":
        user = riders.get(user_id)
    elif role == "driver":
        user = drivers.get(user_id)
    else:
        raise HTTPException(status_code=400, detail="Role must be rider or driver")

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.patch("/admin/users/{role}/{user_id}/status")
async def update_admin_user_status(
    role: str,
    user_id: str,
    payload: AdminUserStatusRequest,
    current_user: dict = Depends(get_current_user),
):
    require_role(current_user, "admin")
    status = payload.get("status")
    if status not in ACCOUNT_STATUSES:
        raise HTTPException(status_code=400, detail="Status must be active or suspended")

    user = admin_target_user(role, user_id)
    if role == "driver" and status == "suspended":
        await set_driver_availability_state(user_id, False)

    user["account_status"] = status
    user["updated_at"] = now_iso()
    if role == "driver" and status == "suspended":
        user["availability"] = "offline"

    await persist_runtime_state()
    return admin_user_summary(user)


@app.post("/admin/drivers/{driver_id}/force-offline")
async def force_admin_driver_offline(
    driver_id: str,
    current_user: dict = Depends(get_current_user),
):
    require_role(current_user, "admin")
    driver = drivers.get(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    await set_driver_availability_state(driver_id, False)
    driver["updated_at"] = now_iso()
    await persist_runtime_state()
    return admin_driver_summary(driver)


@app.post("/drivers/location")
async def update_driver_location(
    payload: DriverLocationRequest,
    current_user: dict = Depends(get_current_user),
):
    driver_id = require_role(current_user, "driver")
    driver = drivers.get(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    driver = await update_driver_location_state(driver_id, payload["location"])
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    return safe_user(driver)


@app.post("/drivers/availability")
async def update_driver_availability(
    payload: DriverAvailabilityRequest,
    current_user: dict = Depends(get_current_user),
):
    driver_id = require_role(current_user, "driver")
    driver = await set_driver_availability_state(driver_id, payload["online"])
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    return safe_user(driver)


@app.get("/drivers/me/ride-request")
def get_driver_ride_request(current_user: dict = Depends(get_current_user)):
    driver_id = require_role(current_user, "driver")
    ride = active_ride_for_driver(driver_id)
    if not ride:
        return None
    return public_ride(ride)


@app.get("/drivers/me/earnings")
def get_driver_earnings(current_user: dict = Depends(get_current_user)):
    driver_id = require_role(current_user, "driver")
    if driver_id not in drivers:
        raise HTTPException(status_code=404, detail="Driver not found")
    return build_driver_earnings(driver_id)


@app.get("/search/locations")
async def search_locations(q: str):
    query = (q or "").strip()
    if len(query) < 2:
        return []

    key = cache_key("location-search", {"q": query.lower(), "limit": 5})
    cached = await cache_get_json(key)
    if isinstance(cached, list):
        return cached

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": query,
                "format": "json",
                "limit": 5,
                "addressdetails": 1,
            },
            headers={
                "User-Agent": "MyUber-Dev/1.0",
            },
            timeout=10.0,
        )

    if response.status_code != 200:
        return []

    results = response.json()
    locations = [
        {
            "name": r.get("display_name", ""),
            "lat": float(r.get("lat", 0)),
            "lng": float(r.get("lon", 0)),
        }
        for r in results
    ]
    await cache_set_json(key, locations)
    return locations


async def calculate_route_estimate(pickup: Location, destination: Location) -> dict:
    key = cache_key(
        "route-estimate",
        {
            "profile": "driving",
            "pickup": rounded_location_for_cache(pickup),
            "destination": rounded_location_for_cache(destination),
        },
    )
    cached = await cache_get_json(key)
    if isinstance(cached, dict):
        cached_estimate = route_estimate_from_cached_payload(cached)
        if cached_estimate:
            return cached_estimate

    coords = f"{pickup['lng']},{pickup['lat']};{destination['lng']},{destination['lat']}"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://router.project-osrm.org/route/v1/driving/{coords}",
                params={
                    "overview": "full",
                    "geometries": "geojson",
                    "steps": "true",
                },
                headers={
                    "User-Agent": "MyUber-Dev/1.0",
                },
                timeout=10.0,
            )
    except httpx.HTTPError:
        return fallback_route_estimate(pickup, destination)

    if response.status_code != 200:
        return fallback_route_estimate(pickup, destination)

    data = response.json()
    routes = data.get("routes", [])
    if not routes:
        return fallback_route_estimate(pickup, destination)

    route = routes[0]
    geometry = route.get("geometry", {}).get("coordinates", [])
    route_points = []
    for coord in geometry:
        if not isinstance(coord, (list, tuple)) or len(coord) < 2:
            continue
        lng, lat = coord[:2]
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            route_points.append({"lat": lat, "lng": lng})

    if len(route_points) < 2:
        return fallback_route_estimate(pickup, destination)

    estimate = build_route_estimate(
        (route.get("distance") or 0) / 1000,
        (route.get("duration") or 0) / 60,
        route_points,
        "osrm",
        steps_from_osrm_legs(route.get("legs", [])),
    )
    await cache_set_json(key, cached_route_payload(estimate))
    return estimate


@app.post("/routes/estimate")
async def estimate_route(payload: RouteEstimateRequest):
    return await calculate_route_estimate(payload["pickup"], payload["destination"])


@app.post("/rides")
async def request_ride(
    payload: RideRequest,
    current_user: dict = Depends(get_current_user),
):
    rider_id = require_role(current_user, "rider")
    if payload["rider_id"] != rider_id:
        raise HTTPException(status_code=403, detail="Cannot request for another rider")
    ensure_account_active(riders.get(rider_id))

    now = now_iso()
    ride_id = str(uuid4())
    estimate = await calculate_route_estimate(payload["pickup"], payload["destination"])
    ride = {
        "id": ride_id,
        "rider_id": rider_id,
        "pickup": payload["pickup"],
        "destination": payload["destination"],
        "distance_km": estimate["distance_km"],
        "duration_min": estimate["duration_min"],
        "currency": estimate["currency"],
        "fare": estimate["fare"],
        "fare_breakdown": estimate["fare_breakdown"],
        "status": "matching",
        "driver_id": None,
        "driver_distance_km": None,
        "driver_location": None,
        "rider_location": None,
        "payment": create_mock_payment(ride_id, estimate["fare"], now, estimate["fare_breakdown"]),
        "declined_driver_ids": [],
        "status_history": [
            {
                "from": None,
                "status": "matching",
                "actor": "rider",
                "at": now,
            }
        ],
        "created_at": now,
        "updated_at": now,
    }

    rides[ride_id] = ride
    await assign_nearest_driver(ride)
    await persist_runtime_state()
    return public_ride(ride)


@app.post("/rides/{ride_id}/rider-location")
async def update_rider_location(
    ride_id: str,
    payload: RiderLocationRequest,
    current_user: dict = Depends(get_current_user),
):
    rider_id = require_role(current_user, "rider")
    ride = await update_rider_location_state(ride_id, rider_id, payload["location"])
    return public_ride(ride)


@app.get("/rides/history")
def get_ride_history(current_user: dict = Depends(get_current_user)):
    return ride_history_for_user(current_user)


@app.get("/rides/{ride_id}")
def get_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    ride = rides.get(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if not can_view_ride(current_user, ride):
        raise HTTPException(status_code=403, detail="Cannot view this ride")

    return public_ride(ride)


@app.post("/rides/{ride_id}/share")
async def create_ride_share(ride_id: str, current_user: dict = Depends(get_current_user)):
    ride = rides.get(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if not can_view_ride(current_user, ride):
        raise HTTPException(status_code=403, detail="Cannot share this ride")

    share_token = ride.get("share_token")
    share = trip_shares.get(share_token) if share_token else None
    if not share or share.get("revoked_at"):
        share_token = uuid4().hex
        share = {
            "token": share_token,
            "ride_id": ride_id,
            "created_by_user_id": current_user["user_id"],
            "created_by_role": current_user["role"],
            "created_at": now_iso(),
            "revoked_at": None,
        }
        trip_shares[share_token] = share
        ride["share_token"] = share_token
        ride["updated_at"] = now_iso()
        await persist_runtime_state()

    return {
        "token": share_token,
        "url_path": f"/?share={share_token}",
        "created_at": share["created_at"],
        "ride": public_shared_ride(ride),
    }


@app.get("/shares/{share_token}")
def get_shared_ride(share_token: str):
    share = trip_shares.get(share_token)
    if not share or share.get("revoked_at"):
        raise HTTPException(status_code=404, detail="Shared trip not found")

    ride = rides.get(share.get("ride_id"))
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    return {
        "token": share_token,
        "created_at": share["created_at"],
        "ride": public_shared_ride(ride),
    }


@app.post("/rides/{ride_id}/ratings")
async def rate_ride(
    ride_id: str,
    payload: RideRatingRequest,
    current_user: dict = Depends(get_current_user),
):
    ride = rides.get(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Only completed rides can be rated")

    target_id, target_role = rating_target_for(current_user, ride)
    actor_role = current_user["role"]
    rating_key = actor_role
    ratings = ride.setdefault("ratings", {})
    existing = ratings.get(rating_key)
    created_at = existing.get("created_at") if existing else now_iso()
    updated_at = now_iso()

    ratings[rating_key] = {
        "id": existing.get("id") if existing else f"rate_{uuid4().hex[:12]}",
        "ride_id": ride_id,
        "score": normalized_rating_score(payload["score"]),
        "comment": normalized_feedback_text(payload.get("comment", ""), "Rating comment", 280),
        "from_user_id": current_user["user_id"],
        "from_role": actor_role,
        "to_user_id": target_id,
        "to_role": target_role,
        "created_at": created_at,
        "updated_at": updated_at,
    }
    ride["updated_at"] = updated_at

    if actor_role == "rider" and ride.get("driver_id"):
        await notify_driver(
            ride["driver_id"],
            "New rating received",
            "The rider rated your completed trip.",
            "rating_received",
            ride_id,
        )
    elif actor_role == "driver":
        await notify_rider(
            ride,
            "New rating received",
            "Your driver rated the completed trip.",
            "rating_received",
        )

    await broadcast_ride(ride)
    driver_id = ride.get("driver_id")
    if driver_id:
        await broadcast_driver(driver_id, {"type": "ride_update", "ride": public_ride(ride)})

    await persist_runtime_state()
    return public_ride(ride)


@app.post("/rides/{ride_id}/issues")
async def report_ride_issue(
    ride_id: str,
    payload: RideIssueReportRequest,
    current_user: dict = Depends(get_current_user),
):
    ride = rides.get(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if not can_view_ride(current_user, ride):
        raise HTTPException(status_code=403, detail="Cannot report this ride")

    description = normalized_feedback_text(
        payload.get("description", ""),
        "Issue description",
        600,
    )
    if not description:
        raise HTTPException(status_code=400, detail="Issue description is required")

    category = normalized_feedback_text(payload.get("category", "other"), "Issue category", 40)
    if not category:
        category = "other"

    reported_at = now_iso()
    report = {
        "id": f"issue_{uuid4().hex[:12]}",
        "ride_id": ride_id,
        "category": category,
        "description": description,
        "status": "open",
        "reporter_user_id": current_user["user_id"],
        "reporter_role": current_user["role"],
        "created_at": reported_at,
        "updated_at": reported_at,
    }

    issue_reports[report["id"]] = report
    ride.setdefault("issue_reports", []).append(report)
    ride["updated_at"] = reported_at

    if current_user["role"] == "rider" and ride.get("driver_id"):
        await notify_driver(
            ride["driver_id"],
            "Ride issue reported",
            "The rider submitted a report for this trip.",
            "issue_reported",
            ride_id,
        )
    elif current_user["role"] == "driver":
        await notify_rider(
            ride,
            "Ride issue reported",
            "Your driver submitted a report for this trip.",
            "issue_reported",
        )

    await broadcast_ride(ride)
    driver_id = ride.get("driver_id")
    if driver_id:
        await broadcast_driver(driver_id, {"type": "ride_update", "ride": public_ride(ride)})

    await persist_runtime_state()
    return public_ride(ride)


@app.post("/rides/{ride_id}/accept")
async def accept_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver_id = require_role(current_user, "driver")
    ride = rides.get(ride_id)
    driver = drivers.get(driver_id)
    if not ride or not driver:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("driver_id") != driver_id or ride.get("status") != "pending_driver":
        raise HTTPException(status_code=409, detail="Ride is not assigned to this driver")

    stats = driver.setdefault("stats", {"accepted": 0, "rejected": 0})
    driver["availability"] = "busy"
    driver["current_ride_id"] = ride_id
    stats["accepted"] += 1
    set_ride_status(ride, "accepted", "driver")
    await notify_rider(
        ride,
        "Ride accepted",
        f"{driver['name']} is on the way to your pickup.",
        "ride_accepted",
    )

    await broadcast_ride(ride)
    await broadcast_driver(driver_id, {"type": "ride_update", "ride": public_ride(ride)})
    await persist_runtime_state()
    return public_ride(ride)


@app.post("/rides/{ride_id}/reject")
async def reject_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver_id = require_role(current_user, "driver")
    ride = rides.get(ride_id)
    driver = drivers.get(driver_id)
    if not ride or not driver:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("driver_id") != driver_id or ride.get("status") != "pending_driver":
        raise HTTPException(status_code=409, detail="Ride is not assigned to this driver")

    stats = driver.setdefault("stats", {"accepted": 0, "rejected": 0})
    if driver_id not in ride["declined_driver_ids"]:
        ride["declined_driver_ids"].append(driver_id)
    driver["availability"] = "available"
    driver["current_ride_id"] = None
    stats["rejected"] += 1
    set_ride_status(ride, "matching", "driver")

    await broadcast_driver(driver_id, {"type": "ride_cleared", "ride_id": ride_id})
    await assign_nearest_driver(ride)
    await broadcast_ride(ride)
    await persist_runtime_state()
    return public_ride(ride)


@app.post("/rides/{ride_id}/status")
async def update_ride_status(
    ride_id: str,
    payload: RideStatusUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    driver_id = require_role(current_user, "driver")
    ride = rides.get(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("driver_id") != driver_id:
        raise HTTPException(status_code=403, detail="Cannot update this ride")

    next_status = payload["status"]
    if next_status not in DRIVER_PROGRESS_STATUSES:
        raise HTTPException(status_code=400, detail="Unsupported driver status update")

    set_ride_status(ride, next_status, "driver")
    if next_status == "completed":
        capture_mock_payment(ride)
        release_driver_for_ride(ride)
        await notify_rider(
            ride,
            "Trip complete",
            "Your trip is complete and the payment has been captured.",
            "ride_completed",
        )
        await notify_driver(
            driver_id,
            "Ride completed",
            "The trip is complete and earnings were updated.",
            "ride_completed",
            ride_id,
        )
    elif next_status == "arrived":
        await notify_rider(
            ride,
            "Driver arrived",
            "Your driver is at the pickup point.",
            "ride_arrived",
        )
    elif next_status == "in_progress":
        await notify_rider(
            ride,
            "Trip started",
            "Your trip is now in progress.",
            "ride_started",
        )

    await broadcast_ride(ride)
    await broadcast_driver(driver_id, {"type": "ride_update", "ride": public_ride(ride)})
    await persist_runtime_state()
    return public_ride(ride)


@app.post("/rides/{ride_id}/refund")
async def simulate_ride_refund(ride_id: str, current_user: dict = Depends(get_current_user)):
    rider_id = require_role(current_user, "rider")
    ride = rides.get(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != rider_id:
        raise HTTPException(status_code=403, detail="Cannot refund this ride")
    if ride.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Only completed rides can be refunded")

    refund_mock_payment(ride, "simulated_rider_refund")
    driver_id = ride.get("driver_id")
    await notify_rider(
        ride,
        "Refund simulated",
        "The mock payment was marked as refunded.",
        "payment_refunded",
    )

    await broadcast_ride(ride)
    if driver_id:
        await broadcast_driver(driver_id, {"type": "ride_update", "ride": public_ride(ride)})

    await persist_runtime_state()
    return public_ride(ride)


@app.post("/rides/{ride_id}/cancel")
async def cancel_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    ride = rides.get(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    actor = current_user["role"]
    user_id = current_user["user_id"]
    if actor == "rider":
        if ride.get("rider_id") != user_id:
            raise HTTPException(status_code=403, detail="Cannot cancel this ride")
        if ride.get("status") not in RIDER_CANCELABLE_STATUSES:
            raise HTTPException(status_code=409, detail="Ride cannot be cancelled now")
    elif actor == "driver":
        if ride.get("driver_id") != user_id:
            raise HTTPException(status_code=403, detail="Cannot cancel this ride")
        if ride.get("status") not in DRIVER_CANCELABLE_STATUSES:
            raise HTTPException(status_code=409, detail="Ride cannot be cancelled now")
    else:
        raise HTTPException(status_code=403, detail="Cannot cancel this ride")

    driver_id = ride.get("driver_id")
    set_ride_status(ride, "cancelled", actor)
    void_mock_payment(ride, f"cancelled_by_{actor}")
    release_driver_for_ride(ride)

    if actor == "driver":
        await notify_rider(
            ride,
            "Ride cancelled",
            "Your driver cancelled the ride. You can request again when ready.",
            "ride_cancelled",
        )
    elif driver_id:
        await notify_driver(
            driver_id,
            "Ride cancelled",
            "The rider cancelled this trip.",
            "ride_cancelled",
            ride_id,
        )

    await broadcast_ride(ride)
    if driver_id:
        await broadcast_driver(driver_id, {"type": "ride_update", "ride": public_ride(ride)})
        await broadcast_driver(driver_id, {"type": "ride_cleared", "ride_id": ride_id})

    await persist_runtime_state()
    return public_ride(ride)


@app.websocket("/ws/rides/{ride_id}")
async def ride_socket(websocket: WebSocket, ride_id: str, token: str | None = None):
    current_user = await authenticate_socket(websocket, token)
    if not current_user:
        return

    ride = rides.get(ride_id)
    if not ride or not can_view_ride(current_user, ride):
        await websocket.accept()
        await websocket.close(code=1008)
        return

    await websocket.accept()
    ride_connections.setdefault(ride_id, []).append(websocket)
    await websocket.send_json({"type": "ride_update", "ride": public_ride(ride)})

    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")

            if message_type == "ping":
                await websocket.send_json({"type": "pong", "sent_at": message.get("sent_at")})
                continue

            if message_type != "rider_location_update":
                continue

            if current_user["role"] != "rider":
                await websocket.send_json({"type": "error", "detail": "Rider account required"})
                continue

            location = parse_location_payload(message.get("location"))
            if not location:
                await websocket.send_json({"type": "error", "detail": "Invalid location"})
                continue

            await update_rider_location_state(ride_id, current_user["user_id"], location)
    except WebSocketDisconnect:
        pass
    finally:
        sockets = ride_connections.get(ride_id, [])
        if websocket in sockets:
            sockets.remove(websocket)


@app.websocket("/ws/shares/{share_token}")
async def share_socket(websocket: WebSocket, share_token: str):
    share = trip_shares.get(share_token)
    ride = rides.get(share.get("ride_id")) if share and not share.get("revoked_at") else None
    if not share or not ride:
        await websocket.accept()
        await websocket.close(code=1008)
        return

    await websocket.accept()
    share_connections.setdefault(share_token, []).append(websocket)
    await websocket.send_json({"type": "share_update", "ride": public_shared_ride(ride)})

    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong", "sent_at": message.get("sent_at")})
    except WebSocketDisconnect:
        pass
    finally:
        sockets = share_connections.get(share_token, [])
        if websocket in sockets:
            sockets.remove(websocket)


@app.websocket("/ws/drivers/{driver_id}")
async def driver_socket(websocket: WebSocket, driver_id: str, token: str | None = None):
    current_user = await authenticate_socket(websocket, token)
    if not current_user:
        return

    if current_user["role"] != "driver" or current_user["user_id"] != driver_id:
        await websocket.accept()
        await websocket.close(code=1008)
        return

    await websocket.accept()
    driver_connections.setdefault(driver_id, []).append(websocket)

    ride = active_ride_for_driver(driver_id)
    if ride:
        event_type = "ride_request" if ride.get("status") == "pending_driver" else "ride_update"
        await websocket.send_json({"type": event_type, "ride": public_ride(ride)})

    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")

            if message_type == "ping":
                await websocket.send_json({"type": "pong", "sent_at": message.get("sent_at")})
                continue

            if message_type == "availability_update":
                online = message.get("online")
                if not isinstance(online, bool):
                    await websocket.send_json({"type": "error", "detail": "Invalid availability"})
                    continue

                try:
                    driver = await set_driver_availability_state(driver_id, online)
                except HTTPException as exc:
                    await websocket.send_json({"type": "error", "detail": exc.detail})
                    continue

                if driver:
                    await websocket.send_json(
                        {
                            "type": "availability_update",
                            "availability": driver.get("availability"),
                            "online": driver.get("availability") != "offline",
                        }
                    )
                continue

            if message_type != "driver_location_update":
                continue

            location = parse_location_payload(message.get("location"))
            if not location:
                await websocket.send_json({"type": "error", "detail": "Invalid location"})
                continue

            await update_driver_location_state(driver_id, location)
    except WebSocketDisconnect:
        pass
    finally:
        sockets = driver_connections.get(driver_id, [])
        if websocket in sockets:
            sockets.remove(websocket)
