from datetime import datetime, timedelta, timezone


DEMO_PASSWORD = "myuber-demo-pass"

POINTS = {
    "kings_cross": {"lat": 51.5308, "lng": -0.1238},
    "covent_garden": {"lat": 51.5117, "lng": -0.1240},
    "soho": {"lat": 51.5136, "lng": -0.1365},
    "canary_wharf": {"lat": 51.5054, "lng": -0.0235},
    "paddington": {"lat": 51.5154, "lng": -0.1755},
    "heathrow": {"lat": 51.4700, "lng": -0.4543},
    "greenwich": {"lat": 51.4826, "lng": -0.0077},
}


def seed_time(**delta_kwargs) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta_kwargs)).isoformat()


def find_by_email(store: dict, email: str) -> dict | None:
    normalized = email.lower()
    for record in store.values():
        if str(record.get("email", "")).lower() == normalized:
            return record
    return None


def reset_runtime_state(myuber) -> None:
    for store_name in (
        "riders",
        "drivers",
        "admins",
        "rides",
        "issue_reports",
        "notifications",
        "push_subscriptions",
        "trip_shares",
        "refresh_tokens",
        "fraud_events",
        "audit_logs",
    ):
        getattr(myuber, store_name).clear()
    myuber.ensure_default_admin()


def ensure_admin(myuber, admin_id: str, *, email: str, name: str, admin_role: str) -> dict:
    admin = myuber.admins.get(admin_id) or find_by_email(myuber.admins, email) or {}
    created_at = admin.get("created_at") or seed_time(days=-10)
    admin.update(
        {
            "id": admin.get("id") or admin_id,
            "email": email,
            "password_hash": myuber.hash_password(DEMO_PASSWORD),
            "name": name,
            "phone": "+44 20 7946 0900",
            "role": "admin",
            "admin_role": admin_role,
            "account_status": "active",
            "created_at": created_at,
        }
    )
    myuber.admin_permissions_for(admin)
    myuber.admins[admin["id"]] = admin
    return admin


def demo_wallet(myuber, balance: float, created_at: str) -> dict:
    amount = myuber.money(balance)
    return {
        "currency": myuber.FARE_CURRENCY,
        "balance": amount,
        "transactions": [
            {
                "id": "seed_wallet_credit",
                "type": "starter_credit",
                "amount": amount,
                "currency": myuber.FARE_CURRENCY,
                "balance_after": amount,
                "description": "Demo wallet credit",
                "ride_id": None,
                "payment_id": None,
                "created_at": created_at,
            }
        ],
    }


def ensure_rider(myuber, rider_id: str, *, email: str, name: str, phone: str, balance: float) -> dict:
    rider = myuber.riders.get(rider_id) or find_by_email(myuber.riders, email) or {}
    created_at = rider.get("created_at") or seed_time(days=-9)
    rider.update(
        {
            "id": rider.get("id") or rider_id,
            "email": email,
            "password_hash": myuber.hash_password(DEMO_PASSWORD),
            "name": name,
            "phone": phone,
            "role": "rider",
            "account_status": "active",
            "wallet": rider.get("wallet") if isinstance(rider.get("wallet"), dict) else demo_wallet(myuber, balance, created_at),
            "created_at": created_at,
        }
    )
    myuber.riders[rider["id"]] = rider
    return rider


def ensure_driver(
    myuber,
    driver_id: str,
    *,
    email: str,
    name: str,
    phone: str,
    vehicle: dict,
    location: dict,
    document_status: str = "verified",
    availability: str = "available",
) -> dict:
    driver = myuber.drivers.get(driver_id) or find_by_email(myuber.drivers, email) or {}
    created_at = driver.get("created_at") or seed_time(days=-8)
    vehicle_type = myuber.normalize_vehicle_type(vehicle.get("type"))
    driver.update(
        {
            "id": driver.get("id") or driver_id,
            "email": email,
            "password_hash": myuber.hash_password(DEMO_PASSWORD),
            "name": name,
            "phone": phone,
            "role": "driver",
            "account_status": "active",
            "vehicle": {
                "make": vehicle["make"],
                "model": vehicle["model"],
                "year": vehicle["year"],
                "color": vehicle["color"],
                "plate": vehicle["plate"],
                "type": vehicle_type,
                "type_label": myuber.vehicle_type_config(vehicle_type)["label"],
            },
            "vehicle_type": vehicle_type,
            "license_number": vehicle["license_number"],
            "document_verification": {
                "documents": myuber.default_driver_documents(created_at, status=document_status),
            },
            "availability": availability if document_status == "verified" else "offline",
            "location": location,
            "last_location_at": seed_time(minutes=-4),
            "current_ride_id": None,
            "stats": driver.get("stats") if isinstance(driver.get("stats"), dict) else {"accepted": 4, "rejected": 1, "timed_out": 0},
            "created_at": created_at,
        }
    )
    myuber.refresh_driver_document_summary(driver)
    myuber.drivers[driver["id"]] = driver
    return driver


