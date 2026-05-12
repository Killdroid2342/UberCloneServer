from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
import bcrypt
import httpx

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

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/riders/login", auto_error=False)

riders: dict[str, dict] = {}
drivers: dict[str, dict] = {}
rides: dict[str, dict] = {}
connections: list[WebSocket] = []

class Location(BaseModel):
    lat: float
    lng: float


class RiderSignup(BaseModel):
    email: str
    password: str
    name: str
    phone: str


class DriverSignup(BaseModel):
    email: str
    password: str
    name: str
    phone: str
    vehicle_make: str
    vehicle_model: str
    vehicle_year: int
    vehicle_color: str
    vehicle_plate: str
    license_number: str


class LoginRequest(BaseModel):
    email: str
    password: str


class RideRequest(BaseModel):
    rider_id: str
    pickup: Location
    destination: Location

def create_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme)):
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

@app.get("/")
def root():
    return {"status": "MyUber API running"}

@app.post("/riders/signup")
def rider_signup(payload: RiderSignup):
    for rider in riders.values():
        if rider["email"] == payload.email:
            raise HTTPException(status_code=400, detail="Email already registered")

    rider_id = str(uuid4())
    rider = {
        "id": rider_id,
        "email": payload.email,
        "password_hash": hash_password(payload.password),
        "name": payload.name,
        "phone": payload.phone,
        "role": "rider",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    riders[rider_id] = rider
    safe = {k: v for k, v in rider.items() if k != "password_hash"}
    return safe


@app.post("/riders/login")
def rider_login(payload: LoginRequest):
    for rider in riders.values():
        if rider["email"] == payload.email:
            if verify_password(payload.password, rider["password_hash"]):
                token = create_token({"sub": rider["id"], "role": "rider"})
                safe = {k: v for k, v in rider.items() if k != "password_hash"}
                return {"token": token, "user": safe}
            else:
                raise HTTPException(status_code=401, detail="Invalid password")
    raise HTTPException(status_code=404, detail="Account not found")

@app.post("/drivers/signup")
def driver_signup(payload: DriverSignup):
    for driver in drivers.values():
        if driver["email"] == payload.email:
            raise HTTPException(status_code=400, detail="Email already registered")

    driver_id = str(uuid4())
    driver = {
        "id": driver_id,
        "email": payload.email,
        "password_hash": hash_password(payload.password),
        "name": payload.name,
        "phone": payload.phone,
        "role": "driver",
        "vehicle": {
            "make": payload.vehicle_make,
            "model": payload.vehicle_model,
            "year": payload.vehicle_year,
            "color": payload.vehicle_color,
            "plate": payload.vehicle_plate,
        },
        "license_number": payload.license_number,
        "onboarding_status": "complete",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    drivers[driver_id] = driver

    safe = {k: v for k, v in driver.items() if k != "password_hash"}
    return safe




@app.post("/rides")
def request_ride(payload: RideRequest):
    ride_id = str(uuid4())

    ride = {
        "id": ride_id,
        "rider_id": payload.rider_id,
        "pickup": payload.pickup.model_dump(),
        "destination": payload.destination.model_dump(),
        "status": "requested",
        "driver_location": None,
    }

    rides[ride_id] = ride
    return ride


@app.get("/rides/{ride_id}")
def get_ride(ride_id: str):
    return rides.get(ride_id, {"error": "Ride not found"})

@app.websocket("/ws/rides/{ride_id}")
async def ride_socket(websocket: WebSocket, ride_id: str):
    await websocket.accept()
    connections.append(websocket)

    try:
        while True:
            data = await websocket.receive_json()

            if data["type"] == "driver_location":
                rides[ride_id]["driver_location"] = data["location"]

                message = {
                    "type": "ride_update",
                    "ride": rides[ride_id],
                }

                for conn in connections:
                    await conn.send_json(message)

    except WebSocketDisconnect:
        connections.remove(websocket)