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
import httpx
import json
import logging
import os
from math import asin, cos, radians, sin, sqrt

try:
    import redis.asyncio as redis
except ImportError:
    redis = None

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
REDIS_URL = os.getenv("REDIS_URL")
REDIS_CHANNEL = os.getenv("REDIS_CHANNEL", "myuber:realtime")

logger = logging.getLogger("myuber")

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/riders/login", auto_error=False)

riders: dict[str, dict] = {}
drivers: dict[str, dict] = {}
rides: dict[str, dict] = {}
ride_connections: dict[str, list[WebSocket]] = {}
driver_connections: dict[str, list[WebSocket]] = {}
DEFAULT_DRIVER_LOCATION = {"lat": 51.5074, "lng": -0.1278}
redis_client = None
redis_subscriber_task: asyncio.Task | None = None

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

location_adapter = TypeAdapter(Location)



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


def fare_for(distance_km: float, duration_min: float) -> float:
    distance_miles = distance_km * 0.621371
    fare = 3.50 + (distance_miles * 1.65) + (duration_min * 0.28)
    return round(max(fare, 7.00), 2)


def build_route_estimate(
    distance_km: float,
    duration_min: float,
    route: list[dict],
    source: str,
    steps: list[dict] | None = None,
) -> dict:
    return {
        "distance_km": round(distance_km, 2),
        "duration_min": max(1, round(duration_min)),
        "fare": fare_for(distance_km, duration_min),
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


def public_driver(driver: dict | None) -> dict | None:
    if not driver:
        return None
    return {
        "id": driver["id"],
        "name": driver["name"],
        "phone": driver["phone"],
        "vehicle": driver.get("vehicle"),
        "location": driver.get("location"),
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


def safe_user(user: dict) -> dict:
    return {k: v for k, v in user.items() if k != "password_hash"}


def require_role(current_user: dict, role: str) -> str:
    if current_user["role"] != role:
        raise HTTPException(status_code=403, detail=f"{role.title()} account required")
    return current_user["user_id"]


def can_view_ride(current_user: dict, ride: dict) -> bool:
    if current_user["role"] == "rider":
        return ride.get("rider_id") == current_user["user_id"]
    if current_user["role"] == "driver":
        return ride.get("driver_id") == current_user["user_id"]
    return False


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


def available_driver_candidates(ride: dict) -> list[tuple[float, dict]]:
    pickup = parse_location_payload(ride.get("pickup"))
    if not pickup:
        return []
    declined = set(ride.get("declined_driver_ids", []))
    candidates: list[tuple[float, dict]] = []

    for driver in drivers.values():
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



async def broadcast_ride(ride: dict) -> None:
    await fanout_realtime_event(
        "ride",
        ride["id"],
        {"type": "ride_update", "ride": public_ride(ride)},
    )


async def broadcast_driver(driver_id: str, message: dict) -> None:
    await fanout_realtime_event("driver", driver_id, message)


async def assign_nearest_driver(ride: dict) -> dict | None:
    if ride.get("status") in TERMINAL_RIDE_STATUSES:
        return None

    candidates = available_driver_candidates(ride)
    if not candidates:
        set_ride_status(ride, "no_drivers_available", "system", allow_same=True)
        ride["driver_id"] = None
        ride["driver_distance_km"] = None
        ride["driver_location"] = None
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
        return driver

    if driver.get("availability") == "offline":
        return driver

    if driver.get("availability") != "busy":
        driver["availability"] = "available"
    await assign_waiting_rides()
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
    return ride

@app.get("/")
def root():
    return {"status": "MyUber API running"}

@app.post("/riders/signup")
def rider_signup(payload: RiderSignup):
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
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    riders[rider_id] = rider
    return safe_user(rider)


@app.post("/riders/login")
def rider_login(payload: LoginRequest):
    for rider in riders.values():
        if rider["email"] == payload["email"]:
            if verify_password(payload["password"], rider["password_hash"]):
                token = create_token({"sub": rider["id"], "role": "rider"})
                return {"token": token, "user": safe_user(rider)}
            else:
                raise HTTPException(status_code=401, detail="Invalid password")
    raise HTTPException(status_code=404, detail="Account not found")

@app.post("/drivers/signup")
def driver_signup(payload: DriverSignup):
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

    return safe_user(driver)


@app.post("/drivers/login")
def driver_login(payload: LoginRequest):
    for driver in drivers.values():
        if driver["email"] == payload["email"]:
            if verify_password(payload["password"], driver["password_hash"]):
                token = create_token({"sub": driver["id"], "role": "driver"})
                return {"token": token, "user": safe_user(driver)}
            else:
                raise HTTPException(status_code=401, detail="Invalid password")
    raise HTTPException(status_code=404, detail="Account not found")

@app.get("/auth/me")
def get_me(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    role = current_user["role"]

    if role == "rider":
        user = riders.get(user_id)
    else:
        user = drivers.get(user_id)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return safe_user(user)


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

@app.get("/search/locations")
async def search_locations(q: str):
    if not q or len(q) < 2:
        return []

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": q,
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
    return [
        {
            "name": r.get("display_name", ""),
            "lat": float(r.get("lat", 0)),
            "lng": float(r.get("lon", 0)),
        }
        for r in results
    ]


@app.post("/routes/estimate")
async def estimate_route(payload: RouteEstimateRequest):
    pickup = payload["pickup"]
    destination = payload["destination"]
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

    return build_route_estimate(
        (route.get("distance") or 0) / 1000,
        (route.get("duration") or 0) / 60,
        route_points,
        "osrm",
        steps_from_osrm_legs(route.get("legs", [])),
    )

@app.post("/rides")
async def request_ride(
    payload: RideRequest,
    current_user: dict = Depends(get_current_user),
):
    rider_id = require_role(current_user, "rider")
    if payload["rider_id"] != rider_id:
        raise HTTPException(status_code=403, detail="Cannot request for another rider")

    now = now_iso()
    ride_id = str(uuid4())
    ride = {
        "id": ride_id,
        "rider_id": rider_id,
        "pickup": payload["pickup"],
        "destination": payload["destination"],
        "status": "matching",
        "driver_id": None,
        "driver_distance_km": None,
        "driver_location": None,
        "rider_location": None,
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


@app.get("/rides/{ride_id}")
def get_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    ride = rides.get(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if not can_view_ride(current_user, ride):
        raise HTTPException(status_code=403, detail="Cannot view this ride")

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

    await broadcast_ride(ride)
    await broadcast_driver(driver_id, {"type": "ride_update", "ride": public_ride(ride)})
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