def base_ride(myuber, ride_id: str, *, rider: dict, pickup: dict, destination: dict, vehicle_type: str, promo_code: str | None = None) -> dict:
    created_at = seed_time(hours=-3)
    estimate = myuber.fallback_route_estimate(pickup, destination, vehicle_type, promo_code)
    return {
        "id": ride_id,
        "rider_id": rider["id"],
        "pickup": pickup,
        "destination": destination,
        "vehicle_type": estimate["vehicle_type"],
        "vehicle_type_label": estimate["vehicle_type_label"],
        "promo_code": estimate["promo_code"],
        "scheduled_for": None,
        "distance_km": estimate["distance_km"],
        "duration_min": estimate["duration_min"],
        "currency": estimate["currency"],
        "fare": estimate["fare"],
        "fare_breakdown": estimate["fare_breakdown"],
        "status": "matching",
        "driver_id": None,
        "driver_distance_km": None,
        "driver_location": None,
        "dispatch_expires_at": None,
        "dispatch_timeout_seconds": myuber.DISPATCH_REQUEST_TIMEOUT_SECONDS,
        "rider_location": pickup,
        "payment": myuber.create_mock_payment(ride_id, rider["id"], estimate["fare"], created_at, estimate["fare_breakdown"]),
        "fraud_assessment": {
            "risk_score": 0,
            "risk_level": "low",
            "signals": [],
            "review_required": False,
            "blocked": False,
        },
        "declined_driver_ids": [],
        "queued_at": created_at,
        "queue_position": None,
        "queue_size": None,
        "status_history": [{"from": None, "status": "matching", "actor": "seed", "at": created_at}],
        "scheduled_at": None,
        "created_at": created_at,
        "updated_at": created_at,
    }


def ensure_completed_ride(myuber, rider: dict, driver: dict) -> dict:
    ride_id = "seed_ride_completed_airport"
    ride = myuber.rides.get(ride_id)
    if ride:
        return ride

    ride = base_ride(
        myuber,
        ride_id,
        rider=rider,
        pickup=POINTS["paddington"],
        destination=POINTS["heathrow"],
        vehicle_type="standard",
        promo_code="SAVE10",
    )
    ride.update(
        {
            "driver_id": driver["id"],
            "driver_distance_km": 1.4,
            "driver_location": POINTS["heathrow"],
            "status": "completed",
            "matched_at": seed_time(hours=-2, minutes=-45),
            "accepted_at": seed_time(hours=-2, minutes=-42),
            "arrived_at": seed_time(hours=-2, minutes=-30),
            "started_at": seed_time(hours=-2, minutes=-24),
            "completed_at": seed_time(hours=-1, minutes=-45),
            "updated_at": seed_time(hours=-1, minutes=-45),
            "status_history": [
                {"from": None, "status": "matching", "actor": "rider", "at": seed_time(hours=-3)},
                {"from": "matching", "status": "pending_driver", "actor": "system", "at": seed_time(hours=-2, minutes=-45)},
                {"from": "pending_driver", "status": "accepted", "actor": "driver", "at": seed_time(hours=-2, minutes=-42)},
                {"from": "accepted", "status": "arrived", "actor": "driver", "at": seed_time(hours=-2, minutes=-30)},
                {"from": "arrived", "status": "in_progress", "actor": "driver", "at": seed_time(hours=-2, minutes=-24)},
                {"from": "in_progress", "status": "completed", "actor": "driver", "at": seed_time(hours=-1, minutes=-45)},
            ],
            "ratings": {
                "rider": {
                    "id": "seed_rating_rider_to_driver",
                    "ride_id": ride_id,
                    "score": 5,
                    "comment": "Smooth airport run.",
                    "from_user_id": rider["id"],
                    "from_role": "rider",
                    "to_user_id": driver["id"],
                    "to_role": "driver",
                    "created_at": seed_time(hours=-1, minutes=-35),
                    "updated_at": seed_time(hours=-1, minutes=-35),
                }
            },
        }
    )
    myuber.rides[ride_id] = ride
    myuber.capture_mock_payment(ride)
    return ride


