import os
import io
import pandas as pd
import re
from typing import Optional
from fastapi import FastAPI, Depends, Query, HTTPException, UploadFile, File, Header, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Index
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ==========================================
# 1. DATABASE CONFIGURATION
# ==========================================
DATABASE_URL = "sqlite:///./vehicles.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "SuperSecretAdminKey123")

class Vehicle(Base):
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True, index=True)
    Make = Column(String, index=True, nullable=False)
    Model = Column(String, index=True, nullable=False)
    Variant = Column(String, index=True, nullable=False)
    BodyType = Column(String, index=True, nullable=False)
    Trim = Column(String, index=True, nullable=False)
    ModelYear = Column(String, index=True, nullable=False)

    __table_args__ = (
        Index('idx_cascading_lookup', 'Make', 'Model', 'Variant', 'BodyType', 'Trim', 'ModelYear'),
    )

Base.metadata.create_all(bind=engine)

# ==========================================
# 2. FASTAPI & DATA CLEANING
# ==========================================
app = FastAPI(title="Vehicle Parts API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def clean_invisible_chars(text):
    """Removes zero-width spaces, BOMs, non-breaking spaces, and strips whitespace."""
    if pd.isna(text):
        return ""
    cleaned = re.sub(r'[\u200B-\u200D\uFEFF\xa0]', '', str(text))
    return cleaned.strip()

def populate_db_from_excel(excel_bytes: bytes, db: Session):
    """Reads ALL sheets, aggressively cleans unicode characters, and updates SQLite."""
    all_sheets = pd.read_excel(io.BytesIO(excel_bytes), sheet_name=None)
    
    all_dfs = []
    required_columns = ['Model', 'Variant', 'BodyType', 'Trim', 'ModelYear']
    
    for sheet_name, df in all_sheets.items():
        if df.empty:
            continue
            
        clean_sheet_name = clean_invisible_chars(sheet_name)
        
        column_mapping = {}
        for dirty_col in df.columns:
            clean_dirty_col = str(dirty_col).lower().replace(" ", "")
            for req in required_columns + ['Make']:
                if req.lower() == clean_dirty_col:
                    column_mapping[dirty_col] = req
                    
        df = df.rename(columns=column_mapping)
        
        if 'Make' not in df.columns:
            df['Make'] = clean_sheet_name
            
        if not set(required_columns).issubset(set(df.columns)):
            print(f"Skipping sheet '{sheet_name}': Missing required columns.")
            continue
            
        for col in required_columns + ['Make']:
            df[col] = df[col].apply(clean_invisible_chars)
            
        all_dfs.append(df[required_columns + ['Make']])
        
    if not all_dfs:
        raise ValueError("Could not find any valid data matching your required columns across all sheets.")
        
    master_df = pd.concat(all_dfs, ignore_index=True)

    db.query(Vehicle).delete()
    db.commit()

    records = master_df.to_dict(orient="records")
    db.bulk_insert_mappings(Vehicle, records)
    db.commit()

@app.on_event("startup")
def startup_event():
    if os.path.exists("vehicles.xlsx"):
        db = SessionLocal()
        if db.query(Vehicle).count() == 0:
            with open("vehicles.xlsx", "rb") as f:
                populate_db_from_excel(f.read(), db)
            print("✅ Database successfully cleaned and loaded from vehicles.xlsx")
        db.close()

# ==========================================
# 3. API ENDPOINTS
# ==========================================
@app.get("/api/v1/dropdown/{target_column}")
def get_dropdown_options(
    target_column: str,
    make: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    variant: Optional[str] = Query(None),
    body_type: Optional[str] = Query(None),
    trim: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    column_map = {
        "Make": Vehicle.Make,
        "Model": Vehicle.Model,
        "Variant": Vehicle.Variant,
        "BodyType": Vehicle.BodyType,
        "Trim": Vehicle.Trim,
        "ModelYear": Vehicle.ModelYear
    }

    if target_column not in column_map:
        raise HTTPException(status_code=400, detail="Invalid target column.")

    target = column_map[target_column]
    query = db.query(target)

    if make:
        query = query.filter(Vehicle.Make == make)
    if model:
        query = query.filter(Vehicle.Model == model)
    if variant:
        query = query.filter(Vehicle.Variant == variant)
    if body_type:
        query = query.filter(Vehicle.BodyType == body_type)
    if trim:
        query = query.filter(Vehicle.Trim == trim)

    results = query.distinct().order_by(target).all()
    return {"options": [row[0] for row in results if row[0]]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)