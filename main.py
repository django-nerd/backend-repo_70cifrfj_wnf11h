import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import Car as CarSchema, Rental as RentalSchema, Invoice as InvoiceSchema


# Utilities to serialize MongoDB documents
class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return v
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return ObjectId(v)


def serialize_value(v):
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    return v


def serialize_doc(doc: dict):
    return {k: serialize_value(v) for k, v in doc.items()}


app = FastAPI(title="Car Rental API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "Car Rental Backend is running"}


# Cars Endpoints
@app.get("/api/cars")
def list_cars():
    cars = get_documents("car")
    return [serialize_doc(c) for c in cars]


class CreateCarRequest(BaseModel):
    make: str
    model: str
    year: int
    plate_number: str
    daily_rate: float


@app.post("/api/cars")
def add_car(payload: CreateCarRequest):
    # Ensure unique plate number
    existing = db["car"].find_one({"plate_number": payload.plate_number})
    if existing:
        raise HTTPException(status_code=400, detail="Plate number already exists")

    car = CarSchema(
        make=payload.make,
        model=payload.model,
        year=payload.year,
        plate_number=payload.plate_number,
        daily_rate=payload.daily_rate,
        available=True,
    )
    car_id = create_document("car", car)
    created = db["car"].find_one({"_id": ObjectId(car_id)})
    return serialize_doc(created)


# Rentals Endpoints
class StartRentalRequest(BaseModel):
    car_id: str
    customer_name: str


@app.post("/api/rentals/start")
def start_rental(payload: StartRentalRequest):
    # Validate car
    if not ObjectId.is_valid(payload.car_id):
        raise HTTPException(status_code=400, detail="Invalid car_id")

    car = db["car"].find_one({"_id": ObjectId(payload.car_id)})
    if not car:
        raise HTTPException(status_code=404, detail="Car not found")
    if not car.get("available", True):
        raise HTTPException(status_code=400, detail="Car is not available")

    # Create rental
    rental = RentalSchema(
        car_id=str(car["_id"]),
        customer_name=payload.customer_name,
    )
    rental_id = create_document("rental", rental)

    # Mark car unavailable
    db["car"].update_one({"_id": car["_id"]}, {"$set": {"available": False, "updated_at": datetime.now(timezone.utc)}})

    created = db["rental"].find_one({"_id": ObjectId(rental_id)})
    return serialize_doc(created)


@app.get("/api/rentals/active")
def list_active_rentals():
    rentals = list(db["rental"].find({"status": "active"}))
    return [serialize_doc(r) for r in rentals]


# Return + Invoice
class ReturnRentalRequest(BaseModel):
    tax_rate: Optional[float] = 0.0


def compute_invoice(car: dict, rental: dict, tax_rate: float = 0.0) -> dict:
    start: datetime = rental.get("start_date")
    end: datetime = rental.get("end_date") or datetime.now(timezone.utc)

    # Calculate rental days (ceil to next day if any partial day)
    duration = end - start
    days = max(1, (duration.days + (1 if duration.seconds > 0 else 0)))
    daily_rate = float(car.get("daily_rate", 0.0))
    subtotal = round(days * daily_rate, 2)
    tax_amount = round(subtotal * (tax_rate or 0.0), 2)
    total = round(subtotal + tax_amount, 2)

    invoice_data = InvoiceSchema(
        rental_id=str(rental["_id"]),
        car_id=str(car["_id"]),
        customer_name=rental["customer_name"],
        start_date=start,
        end_date=end,
        days=days,
        daily_rate=daily_rate,
        subtotal=subtotal,
        tax_rate=tax_rate or 0.0,
        tax_amount=tax_amount,
        total=total,
        items=None,
    ).model_dump()
    return invoice_data


@app.post("/api/rentals/{rental_id}/return")
def return_rental(rental_id: str, payload: ReturnRentalRequest):
    if not ObjectId.is_valid(rental_id):
        raise HTTPException(status_code=400, detail="Invalid rental_id")

    rental = db["rental"].find_one({"_id": ObjectId(rental_id)})
    if not rental:
        raise HTTPException(status_code=404, detail="Rental not found")
    if rental.get("status") == "returned":
        raise HTTPException(status_code=400, detail="Rental already returned")

    car = db["car"].find_one({"_id": ObjectId(rental["car_id"])})
    if not car:
        raise HTTPException(status_code=404, detail="Car for rental not found")

    # Set end_date and mark returned
    end_time = datetime.now(timezone.utc)
    db["rental"].update_one(
        {"_id": rental["_id"]},
        {"$set": {"end_date": end_time, "status": "returned", "updated_at": datetime.now(timezone.utc)}},
    )

    # Mark car available again
    db["car"].update_one({"_id": car["_id"]}, {"$set": {"available": True, "updated_at": datetime.now(timezone.utc)}})

    # Re-fetch updated rental
    rental = db["rental"].find_one({"_id": rental["_id"]})

    # Generate and store invoice
    invoice_data = compute_invoice(car, rental, payload.tax_rate or 0.0)
    invoice_id = create_document("invoice", invoice_data)
    invoice = db["invoice"].find_one({"_id": ObjectId(invoice_id)})

    return {"rental": serialize_doc(rental), "invoice": serialize_doc(invoice)}


# Invoices Endpoints
@app.get("/api/invoices")
def list_invoices():
    invoices = get_documents("invoice")
    return [serialize_doc(i) for i in invoices]


@app.get("/api/invoices/{invoice_id}")
def get_invoice(invoice_id: str):
    if not ObjectId.is_valid(invoice_id):
        raise HTTPException(status_code=400, detail="Invalid invoice_id")
    inv = db["invoice"].find_one({"_id": ObjectId(invoice_id)})
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return serialize_doc(inv)


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, "name") else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    # Check environment variables
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
