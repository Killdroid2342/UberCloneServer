from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from pydantic import TypeAdapter, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from typing_extensions import NotRequired, TypedDict
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from jose import jwt, JWTError
from dotenv import load_dotenv
import asyncio
import bcrypt
import contextlib
import hashlib
import httpx
import json
import logging
import os
import secrets
import smtplib
import time
from math import asin, cos, radians, sin, sqrt

try:
    import redis.asyncio as redis
except ImportError:
    redis = None

try:
    from pywebpush import WebPushException, webpush
except ImportError:
    WebPushException = None
    webpush = None

try:
    import asyncpg
except ImportError:
    asyncpg = None

load_dotenv()


def parse_csv_env(name: str, default: str) -> list[str]:
    raw_value = os.getenv(name, default)
    return [value.strip() for value in raw_value.split(",") if value.strip()]


def parse_float_env(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def parse_int_env(name: str, default: str) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return int(default)


def parse_bool_env(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


app = FastAPI(title="MyUber API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_csv_env("MYUBER_ALLOWED_ORIGINS", "*"),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SECRET_KEY = os.getenv("MYUBER_SECRET_KEY") or os.getenv("SECRET_KEY")
USING_GENERATED_SECRET_KEY = not bool(SECRET_KEY)
if not SECRET_KEY:
    SECRET_KEY = f"{uuid4().hex}{uuid4().hex}"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = parse_int_env("MYUBER_ACCESS_TOKEN_EXPIRE_MINUTES", "15")
REFRESH_TOKEN_EXPIRE_DAYS = parse_int_env("MYUBER_REFRESH_TOKEN_EXPIRE_DAYS", "30")
INSTANCE_ID = str(uuid4())
APP_STARTED_AT = time.time()
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
REDIS_CHANNEL = os.getenv("REDIS_CHANNEL", "myuber:realtime")
REDIS_CACHE_PREFIX = os.getenv("REDIS_CACHE_PREFIX", "myuber:cache")
REDIS_CACHE_TTL_SECONDS = int(os.getenv("REDIS_CACHE_TTL_SECONDS", "900"))
RATE_LIMIT_ENABLED = parse_bool_env("MYUBER_RATE_LIMIT_ENABLED", "true")
RATE_LIMIT_DEFAULT_PER_MINUTE = parse_int_env("MYUBER_RATE_LIMIT_DEFAULT_PER_MINUTE", "180")
RATE_LIMIT_READ_PER_MINUTE = parse_int_env("MYUBER_RATE_LIMIT_READ_PER_MINUTE", "300")
RATE_LIMIT_LOGIN_PER_MINUTE = parse_int_env("MYUBER_RATE_LIMIT_LOGIN_PER_MINUTE", "6")
RATE_LIMIT_SIGNUP_PER_HOUR = parse_int_env("MYUBER_RATE_LIMIT_SIGNUP_PER_HOUR", "10")
RATE_LIMIT_REFRESH_PER_MINUTE = parse_int_env("MYUBER_RATE_LIMIT_REFRESH_PER_MINUTE", "30")
RATE_LIMIT_RIDE_REQUESTS_PER_MINUTE = parse_int_env("MYUBER_RATE_LIMIT_RIDE_REQUESTS_PER_MINUTE", "12")
ROUTING_PROVIDER = os.getenv("ROUTING_PROVIDER", "osrm").strip().lower()
try:
    ROUTING_TIMEOUT_SECONDS = float(os.getenv("ROUTING_TIMEOUT_SECONDS", "10"))
except ValueError:
    ROUTING_TIMEOUT_SECONDS = 10.0
OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org").rstrip("/")
OSRM_PROFILE = os.getenv("OSRM_PROFILE", "driving").strip()
GRAPHHOPPER_BASE_URL = os.getenv("GRAPHHOPPER_BASE_URL", "https://graphhopper.com/api/1").rstrip("/")
GRAPHHOPPER_PROFILE = os.getenv("GRAPHHOPPER_PROFILE", "car").strip()
GRAPHHOPPER_API_KEY = os.getenv("GRAPHHOPPER_API_KEY")
DATABASE_STATE_KEY = "runtime"
DEFAULT_ADMIN_EMAIL = os.getenv("MYUBER_ADMIN_EMAIL", "admin@myuber.local")
DEFAULT_ADMIN_PASSWORD = os.getenv("MYUBER_ADMIN_PASSWORD")
USING_GENERATED_ADMIN_PASSWORD = not bool(DEFAULT_ADMIN_PASSWORD)
if not DEFAULT_ADMIN_PASSWORD:
    DEFAULT_ADMIN_PASSWORD = uuid4().hex
PLATFORM_FEE_RATE = 0.20
FARE_CURRENCY = "USD"
DEFAULT_RIDER_WALLET_BALANCE = parse_float_env("MYUBER_DEFAULT_WALLET_BALANCE", "50")
WALLET_TOP_UP_MIN = 1.00
WALLET_TOP_UP_MAX = 500.00
BASE_FARE = 3.50
PER_MILE_RATE = 1.65
PER_MINUTE_RATE = 0.28
MINIMUM_FARE = 7.00
CANCELLATION_FEE_MIN = 3.00
CANCELLATION_FEE_MAX = 10.00
CANCELLATION_FEE_RATE = 0.25
SURGE_MAX_MULTIPLIER = 2.25
SURGE_RESPONSE_FACTOR = 0.35
SCHEDULED_RIDE_CHECK_INTERVAL_SECONDS = 30
DISPATCH_REQUEST_TIMEOUT_SECONDS = int(os.getenv("MYUBER_DISPATCH_TIMEOUT_SECONDS", "30"))
DEMAND_HEATMAP_GRID_SIZE = float(os.getenv("MYUBER_DEMAND_HEATMAP_GRID_SIZE", "0.01"))
FRAUD_RECENT_WINDOW_MINUTES = parse_int_env("MYUBER_FRAUD_RECENT_WINDOW_MINUTES", "15")
FRAUD_REVIEW_SCORE = parse_int_env("MYUBER_FRAUD_REVIEW_SCORE", "60")
FRAUD_BLOCK_SCORE = parse_int_env("MYUBER_FRAUD_BLOCK_SCORE", "90")
LOG_LEVEL = os.getenv("MYUBER_LOG_LEVEL", "INFO").upper()
RECEIPT_EMAIL_FROM = os.getenv("MYUBER_RECEIPT_EMAIL_FROM", "receipts@myuber.local")
SMTP_HOST = os.getenv("MYUBER_SMTP_HOST")
SMTP_PORT = int(os.getenv("MYUBER_SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("MYUBER_SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("MYUBER_SMTP_PASSWORD")
SMTP_USE_TLS = os.getenv("MYUBER_SMTP_USE_TLS", "true").strip().lower() not in {"0", "false", "no"}
SMTP_TIMEOUT_SECONDS = parse_float_env("MYUBER_SMTP_TIMEOUT_SECONDS", "10")
PUSH_VAPID_PUBLIC_KEY = os.getenv("MYUBER_VAPID_PUBLIC_KEY")
PUSH_VAPID_PRIVATE_KEY = os.getenv("MYUBER_VAPID_PRIVATE_KEY")
PUSH_VAPID_SUBJECT = os.getenv("MYUBER_VAPID_SUBJECT", f"mailto:{DEFAULT_ADMIN_EMAIL}")
AUDIT_LOG_MAX_EVENTS = parse_int_env("MYUBER_AUDIT_LOG_MAX_EVENTS", "1000")
OBSERVABILITY_RECENT_REQUESTS = parse_int_env("MYUBER_OBSERVABILITY_RECENT_REQUESTS", "50")
OBSERVABILITY_LATENCY_SAMPLES = parse_int_env("MYUBER_OBSERVABILITY_LATENCY_SAMPLES", "500")

ADMIN_PERMISSIONS = {
    "admin.dashboard.read": {
        "id": "admin.dashboard.read",
        "label": "View operations dashboard",
        "description": "Read operational totals, live rides, demand, and management summaries.",
    },
    "admin.users.manage": {
        "id": "admin.users.manage",
        "label": "Manage accounts",
        "description": "Suspend and reactivate rider or driver accounts.",
    },
    "admin.drivers.manage": {
        "id": "admin.drivers.manage",
        "label": "Manage drivers",
        "description": "Force drivers offline and review driver documents.",
    },
    "admin.fraud.read": {
        "id": "admin.fraud.read",
        "label": "View fraud events",
        "description": "Read risk events and fraud review signals.",
    },
    "admin.audit.read": {
        "id": "admin.audit.read",
        "label": "View audit logs",
        "description": "Read security and administrative activity logs.",
    },
    "admin.observability.read": {
        "id": "admin.observability.read",
        "label": "View observability",
        "description": "Read API latency, error, storage, background job, and websocket health.",
    },
}

ADMIN_ROLE_DEFINITIONS = {
    "super_admin": {
        "id": "super_admin",
        "label": "Super admin",
        "permissions": sorted(ADMIN_PERMISSIONS),
    },
    "operations": {
        "id": "operations",
        "label": "Operations",
        "permissions": [
            "admin.dashboard.read",
            "admin.users.manage",
            "admin.drivers.manage",
            "admin.fraud.read",
            "admin.observability.read",
        ],
    },
    "support": {
        "id": "support",
        "label": "Support",
        "permissions": [
            "admin.dashboard.read",
            "admin.audit.read",
        ],
    },
}

ROLE_PERMISSIONS = {
    "rider": [
        "rides.request",
        "rides.view_own",
        "rides.cancel_own",
        "rides.share_own",
        "wallet.manage_own",
        "notifications.manage_own",
    ],
    "driver": [
        "drivers.location.write",
        "drivers.availability.write",
        "rides.accept_assigned",
        "rides.progress_assigned",
        "rides.view_assigned",
        "earnings.view_own",
        "notifications.manage_own",
    ],
}

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("myuber")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

if USING_GENERATED_SECRET_KEY:
    logger.warning("MYUBER_SECRET_KEY is not set; generated a temporary in-memory JWT secret")

if USING_GENERATED_ADMIN_PASSWORD:
    logger.warning("MYUBER_ADMIN_PASSWORD is not set; generated a temporary admin password for this process")

if PUSH_VAPID_PUBLIC_KEY and PUSH_VAPID_PRIVATE_KEY and webpush is None:
    logger.warning("VAPID push keys are configured but pywebpush is not installed; push delivery is disabled")


def request_id_for(request: Request) -> str:
    return getattr(request.state, "request_id", None) or uuid4().hex


def error_response(
    request: Request,
    status_code: int,
    detail,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = request_id_for(request)
    response_headers = {"X-Request-ID": request_id}
    if headers:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "request_id": request_id},
        headers=response_headers,
    )


def client_ip_for(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip() or "unknown"
    if request.client:
        return request.client.host
    return "unknown"


def rate_limit_rule_for(method: str, path: str) -> tuple[str, int, int] | None:
    if path in {"/", "/health/storage", "/vehicle-types"}:
        return None
    if method == "POST" and path in {"/riders/login", "/drivers/login", "/admin/login"}:
        return "auth-login", RATE_LIMIT_LOGIN_PER_MINUTE, 60
    if method == "POST" and path in {"/riders/signup", "/drivers/signup"}:
        return "auth-signup", RATE_LIMIT_SIGNUP_PER_HOUR, 3600
    if method == "POST" and path in {"/auth/refresh", "/auth/logout"}:
        return "auth-session", RATE_LIMIT_REFRESH_PER_MINUTE, 60
    if method == "POST" and path == "/rides":
        return "ride-create", RATE_LIMIT_RIDE_REQUESTS_PER_MINUTE, 60
    if path == "/routes/estimate":
        return "route-estimate", 60, 60
    if path == "/search/locations":
        return "location-search", 90, 60
    if method == "GET":
        return "read", RATE_LIMIT_READ_PER_MINUTE, 60
    return "write", RATE_LIMIT_DEFAULT_PER_MINUTE, 60


def rate_limit_headers(
    limit: int,
    remaining: int,
    reset_epoch: int,
    *,
    include_retry_after: bool = False,
) -> dict[str, str]:
    retry_after = max(0, reset_epoch - int(time.time()))
    headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(max(0, remaining)),
        "X-RateLimit-Reset": str(reset_epoch),
    }
    if include_retry_after:
        headers["Retry-After"] = str(retry_after)
    return headers


def cleanup_rate_limit_buckets(now_epoch: int) -> None:
    if len(rate_limit_buckets) < 10000:
        return
    stale_keys = [
        key
        for key, bucket in rate_limit_buckets.items()
        if int(bucket.get("reset_epoch", 0)) <= now_epoch
    ]
    for key in stale_keys:
        rate_limit_buckets.pop(key, None)


async def check_rate_limit(request: Request) -> dict | None:
    if not RATE_LIMIT_ENABLED:
        return None

    rule = rate_limit_rule_for(request.method.upper(), request.url.path)
    if not rule:
        return None

    name, limit, window_seconds = rule
    if limit <= 0:
        return None

    now_epoch = int(time.time())
    window_start = (now_epoch // window_seconds) * window_seconds
    reset_epoch = window_start + window_seconds
    client_ip = client_ip_for(request)
    bucket_key = f"{name}:{client_ip}"

    if redis_client:
        redis_key = f"{REDIS_CACHE_PREFIX}:rate:{window_start}:{bucket_key}"
        try:
            count = await redis_client.incr(redis_key)
            if count == 1:
                await redis_client.expire(redis_key, max(1, reset_epoch - now_epoch + 1))
            remaining = limit - int(count)
            return {
                "allowed": count <= limit,
                "limit": limit,
                "remaining": remaining,
                "reset_epoch": reset_epoch,
                "name": name,
            }
        except Exception:
            logger.exception("Redis rate limit check failed; using in-memory limiter")

    cleanup_rate_limit_buckets(now_epoch)
    memory_key = f"{window_start}:{bucket_key}"
    bucket = rate_limit_buckets.get(memory_key)
    if not bucket:
        bucket = {"count": 0, "reset_epoch": reset_epoch}
        rate_limit_buckets[memory_key] = bucket
    bucket["count"] += 1
    remaining = limit - int(bucket["count"])
    return {
        "allowed": bucket["count"] <= limit,
        "limit": limit,
        "remaining": remaining,
        "reset_epoch": reset_epoch,
        "name": name,
    }


@app.middleware("http")
async def log_http_requests(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid4().hex
    request.state.request_id = request_id
    start = time.perf_counter()

    rate_limit = await check_rate_limit(request)
    if rate_limit and not rate_limit["allowed"]:
        duration_ms = (time.perf_counter() - start) * 1000
        record_http_observation(
            method=request.method,
            path=request.url.path,
            status_code=429,
            duration_ms=duration_ms,
            request_id=request_id,
            rate_limited=True,
        )
        logger.warning(
            "rate_limited method=%s path=%s bucket=%s client_ip=%s duration_ms=%.2f request_id=%s",
            request.method,
            request.url.path,
            rate_limit.get("name"),
            client_ip_for(request),
            duration_ms,
            request_id,
        )
        return error_response(
            request,
            429,
            "Too many requests. Please wait before trying again.",
            rate_limit_headers(
                rate_limit["limit"],
                rate_limit["remaining"],
                rate_limit["reset_epoch"],
                include_retry_after=True,
            ),
        )

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000
        record_http_observation(
            method=request.method,
            path=request.url.path,
            status_code=500,
            duration_ms=duration_ms,
            request_id=request_id,
        )
        logger.exception(
            "unhandled_request_error method=%s path=%s duration_ms=%.2f request_id=%s",
            request.method,
            request.url.path,
            duration_ms,
            request_id,
        )
        return error_response(request, 500, "Internal server error")

    duration_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    record_http_observation(
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
        request_id=request_id,
    )
    if rate_limit:
        response.headers.update(
            rate_limit_headers(
                rate_limit["limit"],
                rate_limit["remaining"],
                rate_limit["reset_epoch"],
            )
        )
    logger.info(
        "http_request method=%s path=%s status_code=%s duration_ms=%.2f request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    return response


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(request: Request, exc: StarletteHTTPException):
    level = logging.ERROR if exc.status_code >= 500 else logging.WARNING
    logger.log(
        level,
        "http_error method=%s path=%s status_code=%s detail=%s request_id=%s",
        request.method,
        request.url.path,
        exc.status_code,
        exc.detail,
        request_id_for(request),
    )
    response = error_response(request, exc.status_code, exc.detail)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


@app.exception_handler(RequestValidationError)
async def handle_validation_exception(request: Request, exc: RequestValidationError):
    logger.warning(
        "validation_error method=%s path=%s errors=%s request_id=%s",
        request.method,
        request.url.path,
        exc.errors(),
        request_id_for(request),
    )
    return error_response(request, 422, exc.errors())

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
push_subscriptions: dict[str, dict] = {}
trip_shares: dict[str, dict] = {}
refresh_tokens: dict[str, dict] = {}
fraud_events: dict[str, dict] = {}
audit_logs: dict[str, dict] = {}
ride_connections: dict[str, list[WebSocket]] = {}
driver_connections: dict[str, list[WebSocket]] = {}
share_connections: dict[str, list[WebSocket]] = {}
DEFAULT_DRIVER_LOCATION = {"lat": 51.5074, "lng": -0.1278}
redis_client = None
redis_subscriber_task: asyncio.Task | None = None
scheduled_rides_task: asyncio.Task | None = None
database_pool = None
database_available = False
postgis_available = False
rate_limit_buckets: dict[str, dict] = {}
observability_metrics: dict[str, object] = {
    "total_requests": 0,
    "status_counts": {},
    "method_counts": {},
    "route_metrics": {},
    "latency_samples_ms": [],
    "recent_requests": [],
    "rate_limited_requests": 0,
    "error_requests": 0,
}

RIDE_STATUSES = {
    "scheduled",
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
RIDE_QUEUE_STATUSES = {"matching", "no_drivers_available"}
DRIVER_PROGRESS_STATUSES = {"arrived", "in_progress", "completed"}
RIDER_CANCELABLE_STATUSES = {
    "scheduled",
    "matching",
    "pending_driver",
    "accepted",
    "arrived",
    "no_drivers_available",
}
DRIVER_CANCELABLE_STATUSES = {"accepted", "arrived"}
ALLOWED_RIDE_TRANSITIONS = {
    "scheduled": {"matching", "cancelled"},
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
    "scheduled": "scheduled_at",
    "pending_driver": "matched_at",
    "accepted": "accepted_at",
    "arrived": "arrived_at",
    "in_progress": "started_at",
    "completed": "completed_at",
    "cancelled": "cancelled_at",
    "no_drivers_available": "no_drivers_available_at",
}
ACCOUNT_STATUSES = {"active", "suspended"}
DRIVER_DOCUMENT_STATUSES = {"pending_review", "verified", "rejected"}
DRIVER_DOCUMENT_TYPES = {
    "license": {
        "type": "license",
        "label": "Driver license",
        "required": True,
    },
    "insurance": {
        "type": "insurance",
        "label": "Insurance",
        "required": True,
    },
    "vehicle_registration": {
        "type": "vehicle_registration",
        "label": "Vehicle registration",
        "required": True,
    },
}
VEHICLE_TYPES = {
    "standard": {
        "type": "standard",
        "label": "Standard",
        "description": "Everyday cars for up to 4 riders",
        "capacity": 4,
        "fare_multiplier": 1.0,
    },
    "xl": {
        "type": "xl",
        "label": "XL",
        "description": "Larger vehicles for groups and luggage",
        "capacity": 6,
        "fare_multiplier": 1.35,
    },
    "premium": {
        "type": "premium",
        "label": "Premium",
        "description": "Higher-rated premium vehicles",
        "capacity": 4,
        "fare_multiplier": 1.65,
    },
}
DEFAULT_VEHICLE_TYPE = "standard"
PROMO_CODES = {
    "SAVE10": {
        "code": "SAVE10",
        "label": "10% off",
        "type": "percent",
        "value": 0.10,
        "max_discount": 8.00,
    },
    "MYUBER5": {
        "code": "MYUBER5",
        "label": "$5 off",
        "type": "fixed",
        "value": 5.00,
        "max_discount": 5.00,
    },
    "WELCOME20": {
        "code": "WELCOME20",
        "label": "20% off",
        "type": "percent",
        "value": 0.20,
        "max_discount": 12.00,
    },
}

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
        "vehicle_type": NotRequired[str],
        "license_number": str,
    },
)

LoginRequest = TypedDict("LoginRequest", {"email": str, "password": str})
RefreshTokenRequest = TypedDict("RefreshTokenRequest", {"refresh_token": str})

RideRequest = TypedDict(
    "RideRequest",
    {
        "rider_id": str,
        "pickup": Location,
        "destination": Location,
        "vehicle_type": NotRequired[str],
        "scheduled_for": NotRequired[str],
        "promo_code": NotRequired[str],
    },
)

RouteEstimateRequest = TypedDict(
    "RouteEstimateRequest",
    {
        "pickup": Location,
        "destination": Location,
        "vehicle_type": NotRequired[str],
        "promo_code": NotRequired[str],
    },
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
PushSubscriptionKeys = TypedDict("PushSubscriptionKeys", {"p256dh": str, "auth": str})
PushSubscriptionRequest = TypedDict(
    "PushSubscriptionRequest",
    {
        "endpoint": str,
        "keys": PushSubscriptionKeys,
        "expirationTime": NotRequired[float | None],
    },
)
WalletTopUpRequest = TypedDict("WalletTopUpRequest", {"amount": float})
AdminUserStatusRequest = TypedDict("AdminUserStatusRequest", {"status": str})
AdminDriverDocumentsRequest = TypedDict("AdminDriverDocumentsRequest", {"status": str})

location_adapter = TypeAdapter(Location)

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def vehicle_type_config(vehicle_type: str | None) -> dict:
    return VEHICLE_TYPES.get(vehicle_type or DEFAULT_VEHICLE_TYPE, VEHICLE_TYPES[DEFAULT_VEHICLE_TYPE])


def normalize_vehicle_type(vehicle_type: str | None) -> str:
    normalized = (vehicle_type or DEFAULT_VEHICLE_TYPE).strip().lower()
    if normalized not in VEHICLE_TYPES:
        allowed = ", ".join(sorted(VEHICLE_TYPES))
        raise HTTPException(status_code=400, detail=f"Vehicle type must be one of: {allowed}")
    return normalized


def public_vehicle_types() -> list[dict]:
    return [
        {
            "type": key,
            "label": config["label"],
            "description": config["description"],
            "capacity": config["capacity"],
            "fare_multiplier": config["fare_multiplier"],
        }
        for key, config in VEHICLE_TYPES.items()
    ]


def parse_datetime(value: str | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field_name} must be an ISO date-time")

    cleaned = value.strip()
    if not cleaned:
        return None

    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field_name} must be an ISO date-time")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_scheduled_for(value: str | None) -> str | None:
    scheduled_at = parse_datetime(value, "scheduled_for")
    if scheduled_at is None:
        return None
    if scheduled_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Scheduled time must be in the future")
    return scheduled_at.isoformat()


def ride_scheduled_datetime(ride: dict) -> datetime | None:
    try:
        return parse_datetime(ride.get("scheduled_for"), "scheduled_for")
    except HTTPException:
        return None


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
        "admin_role": "super_admin",
        "permissions": sorted(ADMIN_PERMISSIONS),
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
        "push_subscriptions": push_subscriptions,
        "trip_shares": trip_shares,
        "refresh_tokens": refresh_tokens,
        "fraud_events": fraud_events,
        "audit_logs": audit_logs,
    }


def restore_runtime_state(payload: dict) -> None:
    stores = {
        "riders": riders,
        "drivers": drivers,
        "admins": admins,
        "rides": rides,
        "issue_reports": issue_reports,
        "notifications": notifications,
        "push_subscriptions": push_subscriptions,
        "trip_shares": trip_shares,
        "refresh_tokens": refresh_tokens,
        "fraud_events": fraud_events,
        "audit_logs": audit_logs,
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
        if not driver_can_receive_requests(driver):
            continue
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
        "routing": routing_status(),
        "security": security_status(),
    }


def active_routing_provider() -> str:
    if ROUTING_PROVIDER in {"osrm", "graphhopper"}:
        return ROUTING_PROVIDER
    return "osrm"


def routing_status() -> dict:
    provider = active_routing_provider()
    return {
        "provider": provider,
        "configured_provider": ROUTING_PROVIDER,
        "timeout_seconds": ROUTING_TIMEOUT_SECONDS,
        "osrm": {
            "base_url": OSRM_BASE_URL,
            "profile": OSRM_PROFILE,
        },
        "graphhopper": {
            "base_url": GRAPHHOPPER_BASE_URL,
            "profile": GRAPHHOPPER_PROFILE,
            "api_key_configured": bool(GRAPHHOPPER_API_KEY),
        },
    }


def security_status() -> dict:
    return {
        "rate_limiting": {
            "enabled": RATE_LIMIT_ENABLED,
            "default_per_minute": RATE_LIMIT_DEFAULT_PER_MINUTE,
            "read_per_minute": RATE_LIMIT_READ_PER_MINUTE,
            "login_per_minute": RATE_LIMIT_LOGIN_PER_MINUTE,
            "ride_requests_per_minute": RATE_LIMIT_RIDE_REQUESTS_PER_MINUTE,
        },
        "auth": {
            "access_token_expire_minutes": ACCESS_TOKEN_EXPIRE_MINUTES,
            "refresh_token_expire_days": REFRESH_TOKEN_EXPIRE_DAYS,
        },
        "access_control": role_permission_summary(),
        "fraud": {
            "review_score": FRAUD_REVIEW_SCORE,
            "block_score": FRAUD_BLOCK_SCORE,
            "recent_window_minutes": FRAUD_RECENT_WINDOW_MINUTES,
        },
    }


def normalize_observability_path(path: str) -> str:
    normalized_segments = []
    for segment in path.strip("/").split("/"):
        if not segment:
            continue
        hex_like = all(character in "0123456789abcdefABCDEF-" for character in segment)
        if segment.isdigit() or (len(segment) >= 16 and hex_like):
            normalized_segments.append("{id}")
        else:
            normalized_segments.append(segment)
    return "/" + "/".join(normalized_segments) if normalized_segments else "/"


def bounded_append(items: list, item, limit: int) -> None:
    items.append(item)
    if len(items) > max(limit, 1):
        del items[: len(items) - max(limit, 1)]


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((percentile_value / 100) * (len(ordered) - 1))))
    return round(float(ordered[index]), 2)


def record_http_observation(
    *,
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
    request_id: str,
    rate_limited: bool = False,
) -> None:
    route_path = normalize_observability_path(path)
    status_group = f"{status_code // 100}xx"
    observability_metrics["total_requests"] = int(observability_metrics.get("total_requests", 0)) + 1

    status_counts = observability_metrics.setdefault("status_counts", {})
    status_counts[str(status_code)] = status_counts.get(str(status_code), 0) + 1

    method_counts = observability_metrics.setdefault("method_counts", {})
    method_counts[method] = method_counts.get(method, 0) + 1

    latency_samples = observability_metrics.setdefault("latency_samples_ms", [])
    bounded_append(latency_samples, round(duration_ms, 2), OBSERVABILITY_LATENCY_SAMPLES)

    route_metrics = observability_metrics.setdefault("route_metrics", {})
    route_key = f"{method} {route_path}"
    route = route_metrics.setdefault(
        route_key,
        {
            "method": method,
            "path": route_path,
            "count": 0,
            "total_latency_ms": 0.0,
            "max_latency_ms": 0.0,
            "status_counts": {},
            "latency_samples_ms": [],
            "last_seen_at": None,
        },
    )
    route["count"] += 1
    route["total_latency_ms"] = round(float(route["total_latency_ms"]) + duration_ms, 2)
    route["max_latency_ms"] = round(max(float(route["max_latency_ms"]), duration_ms), 2)
    route["last_seen_at"] = now_iso()
    route["status_counts"][status_group] = route["status_counts"].get(status_group, 0) + 1
    bounded_append(route["latency_samples_ms"], round(duration_ms, 2), 50)

    if status_code >= 500:
        observability_metrics["error_requests"] = int(observability_metrics.get("error_requests", 0)) + 1
    if rate_limited:
        observability_metrics["rate_limited_requests"] = int(observability_metrics.get("rate_limited_requests", 0)) + 1

    recent_requests = observability_metrics.setdefault("recent_requests", [])
    bounded_append(
        recent_requests,
        {
            "method": method,
            "path": route_path,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
            "request_id": request_id,
            "rate_limited": rate_limited,
            "at": now_iso(),
        },
        OBSERVABILITY_RECENT_REQUESTS,
    )




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

    if next_status in RIDE_QUEUE_STATUSES:
        ride.setdefault("queued_at", changed_at)
    else:
        ride["queue_position"] = None
        ride["queue_size"] = None

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
        driver["availability"] = availability if driver_can_receive_requests(driver) else "offline"

def decode_auth_token(token: str | None) -> dict:
    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        role: str = payload.get("role")
        token_type = payload.get("typ", "access")
        if user_id is None or role is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        if token_type != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
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


def active_demand_count(vehicle_type: str | None = None) -> int:
    normalized_vehicle_type = vehicle_type if vehicle_type in VEHICLE_TYPES else None
    return sum(
        1
        for ride in rides.values()
        if ride.get("status") in {"matching", "pending_driver", "no_drivers_available"}
        and (normalized_vehicle_type is None or ride_vehicle_type(ride) == normalized_vehicle_type)
    )


def available_driver_count(vehicle_type: str | None = None) -> int:
    normalized_vehicle_type = vehicle_type if vehicle_type in VEHICLE_TYPES else None
    return sum(
        1
        for driver in drivers.values()
        if driver_can_receive_requests(driver)
        and driver.get("availability") == "available"
        and not driver.get("current_ride_id")
        and parse_location_payload(driver.get("location"))
        and (normalized_vehicle_type is None or driver_vehicle_type(driver) == normalized_vehicle_type)
    )


def demand_level_for(multiplier: float) -> str:
    if multiplier >= 1.75:
        return "peak"
    if multiplier >= 1.35:
        return "busy"
    if multiplier > 1:
        return "elevated"
    return "normal"


def surge_pricing_state(vehicle_type: str | None = None) -> dict:
    normalized_vehicle_type = vehicle_type if vehicle_type in VEHICLE_TYPES else None
    demand = active_demand_count(normalized_vehicle_type)
    supply = available_driver_count(normalized_vehicle_type)
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
        "vehicle_type": normalized_vehicle_type,
    }




def fare_breakdown_for(
    distance_km: float,
    duration_min: float,
    vehicle_type: str | None = None,
    promo_code: str | None = None,
) -> dict:
    normalized_vehicle_type = normalize_vehicle_type(vehicle_type)
    vehicle_config = vehicle_type_config(normalized_vehicle_type)
    distance_miles = max(0, distance_km) * 0.621371
    billable_minutes = max(0, duration_min)
    distance_charge = round(distance_miles * PER_MILE_RATE, 2)
    time_charge = round(billable_minutes * PER_MINUTE_RATE, 2)
    subtotal = round(BASE_FARE + distance_charge + time_charge, 2)
    surge = surge_pricing_state(normalized_vehicle_type)
    surge_multiplier = surge["surge_multiplier"]
    surge_charge = round(subtotal * (surge_multiplier - 1), 2)
    surged_subtotal = round(subtotal + surge_charge, 2)
    vehicle_multiplier = float(vehicle_config["fare_multiplier"])
    vehicle_charge = round(surged_subtotal * (vehicle_multiplier - 1), 2)
    vehicle_subtotal = round(surged_subtotal + vehicle_charge, 2)
    minimum_adjustment = round(max(MINIMUM_FARE - vehicle_subtotal, 0), 2)
    total = round(vehicle_subtotal + minimum_adjustment, 2)
    return apply_promo_to_breakdown(
        {
        "currency": FARE_CURRENCY,
        "base_fare": BASE_FARE,
        "distance_charge": distance_charge,
        "time_charge": time_charge,
        "subtotal": subtotal,
        "surge_multiplier": surge_multiplier,
        "surge_charge": surge_charge,
        "vehicle_type": normalized_vehicle_type,
        "vehicle_type_label": vehicle_config["label"],
        "vehicle_multiplier": vehicle_multiplier,
        "vehicle_charge": vehicle_charge,
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
        },
        promo_code,
    )



def create_mock_payment(
    ride_id: str,
    rider_id: str,
    amount: float,
    created_at: str,
    fare_breakdown: dict | None = None,
) -> dict:
    payment_id = f"mock_{uuid4().hex[:12]}"
    authorized_amount = money(amount)
    return {
        "id": payment_id,
        "ride_id": ride_id,
        "rider_id": rider_id,
        "amount": authorized_amount,
        "authorized_amount": authorized_amount,
        "original_amount": authorized_amount,
        "currency": FARE_CURRENCY,
        "method": "Wallet balance",
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
    if not payment or payment.get("status") in {"paid", "voided", "refunded"}:
        return

    captured_at = now_iso()
    transaction = wallet_transaction(
        ride["rider_id"],
        transaction_type="ride_payment",
        amount=-money(payment.get("amount")),
        description="Ride fare",
        ride_id=ride["id"],
        payment_id=payment["id"],
    )
    payment["status"] = "paid"
    payment["captured_at"] = captured_at
    payment["receipt_number"] = payment.get("receipt_number") or generate_receipt_number()
    payment["voided_at"] = None
    payment["wallet_transaction_id"] = transaction["id"]
    ride["payment"] = payment
    ride["updated_at"] = captured_at
    ensure_receipt_for_ride(ride)


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
    refund_amount = money(payment.get("amount"))
    transaction = wallet_transaction(
        ride["rider_id"],
        transaction_type="refund",
        amount=refund_amount,
        description="Ride refund",
        ride_id=ride["id"],
        payment_id=payment["id"],
    )
    refund = {
        "id": f"rfnd_{uuid4().hex[:12]}",
        "ride_id": ride["id"],
        "payment_id": payment["id"],
        "amount": refund_amount,
        "currency": payment.get("currency", FARE_CURRENCY),
        "status": "succeeded",
        "reason": reason,
        "created_at": refunded_at,
        "wallet_transaction_id": transaction["id"],
    }
    payment["status"] = "refunded"
    payment["refunded_at"] = refunded_at
    payment["refund"] = refund
    ride["payment"] = payment
    ride["refund"] = refund
    ride["updated_at"] = refunded_at
    ensure_receipt_for_ride(ride)
    return True


def build_route_estimate(
    distance_km: float,
    duration_min: float,
    route: list[dict],
    source: str,
    steps: list[dict] | None = None,
    vehicle_type: str | None = None,
    promo_code: str | None = None,
) -> dict:
    normalized_vehicle_type = normalize_vehicle_type(vehicle_type)
    normalized_promo_code = normalize_promo_code(promo_code)
    fare_breakdown = fare_breakdown_for(
        distance_km,
        duration_min,
        normalized_vehicle_type,
        normalized_promo_code,
    )
    return {
        "distance_km": round(distance_km, 2),
        "duration_min": max(1, round(duration_min)),
        "currency": FARE_CURRENCY,
        "fare": fare_breakdown["total"],
        "fare_breakdown": fare_breakdown,
        "vehicle_type": normalized_vehicle_type,
        "vehicle_type_label": vehicle_type_config(normalized_vehicle_type)["label"],
        "promo_code": normalized_promo_code,
        "route": route,
        "steps": steps or [],
        "source": source,
    }


def fallback_route_estimate(
    pickup: Location,
    destination: Location,
    vehicle_type: str | None = None,
    promo_code: str | None = None,
) -> dict:
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
        vehicle_type,
        promo_code,
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


def route_estimate_from_cached_payload(
    payload: dict,
    vehicle_type: str | None = None,
    promo_code: str | None = None,
) -> dict | None:
    try:
        return build_route_estimate(
            float(payload["distance_km"]),
            float(payload["duration_min"]),
            list(payload["route"]),
            str(payload.get("source") or "cache"),
            list(payload.get("steps") or []),
            vehicle_type,
            promo_code,
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
    ensure_driver_document_state(driver)
    rating_summary = rating_summary_for_user(driver["id"])
    vehicle = driver.get("vehicle")
    if isinstance(vehicle, dict):
        vehicle = {
            **vehicle,
            "type": driver_vehicle_type(driver),
            "type_label": vehicle_type_config(driver_vehicle_type(driver))["label"],
        }
    return {
        "id": driver["id"],
        "name": driver["name"],
        "phone": driver["phone"],
        "vehicle": vehicle,
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
    vehicle_type = ride_vehicle_type(ride)
    serialized["vehicle_type"] = vehicle_type
    serialized["vehicle_type_label"] = vehicle_type_config(vehicle_type)["label"]
    serialized["driver"] = public_driver(driver)
    return serialized


def public_shared_driver(driver: dict | None) -> dict | None:
    if not driver:
        return None
    ensure_driver_document_state(driver)
    rating_summary = rating_summary_for_user(driver["id"])
    vehicle = driver.get("vehicle")
    if isinstance(vehicle, dict):
        vehicle = {
            **vehicle,
            "type": driver_vehicle_type(driver),
            "type_label": vehicle_type_config(driver_vehicle_type(driver))["label"],
        }
    return {
        "name": driver["name"],
        "vehicle": vehicle,
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
        "vehicle_type": ride_vehicle_type(ride),
        "vehicle_type_label": vehicle_type_config(ride_vehicle_type(ride))["label"],
        "driver_location": ride.get("driver_location"),
        "rider_location": ride.get("rider_location"),
        "driver": public_shared_driver(driver),
        "created_at": ride.get("created_at"),
        "updated_at": ride.get("updated_at"),
        "scheduled_for": ride.get("scheduled_for"),
        "matched_at": ride.get("matched_at"),
        "accepted_at": ride.get("accepted_at"),
        "arrived_at": ride.get("arrived_at"),
        "started_at": ride.get("started_at"),
        "completed_at": ride.get("completed_at"),
        "cancelled_at": ride.get("cancelled_at"),
    }


def safe_user(user: dict) -> dict:
    normalized_account_status(user)
    if user.get("role") == "driver":
        ensure_driver_document_state(user)
    if user.get("role") == "rider":
        ensure_rider_wallet(user)
    if user.get("role") == "admin":
        normalized_admin_role(user)
        admin_permissions_for(user)
    serialized = {k: v for k, v in user.items() if k != "password_hash"}
    if user.get("role") == "rider":
        serialized["wallet"] = public_wallet(user)
    serialized["permissions"] = permissions_for_user_record(user)
    serialized.update(rating_summary_for_user(user["id"]))
    return serialized


def require_role(current_user: dict, role: str) -> str:
    if current_user["role"] != role:
        record_audit_event(
            action="access.role_denied",
            actor=current_user,
            target_type="role",
            target_id=role,
            outcome="denied",
        )
        raise HTTPException(status_code=403, detail=f"{role.title()} account required")
    user = user_for_auth(current_user.get("user_id"), current_user.get("role"))
    if user:
        ensure_account_active(user)
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
    await dispatch_push_notification(notification)


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
    await dispatch_push_notification(notification)


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
    vehicle_type = ride_vehicle_type(ride)
    return {
        "id": ride["id"],
        "status": ride.get("status"),
        "rider": user_summary(riders.get(ride.get("rider_id"))),
        "driver": user_summary(drivers.get(ride.get("driver_id"))),
        "pickup": ride.get("pickup"),
        "destination": ride.get("destination"),
        "driver_location": ride.get("driver_location"),
        "rider_location": ride.get("rider_location"),
        "dispatch_expires_at": ride.get("dispatch_expires_at"),
        "dispatch_timeout_seconds": ride.get("dispatch_timeout_seconds"),
        "distance_km": ride.get("distance_km"),
        "duration_min": ride.get("duration_min"),
        "vehicle_type": vehicle_type,
        "vehicle_type_label": vehicle_type_config(vehicle_type)["label"],
        "fare": ride.get("fare"),
        "currency": ride.get("currency", FARE_CURRENCY),
        "payment_status": payment.get("status"),
        "fraud_assessment": ride.get("fraud_assessment"),
        "queue_position": ride.get("queue_position"),
        "queue_size": ride.get("queue_size"),
        "scheduled_for": ride.get("scheduled_for"),
        "created_at": ride.get("created_at"),
        "updated_at": ride.get("updated_at"),
    }


def admin_driver_summary(driver: dict) -> dict:
    document_verification = ensure_driver_document_state(driver)
    earnings = build_driver_earnings(driver["id"])
    rating_summary = rating_summary_for_user(driver["id"])
    vehicle = driver.get("vehicle")
    if isinstance(vehicle, dict):
        vehicle = {
            **vehicle,
            "type": driver_vehicle_type(driver),
            "type_label": vehicle_type_config(driver_vehicle_type(driver))["label"],
        }
    return {
        "id": driver["id"],
        "name": driver["name"],
        "email": driver["email"],
        "phone": driver.get("phone", ""),
        "account_status": normalized_account_status(driver),
        "availability": driver.get("availability"),
        "current_ride_id": driver.get("current_ride_id"),
        "location": driver.get("location"),
        "vehicle": vehicle,
        "onboarding_status": driver.get("onboarding_status"),
        "document_verification": document_verification,
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
        document_verification = ensure_driver_document_state(summary)
        earnings = build_driver_earnings(summary["id"])
        vehicle = summary.get("vehicle")
        if isinstance(vehicle, dict):
            vehicle = {
                **vehicle,
                "type": driver_vehicle_type(summary),
                "type_label": vehicle_type_config(driver_vehicle_type(summary))["label"],
            }
        result.update(
            {
                "availability": summary.get("availability"),
                "current_ride_id": summary.get("current_ride_id"),
                "vehicle": vehicle,
                "location": summary.get("location"),
                "onboarding_status": summary.get("onboarding_status"),
                "document_verification": document_verification,
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


def build_admin_dashboard(current_user: dict | None = None) -> dict:
    refresh_ride_queue()
    current_permissions = permissions_for_current_user(current_user) if current_user else []
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
    platform_fee_total = round(
        sum(
            money(payment.get("amount"))
            if payment.get("cancellation_fee")
            else money(payment.get("amount")) * PLATFORM_FEE_RATE
            for payment in payments
            if payment.get("status") == "paid"
        ),
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
            "platform_fee_total": platform_fee_total,
            "refund_total": refund_total,
            "platform_fee_rate": PLATFORM_FEE_RATE,
        },
        "fraud": (
            build_admin_fraud_summary()
            if "admin.fraud.read" in current_permissions
            else {"total_events": 0, "open_reviews": 0, "blocked_requests": 0, "risk_counts": {}, "top_signals": [], "recent_events": []}
        ),
        "audit": (
            build_audit_summary()
            if "admin.audit.read" in current_permissions
            else {"total_events": 0, "action_counts": {}, "outcome_counts": {}, "recent_events": []}
        ),
        "observability": (
            build_observability_dashboard()
            if "admin.observability.read" in current_permissions
            else None
        ),
        "access_control": build_access_control_summary(current_user),
        "demand": surge_pricing_state(),
        "demand_heatmap": build_demand_heatmap(),
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
                LIMIT 100
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
        if not driver_can_receive_requests(driver):
            continue
        if driver.get("availability") != "available" or driver.get("current_ride_id"):
            continue
        if not driver_matches_ride_vehicle(driver, ride):
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
        if not driver_can_receive_requests(driver):
            continue
        if driver["id"] in declined:
            continue
        if driver.get("availability") != "available":
            continue
        if driver.get("current_ride_id"):
            continue
        if not driver_matches_ride_vehicle(driver, ride):
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
    global redis_client, redis_subscriber_task, scheduled_rides_task

    await initialize_database()
    refresh_ride_queue()
    scheduled_rides_task = asyncio.create_task(scheduled_ride_loop())

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
    global redis_client, redis_subscriber_task, scheduled_rides_task

    if scheduled_rides_task:
        scheduled_rides_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduled_rides_task
        scheduled_rides_task = None

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
    if ride.get("status") == "scheduled":
        return None

    candidates = await available_driver_candidates(ride)
    if not candidates:
        changed = set_ride_status(ride, "no_drivers_available", "system", allow_same=True)
        ride["driver_id"] = None
        ride["driver_distance_km"] = None
        ride["driver_location"] = None
        ride["dispatch_expires_at"] = None
        ride.setdefault("queued_at", ride.get("created_at") or now_iso())
        refresh_ride_queue()
        if changed:
            vehicle_label = vehicle_type_config(ride_vehicle_type(ride))["label"]
            await notify_rider(
                ride,
                "Ride queued",
                f"No nearby {vehicle_label} drivers are available right now. We will match the next available driver.",
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
    ride["dispatch_expires_at"] = dispatch_deadline_for(matched_at)
    ride["dispatch_timeout_seconds"] = DISPATCH_REQUEST_TIMEOUT_SECONDS
    ride["updated_at"] = matched_at
    clear_ride_queue_metadata(ride)
    refresh_ride_queue()

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




async def update_driver_location_state(driver_id: str, location: dict) -> dict | None:
    driver = drivers.get(driver_id)
    if not driver:
        return None
    if not is_account_active(driver):
        driver["availability"] = "offline"
        driver["current_ride_id"] = None
        await persist_runtime_state()
        return driver
    active_ride = active_ride_for_driver(driver_id)
    if not driver_documents_verified(driver) and not active_ride:
        driver["availability"] = "offline"
        driver["current_ride_id"] = None
        await persist_runtime_state()
        return driver

    now = datetime.now(timezone.utc).isoformat()
    driver["location"] = location
    driver["last_location_at"] = now

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

    if online and not driver_documents_verified(driver):
        driver["availability"] = "offline"
        await persist_runtime_state()
        raise HTTPException(status_code=403, detail="Driver documents must be verified before going online")

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
            active_ride["dispatch_expires_at"] = None
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


@app.get("/vehicle-types")
def get_vehicle_types():
    return public_vehicle_types()


@app.post("/riders/signup")
async def rider_signup(payload: RiderSignup, request: Request):
    for rider in riders.values():
        if rider["email"] == payload["email"]:
            record_audit_event(
                action="auth.rider_signup",
                outcome="failure",
                metadata={"email": payload["email"], "reason": "duplicate_email"},
                request=request,
            )
            raise HTTPException(status_code=400, detail="Email already registered")

    rider_id = str(uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    rider = {
        "id": rider_id,
        "email": payload["email"],
        "password_hash": hash_password(payload["password"]),
        "name": payload["name"],
        "phone": payload["phone"],
        "role": "rider",
        "account_status": "active",
        "wallet": default_rider_wallet(created_at),
        "created_at": created_at,
    }
    riders[rider_id] = rider
    record_audit_event(
        action="auth.rider_signup",
        actor=rider,
        target_type="rider",
        target_id=rider_id,
        request=request,
    )
    await persist_runtime_state()
    return safe_user(rider)


@app.post("/riders/login")
async def rider_login(payload: LoginRequest, request: Request):
    for rider in riders.values():
        if rider["email"] == payload["email"]:
            if verify_password(payload["password"], rider["password_hash"]):
                ensure_account_active(rider)
                response = auth_response(rider, "rider", request)
                record_audit_event(
                    action="auth.login",
                    actor=rider,
                    target_type="rider",
                    target_id=rider["id"],
                    metadata={"role": "rider"},
                    request=request,
                )
                await persist_runtime_state()
                return response
            else:
                record_audit_event(
                    action="auth.login",
                    outcome="failure",
                    target_type="rider",
                    metadata={"email": payload["email"], "reason": "invalid_password"},
                    request=request,
                )
                raise HTTPException(status_code=401, detail="Invalid password")
    record_audit_event(
        action="auth.login",
        outcome="failure",
        target_type="rider",
        metadata={"email": payload["email"], "reason": "account_not_found"},
        request=request,
    )
    raise HTTPException(status_code=404, detail="Account not found")

@app.post("/drivers/signup")
async def driver_signup(payload: DriverSignup, request: Request):
    for driver in drivers.values():
        if driver["email"] == payload["email"]:
            record_audit_event(
                action="auth.driver_signup",
                outcome="failure",
                metadata={"email": payload["email"], "reason": "duplicate_email"},
                request=request,
            )
            raise HTTPException(status_code=400, detail="Email already registered")

    vehicle_type = normalize_vehicle_type(payload.get("vehicle_type"))
    driver_id = str(uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
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
            "type": vehicle_type,
            "type_label": vehicle_type_config(vehicle_type)["label"],
        },
        "vehicle_type": vehicle_type,
        "license_number": payload["license_number"],
        "document_verification": {
            "documents": default_driver_documents(created_at, status="pending_review"),
        },
        "onboarding_status": "pending_review",
        "availability": "offline",
        "location": None,
        "current_ride_id": None,
        "stats": {"accepted": 0, "rejected": 0, "timed_out": 0},
        "created_at": created_at,
    }
    refresh_driver_document_summary(driver)
    drivers[driver_id] = driver

    record_audit_event(
        action="auth.driver_signup",
        actor=driver,
        target_type="driver",
        target_id=driver_id,
        request=request,
    )
    await persist_runtime_state()
    return safe_user(driver)


@app.post("/drivers/login")
async def driver_login(payload: LoginRequest, request: Request):
    for driver in drivers.values():
        if driver["email"] == payload["email"]:
            if verify_password(payload["password"], driver["password_hash"]):
                ensure_account_active(driver)
                response = auth_response(driver, "driver", request)
                record_audit_event(
                    action="auth.login",
                    actor=driver,
                    target_type="driver",
                    target_id=driver["id"],
                    metadata={"role": "driver"},
                    request=request,
                )
                await persist_runtime_state()
                return response
            else:
                record_audit_event(
                    action="auth.login",
                    outcome="failure",
                    target_type="driver",
                    metadata={"email": payload["email"], "reason": "invalid_password"},
                    request=request,
                )
                raise HTTPException(status_code=401, detail="Invalid password")
    record_audit_event(
        action="auth.login",
        outcome="failure",
        target_type="driver",
        metadata={"email": payload["email"], "reason": "account_not_found"},
        request=request,
    )
    raise HTTPException(status_code=404, detail="Account not found")


@app.post("/admin/login")
async def admin_login(payload: LoginRequest, request: Request):
    ensure_default_admin()
    for admin in admins.values():
        if admin["email"] == payload["email"]:
            if verify_password(payload["password"], admin["password_hash"]):
                ensure_account_active(admin)
                response = auth_response(admin, "admin", request)
                record_audit_event(
                    action="auth.login",
                    actor=admin,
                    target_type="admin",
                    target_id=admin["id"],
                    metadata={"role": "admin", "admin_role": normalized_admin_role(admin)},
                    request=request,
                )
                await persist_runtime_state()
                return response
            record_audit_event(
                action="auth.login",
                outcome="failure",
                target_type="admin",
                metadata={"email": payload["email"], "reason": "invalid_password"},
                request=request,
            )
            raise HTTPException(status_code=401, detail="Invalid password")
    record_audit_event(
        action="auth.login",
        outcome="failure",
        target_type="admin",
        metadata={"email": payload["email"], "reason": "account_not_found"},
        request=request,
    )
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
    require_permission(current_user, "admin.users.manage")
    status = payload.get("status")
    if status not in ACCOUNT_STATUSES:
        raise HTTPException(status_code=400, detail="Status must be active or suspended")

    user = admin_target_user(role, user_id)
    previous_status = normalized_account_status(user)
    if role == "driver" and status == "suspended":
        await set_driver_availability_state(user_id, False)

    user["account_status"] = status
    user["updated_at"] = now_iso()
    if role == "driver" and status == "suspended":
        user["availability"] = "offline"

    record_audit_event(
        action="admin.user_status.updated",
        actor=current_user,
        target_type=role,
        target_id=user_id,
        metadata={"previous_status": previous_status, "status": status},
    )
    await persist_runtime_state()
    return admin_user_summary(user)


@app.post("/admin/drivers/{driver_id}/force-offline")
async def force_admin_driver_offline(
    driver_id: str,
    current_user: dict = Depends(get_current_user),
):
    require_permission(current_user, "admin.drivers.manage")
    driver = drivers.get(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    await set_driver_availability_state(driver_id, False)
    driver["updated_at"] = now_iso()
    record_audit_event(
        action="admin.driver.force_offline",
        actor=current_user,
        target_type="driver",
        target_id=driver_id,
    )
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
async def get_driver_ride_request(current_user: dict = Depends(get_current_user)):
    driver_id = require_role(current_user, "driver")
    await assign_waiting_rides()
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


async def estimate_route_with_osrm(
    pickup: Location,
    destination: Location,
    vehicle_type: str | None = None,
    promo_code: str | None = None,
) -> dict | None:
    coords = f"{pickup['lng']},{pickup['lat']};{destination['lng']},{destination['lat']}"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{OSRM_BASE_URL}/route/v1/{OSRM_PROFILE}/{coords}",
                params={
                    "overview": "full",
                    "geometries": "geojson",
                    "steps": "true",
                },
                headers={
                    "User-Agent": "MyUber-Dev/1.0",
                },
                timeout=ROUTING_TIMEOUT_SECONDS,
            )
    except httpx.HTTPError:
        return None

    if response.status_code != 200:
        return None

    try:
        data = response.json()
    except ValueError:
        return None

    routes = data.get("routes", [])
    if not routes:
        return None

    route = routes[0]
    geometry = (route.get("geometry") or {}).get("coordinates", [])
    route_points = route_points_from_coordinates(geometry)

    if len(route_points) < 2:
        return None

    return build_route_estimate(
        (route.get("distance") or 0) / 1000,
        (route.get("duration") or 0) / 60,
        route_points,
        "osrm",
        steps_from_osrm_legs(route.get("legs", [])),
        vehicle_type,
        promo_code,
    )





@app.post("/routes/estimate")
async def estimate_route(payload: RouteEstimateRequest):
    vehicle_type = normalize_vehicle_type(payload.get("vehicle_type"))
    promo_code = normalize_promo_code(payload.get("promo_code"))
    return await calculate_route_estimate(
        payload["pickup"],
        payload["destination"],
        vehicle_type,
        promo_code,
    )




    rides[ride_id] = ride
    record_fraud_event(
        fraud_assessment,
        "review_required" if fraud_assessment["review_required"] else "allowed",
    )
    record_audit_event(
        action="ride.request.created",
        actor=current_user,
        target_type="ride",
        target_id=ride_id,
        metadata={
            "status": initial_status,
            "vehicle_type": vehicle_type,
            "fare": estimate["fare"],
            "risk_level": fraud_assessment["risk_level"],
        },
    )
    if scheduled_for:
        await notify_rider(
            ride,
            "Ride scheduled",
            f"Your {vehicle_type_config(vehicle_type)['label']} ride is scheduled for {scheduled_for}.",
            "ride_scheduled",
        )
    else:
        await assign_waiting_rides()
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
async def get_ride_history(current_user: dict = Depends(get_current_user)):
    await assign_waiting_rides()
    return ride_history_for_user(current_user)


@app.get("/rides/{ride_id}")
async def get_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    await assign_waiting_rides()
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
        record_audit_event(
            action="ride.share.created",
            actor=current_user,
            target_type="ride",
            target_id=ride_id,
            metadata={"share_token": share_token},
        )
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
    record_audit_event(
        action="ride.issue_reported",
        actor=current_user,
        target_type="ride",
        target_id=ride_id,
        metadata={"issue_id": report["id"], "category": category},
    )

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
    if ride.get("driver_id") == driver_id and ride_dispatch_expired(ride):
        await expire_dispatch_timeouts()
        raise HTTPException(status_code=409, detail="Ride request expired")
    if ride.get("driver_id") != driver_id or ride.get("status") != "pending_driver":
        raise HTTPException(status_code=409, detail="Ride is not assigned to this driver")

    stats = driver.setdefault("stats", {"accepted": 0, "rejected": 0})
    driver["availability"] = "busy"
    driver["current_ride_id"] = ride_id
    stats["accepted"] += 1
    ride["dispatch_expires_at"] = None
    set_ride_status(ride, "accepted", "driver")
    record_audit_event(
        action="ride.driver.accepted",
        actor=current_user,
        target_type="ride",
        target_id=ride_id,
        metadata={"driver_id": driver_id},
    )
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
    driver["availability"] = "available" if driver_can_receive_requests(driver) else "offline"
    driver["current_ride_id"] = None
    stats["rejected"] += 1
    ride["dispatch_expires_at"] = None
    set_ride_status(ride, "matching", "driver")
    record_audit_event(
        action="ride.driver.rejected",
        actor=current_user,
        target_type="ride",
        target_id=ride_id,
        metadata={"driver_id": driver_id},
    )

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
    record_audit_event(
        action="ride.status.updated",
        actor=current_user,
        target_type="ride",
        target_id=ride_id,
        metadata={"status": next_status},
    )
    if next_status == "completed":
        capture_mock_payment(ride)
        await send_receipt_email_for_ride(ride, "trip_completed")
        release_driver_for_ride(ride)
        await notify_rider(
            ride,
            "Trip complete",
            "Your trip is complete and the receipt is ready.",
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
    record_audit_event(
        action="ride.payment.refunded",
        actor=current_user,
        target_type="ride",
        target_id=ride_id,
        metadata={"payment_id": (ride.get("payment") or {}).get("id")},
    )
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
    previous_status = ride.get("status")
    cancellation_fee = cancellation_fee_for_ride(ride, actor, previous_status)
    set_ride_status(ride, "cancelled", actor)
    ride["dispatch_expires_at"] = None
    capture_cancellation_fee(ride, cancellation_fee, actor)
    record_audit_event(
        action="ride.cancelled",
        actor=current_user,
        target_type="ride",
        target_id=ride_id,
        metadata={
            "previous_status": previous_status,
            "cancelled_by": actor,
            "cancellation_fee": cancellation_fee,
        },
    )
    if cancellation_fee > 0:
        await send_receipt_email_for_ride(ride, "cancellation_fee")
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
            (
                "The rider cancelled this trip. A cancellation fee was charged."
                if cancellation_fee > 0
                else "The rider cancelled this trip."
            ),
            "ride_cancelled",
            ride_id,
        )
        if cancellation_fee > 0:
            await notify_rider(
                ride,
                "Cancellation fee charged",
                f"A {FARE_CURRENCY} {cancellation_fee:.2f} cancellation fee was charged to your wallet.",
                "cancellation_fee_charged",
            )

    await broadcast_ride(ride)
    if driver_id:
        await broadcast_driver(driver_id, {"type": "ride_update", "ride": public_ride(ride)})
        await broadcast_driver(driver_id, {"type": "ride_cleared", "ride_id": ride_id})

    await assign_waiting_rides()
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
