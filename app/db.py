"""Data model. SQLite by default (zero setup for the hackathon); set DATABASE_URL=postgresql+psycopg://... for Postgres.

Raw satellite data is never stored — only per-field derived features (JSON) plus everything the farmer tells us.
"""
import datetime as dt
import secrets

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from app.config import DATABASE_URL


def now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Farmer(Base):
    __tablename__ = "farmer"
    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # e.g. "whatsapp:+1555..." or "+1555..."
    channel: Mapped[str] = mapped_column(String(16), default="whatsapp")  # whatsapp | sms | cli
    name: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(8), default="en")
    state: Mapped[str] = mapped_column(String(32), default="new")  # new|awaiting_location|awaiting_crops|awaiting_priority|ready
    priorities: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["save_water"]
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"livestock": true, "equipment": "no-till drill"}
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    last_inbound_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    fields: Mapped[list["Field"]] = relationship(back_populates="farmer", cascade="all, delete-orphan")
    messages: Mapped[list["Message"]] = relationship(back_populates="farmer", cascade="all, delete-orphan")


class Field(Base):
    __tablename__ = "field"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(24), unique=True, index=True,
                                           default=lambda: secrets.token_urlsafe(9))  # unguessable report links
    farmer_id: Mapped[int] = mapped_column(ForeignKey("farmer.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="Main field")
    place: Mapped[str | None] = mapped_column(String(160))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    irrigated: Mapped[bool | None] = mapped_column(Boolean)
    current_rotation: Mapped[list] = mapped_column(JSON, default=list)  # crop ids, confirmed or corrected by farmer
    features: Mapped[dict | None] = mapped_column(JSON)  # derived NASA/USDA features (app.engine.features)
    features_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    farmer: Mapped[Farmer] = relationship(back_populates="fields")
    history: Mapped[list["CropHistory"]] = relationship(cascade="all, delete-orphan", order_by="CropHistory.year")
    observations: Mapped[list["Observation"]] = relationship(cascade="all, delete-orphan")
    recommendations: Mapped[list["Recommendation"]] = relationship(cascade="all, delete-orphan",
                                                                   order_by="Recommendation.id")


class CropHistory(Base):
    __tablename__ = "crop_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("field.id"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    crop: Mapped[str | None] = mapped_column(String(40))  # crop id from seeds/crops.json
    category: Mapped[str | None] = mapped_column(String(80))  # raw CDL class
    source: Mapped[str] = mapped_column(String(16), default="cdl")  # cdl | farmer


class Observation(Base):
    """Ground truth the farmer sends in chat (rain gauge, pests, planting, yields...)."""
    __tablename__ = "observation"
    id: Mapped[int] = mapped_column(primary_key=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("field.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # rain | irrigated | pest | planted | harvested | yield | note
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(16))
    text: Mapped[str | None] = mapped_column(Text)
    observed_on: Mapped[dt.date | None] = mapped_column(DateTime)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Recommendation(Base):
    __tablename__ = "recommendation"
    id: Mapped[int] = mapped_column(primary_key=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("field.id"), index=True)
    priorities: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict] = mapped_column(JSON)  # strategies with scores/why/evidence/benefits/gaps/risks
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Message(Base):
    __tablename__ = "message"
    id: Mapped[int] = mapped_column(primary_key=True)
    farmer_id: Mapped[int] = mapped_column(ForeignKey("farmer.id"), index=True)
    direction: Mapped[str] = mapped_column(String(4))  # in | out
    body: Mapped[str] = mapped_column(Text, default="")
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    media_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    farmer: Mapped[Farmer] = relationship(back_populates="messages")


class Alert(Base):
    __tablename__ = "alert"
    id: Mapped[int] = mapped_column(primary_key=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("field.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # frost | heat | dry_spell | irrigate
    for_date: Mapped[str] = mapped_column(String(10))
    body: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # Small pool (Supabase free tier caps connections); prepare_threshold=None keeps it compatible with
    # Supabase's transaction pooler (port 6543) as well as the session pooler (5432).
    engine = create_engine(DATABASE_URL, pool_size=3, max_overflow=2, pool_pre_ping=True, pool_recycle=300,
                           connect_args={"prepare_threshold": None})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db():
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        # Supabase exposes every public table through its REST API to anyone with the anon key. Enabling RLS with no
        # policies blocks that API; this app connects as the table owner, which bypasses RLS.
        with engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY')


def session() -> Session:
    return SessionLocal()
