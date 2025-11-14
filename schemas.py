"""
Database Schemas

Car Rental App Schemas using Pydantic models.
Each Pydantic model maps to a MongoDB collection using the lowercase class name.
- Car -> "car"
- Rental -> "rental"
- Invoice -> "invoice"
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class Car(BaseModel):
    """
    Cars available for rent
    Collection: "car"
    """
    make: str = Field(..., description="Manufacturer, e.g., Toyota")
    model: str = Field(..., description="Model, e.g., Corolla")
    year: int = Field(..., ge=1900, le=2100, description="Year of manufacture")
    plate_number: str = Field(..., description="Unique registration number")
    daily_rate: float = Field(..., ge=0, description="Daily rental rate")
    available: bool = Field(True, description="Whether the car is available for rent")

class Rental(BaseModel):
    """
    Rental records for each car
    Collection: "rental"
    """
    car_id: str = Field(..., description="ID of the rented car")
    customer_name: str = Field(..., description="Customer full name")
    start_date: datetime = Field(default_factory=datetime.utcnow, description="Rental start time (UTC)")
    end_date: Optional[datetime] = Field(None, description="Rental end time (UTC)")
    status: str = Field("active", description="active | returned")

class InvoiceItem(BaseModel):
    description: str
    quantity: int = 1
    unit_price: float
    amount: float

class Invoice(BaseModel):
    """
    Invoices generated upon return
    Collection: "invoice"
    """
    rental_id: str
    car_id: str
    customer_name: str
    start_date: datetime
    end_date: datetime
    days: int
    daily_rate: float
    subtotal: float
    tax_rate: float = 0.0
    tax_amount: float = 0.0
    total: float
    items: Optional[List[InvoiceItem]] = None