def ensure_scheduled_ride(myuber, rider: dict) -> dict:
    ride_id = "seed_ride_scheduled_museum"
    ride = myuber.rides.get(ride_id)
    if ride:
        return ride

    ride = base_ride(
        myuber,
        ride_id,
        rider=rider,
        pickup=POINTS["soho"],
        destination=POINTS["greenwich"],
        vehicle_type="premium",
        promo_code="MYUBER5",
    )
    scheduled_for = seed_time(days=1, hours=1)
    ride.update(
        {
            "status": "scheduled",
            "scheduled_for": scheduled_for,
            "scheduled_at": ride["created_at"],
            "queued_at": None,
            "status_history": [{"from": None, "status": "scheduled", "actor": "rider", "at": ride["created_at"]}],
        }
    )
    myuber.rides[ride_id] = ride
    return ride


def ensure_notification(myuber, notification_id: str, *, user: dict, title: str, body: str, kind: str, ride_id: str | None = None) -> dict:
    notification = myuber.notifications.get(notification_id) or {}
    notification.update(
        {
            "id": notification_id,
            "user_id": user["id"],
            "role": user["role"],
            "kind": kind,
            "title": title,
            "body": body,
            "ride_id": ride_id,
            "read_at": notification.get("read_at"),
            "created_at": notification.get("created_at") or seed_time(minutes=-20),
        }
    )
    myuber.notifications[notification_id] = notification
    return notification


def seed_demo_data(myuber, *, reset: bool = False) -> dict:
    if reset:
        reset_runtime_state(myuber)

    operations_admin = ensure_admin(
        myuber,
        "seed_admin_operations",
        email="ops@myuber.local",
        name="Olivia Operations",
        admin_role="operations",
    )
    rider = ensure_rider(
        myuber,
        "seed_rider_lina",
        email="rider@myuber.local",
        name="Lina Rider",
        phone="+44 7700 900101",
        balance=180.00,
    )
    driver = ensure_driver(
        myuber,
        "seed_driver_ava",
        email="driver@myuber.local",
        name="Ava Driver",
        phone="+44 7700 900202",
        vehicle={
            "make": "Toyota",
            "model": "Prius",
            "year": 2022,
            "color": "Black",
            "plate": "MYU-202",
            "type": "standard",
            "license_number": "LIC-SEED-202",
        },
        location=POINTS["kings_cross"],
    )
    pending_driver = ensure_driver(
        myuber,
        "seed_driver_noah",
        email="driver.pending@myuber.local",
        name="Noah Pending",
        phone="+44 7700 900303",
        vehicle={
            "make": "Mercedes-Benz",
            "model": "Vito",
            "year": 2023,
            "color": "Silver",
            "plate": "MYU-303",
            "type": "xl",
            "license_number": "LIC-SEED-303",
        },
        location=POINTS["canary_wharf"],
        document_status="pending_review",
        availability="offline",
    )
    completed_ride = ensure_completed_ride(myuber, rider, driver)
    scheduled_ride = ensure_scheduled_ride(myuber, rider)

    ensure_notification(
        myuber,
        "seed_notification_receipt_ready",
        user=rider,
        title="Receipt ready",
        body="Your airport trip receipt is ready to view.",
        kind="receipt_ready",
        ride_id=completed_ride["id"],
    )
    ensure_notification(
        myuber,
        "seed_notification_driver_verified",
        user=driver,
        title="Documents verified",
        body="You can go online and receive ride requests.",
        kind="driver_documents_verified",
    )
    ensure_notification(
        myuber,
        "seed_notification_ops_ready",
        user=operations_admin,
        title="Demo data seeded",
        body="Rider, driver, scheduled ride, and completed payment records are ready.",
        kind="seed_complete",
    )

    myuber.refresh_ride_queue()
    return {
        "riders": len(myuber.riders),
        "drivers": len(myuber.drivers),
        "admins": len(myuber.admins),
        "rides": len(myuber.rides),
        "notifications": len(myuber.notifications),
        "demo_password": DEMO_PASSWORD,
        "accounts": {
            "rider": rider["email"],
            "driver": driver["email"],
            "pending_driver": pending_driver["email"],
            "operations_admin": operations_admin["email"],
        },
    }
