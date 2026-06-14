"""SQLAlchemy ORM models for the 14 geo-context silver tables.

Mirrors `services/backend/alembic/versions/0003_base_contextual_tables.py`
+ `0004_geo_context_hardening.py`. These models are used only by
`GeoContextService` — they're not exposed through the FastAPI API surface.

No behaviour, no business logic — just typed column declarations. Schema
changes happen in alembic migrations first, then are reflected here.
"""

import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    CheckConstraint,
    Date,
    Float,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from flat_chat.core.database import Base


class School(Base):
    __tablename__ = "schools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_number: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    school_type: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str | None] = mapped_column(Text)
    school_category: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    neighborhood: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(Text)
    street: Mapped[str | None] = mapped_column(Text)
    house_number: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(Text)
    school_year: Mapped[str | None] = mapped_column(Text)
    geom: Mapped[object] = mapped_column(Geometry("POINT", srid=4326))


class SchoolCatchment(Base):
    __tablename__ = "school_catchments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    catchment_id: Mapped[str | None] = mapped_column(Text)
    school_number: Mapped[str | None] = mapped_column(Text)
    school_name: Mapped[str | None] = mapped_column(Text)
    geom: Mapped[object] = mapped_column(Geometry("MULTIPOLYGON", srid=4326))


class PopulationDensity2025(Base):
    __tablename__ = "population_density_2025"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lor_key: Mapped[str | None] = mapped_column(Text)
    population: Mapped[int | None] = mapped_column(Integer)
    area_total: Mapped[float | None] = mapped_column(Float)
    area_hectares: Mapped[float | None] = mapped_column(Float)
    population_per_hectare: Mapped[float | None] = mapped_column(Float)
    age_under_6: Mapped[int | None] = mapped_column(Integer)
    age_6_to_10: Mapped[int | None] = mapped_column(Integer)
    age_10_to_18: Mapped[int | None] = mapped_column(Integer)
    age_18_to_65: Mapped[int | None] = mapped_column(Integer)
    age_65_to_70: Mapped[int | None] = mapped_column(Integer)
    age_70_to_75: Mapped[int | None] = mapped_column(Integer)
    age_75_to_80: Mapped[int | None] = mapped_column(Integer)
    age_80_plus: Mapped[int | None] = mapped_column(Integer)
    area_type: Mapped[str | None] = mapped_column(Text)
    area_type_en: Mapped[str | None] = mapped_column(Text)
    geom: Mapped[object] = mapped_column(Geometry("MULTIPOLYGON", srid=4326))


class StreetNoise2022(Base):
    __tablename__ = "street_noise_2022"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    import_id: Mapped[str | None] = mapped_column(Text)
    noise_street_lden: Mapped[float | None] = mapped_column(Float)
    noise_street_lnight: Mapped[float | None] = mapped_column(Float)
    noise_rail_lden: Mapped[float | None] = mapped_column(Float)
    noise_rail_lnight: Mapped[float | None] = mapped_column(Float)
    noise_air_lden_class: Mapped[str | None] = mapped_column(Text)
    noise_air_lnight_class: Mapped[str | None] = mapped_column(Text)
    noise_total_lden: Mapped[float | None] = mapped_column(Float)
    noise_total_lnight: Mapped[float | None] = mapped_column(Float)
    geom: Mapped[object] = mapped_column(Geometry("POINT", srid=4326))


class GreenVolume2020(Base):
    __tablename__ = "green_volume_2020"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lor_key: Mapped[str | None] = mapped_column(Text)
    area_key_5: Mapped[str | None] = mapped_column(Text)
    area_total: Mapped[float | None] = mapped_column(Float)
    area_use_code: Mapped[str | None] = mapped_column(Text)
    area_use_name: Mapped[str | None] = mapped_column(Text)
    block_type_code: Mapped[str | None] = mapped_column(Text)
    block_type_name: Mapped[str | None] = mapped_column(Text)
    area_class_code: Mapped[str | None] = mapped_column(Text)
    area_class_name: Mapped[str | None] = mapped_column(Text)
    veg_height_2020: Mapped[float | None] = mapped_column(Float)
    veg_percent_2020: Mapped[float | None] = mapped_column(Float)
    veg_vol_per_area_2010: Mapped[float | None] = mapped_column(Float)
    veg_vol_per_area_2020: Mapped[float | None] = mapped_column(Float)
    veg_vol_2010: Mapped[float | None] = mapped_column(Float)
    veg_vol_2020: Mapped[float | None] = mapped_column(Float)
    built_area_2020: Mapped[float | None] = mapped_column(Float)
    veg_height_excl_built_2020: Mapped[float | None] = mapped_column(Float)
    veg_percent_excl_built_2020: Mapped[float | None] = mapped_column(Float)
    veg_vol_per_area_excl_built_2020: Mapped[float | None] = mapped_column(Float)
    veg_vol_excl_built_2020: Mapped[float | None] = mapped_column(Float)
    veg_vol_change: Mapped[float | None] = mapped_column(Float)
    area_use_name_en: Mapped[str | None] = mapped_column(Text)
    block_type_name_en: Mapped[str | None] = mapped_column(Text)
    area_class_name_en: Mapped[str | None] = mapped_column(Text)
    geom: Mapped[object] = mapped_column(Geometry("MULTIPOLYGON", srid=4326))


class Park(Base):
    __tablename__ = "parks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pit_id: Mapped[str | None] = mapped_column(Text)
    marker: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    neighborhood: Mapped[str | None] = mapped_column(Text)
    object_type: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    name_addition: Mapped[str | None] = mapped_column(Text)
    year_built: Mapped[str | None] = mapped_column(Text)
    year_renovated: Mapped[str | None] = mapped_column(Text)
    cadastral_area_m2: Mapped[float | None] = mapped_column(Float)
    dedication: Mapped[str | None] = mapped_column(Text)
    plan_number: Mapped[str | None] = mapped_column(Text)
    plan_name: Mapped[str | None] = mapped_column(Text)
    geom: Mapped[object] = mapped_column(Geometry("MULTIPOLYGON", srid=4326))


class Playground(Base):
    __tablename__ = "playgrounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pit_id: Mapped[str | None] = mapped_column(Text)
    marker: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    neighborhood: Mapped[str | None] = mapped_column(Text)
    object_type: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    name_addition: Mapped[str | None] = mapped_column(Text)
    year_built: Mapped[str | None] = mapped_column(Text)
    year_renovated: Mapped[str | None] = mapped_column(Text)
    cadastral_area_m2: Mapped[float | None] = mapped_column(Float)
    dedication: Mapped[str | None] = mapped_column(Text)
    plan_number: Mapped[str | None] = mapped_column(Text)
    plan_name: Mapped[str | None] = mapped_column(Text)
    play_area_m2: Mapped[float | None] = mapped_column(Float)
    geom: Mapped[object] = mapped_column(Geometry("MULTIPOLYGON", srid=4326))


class Hospital(Base):
    __tablename__ = "hospitals"
    __table_args__ = (
        CheckConstraint(
            "tier IN ('plan_hospital', 'other')",
            name="hospitals_tier_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tier: Mapped[str] = mapped_column(Text, nullable=False)
    gis_id: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    street: Mapped[str | None] = mapped_column(Text)
    house_number: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(Text)
    neighborhood: Mapped[str | None] = mapped_column(Text)
    total_beds: Mapped[int | None] = mapped_column(Integer)
    location_number: Mapped[str | None] = mapped_column(Text)
    location_name: Mapped[str | None] = mapped_column(Text)
    hospital_number: Mapped[str | None] = mapped_column(Text)
    departments: Mapped[str | None] = mapped_column(Text)
    geom: Mapped[object] = mapped_column(Geometry("POINT", srid=4326))


class DisabledParking(Base):
    __tablename__ = "disabled_parking"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    spot_count: Mapped[int | None] = mapped_column(Integer)
    police_jurisdiction: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(Text)
    neighborhood: Mapped[str | None] = mapped_column(Text)
    recorded_date: Mapped[datetime.date | None] = mapped_column(Date)
    geom: Mapped[object] = mapped_column(Geometry("POINT", srid=4326))


class SocialMonitoring2025(Base):
    __tablename__ = "social_monitoring_2025"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    planning_area_id: Mapped[str | None] = mapped_column(Text)
    planning_area_name: Mapped[str | None] = mapped_column(Text)
    district_id: Mapped[str | None] = mapped_column(Text)
    residents: Mapped[int | None] = mapped_column(Integer)
    dynamics_index_score: Mapped[int | None] = mapped_column(Integer)
    dynamics_index_label: Mapped[str | None] = mapped_column(Text)
    social_inequality_category: Mapped[str | None] = mapped_column(Text)
    social_inequality_score: Mapped[int | None] = mapped_column(Integer)
    social_inequality_label: Mapped[str | None] = mapped_column(Text)
    status_index_score: Mapped[int | None] = mapped_column(Integer)
    status_index_label: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    geom: Mapped[object] = mapped_column(Geometry("MULTIPOLYGON", srid=4326))


class WaterBody(Base):
    __tablename__ = "water_bodies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    water_number_old: Mapped[str | None] = mapped_column(Text)
    water_type: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    water_number_new: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    neighborhood: Mapped[str | None] = mapped_column(Text)
    receiving_water: Mapped[str | None] = mapped_column(Text)
    # Source surface_area_m2 / length_m come through as TEXT (free-form, sometimes
    # non-numeric) — we preserve as text per the migration.
    surface_area_m2: Mapped[str | None] = mapped_column(Text)
    length_m: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(Text)
    maintenance: Mapped[str | None] = mapped_column(Text)
    water_kind: Mapped[str | None] = mapped_column(Text)
    water_class: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    # Generic Geometry — accepts Polygon / MultiPolygon / GeometryCollection.
    geom: Mapped[object] = mapped_column(Geometry("GEOMETRY", srid=4326))


class TransitStop(Base):
    __tablename__ = "transit_stops"

    stop_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    geom: Mapped[object] = mapped_column(
        Geometry("POINT", srid=4326), nullable=False
    )
    modes_served: Mapped[list[int]] = mapped_column(
        ARRAY(SmallInteger), nullable=False
    )
    lines_served: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)


class TransitRoute(Base):
    __tablename__ = "transit_routes"

    route_id: Mapped[str] = mapped_column(Text, primary_key=True)
    short_name: Mapped[str | None] = mapped_column(Text)
    long_name: Mapped[str | None] = mapped_column(Text)
    route_type: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    color: Mapped[str | None] = mapped_column(Text)
    text_color: Mapped[str | None] = mapped_column(Text)


class TransitRouteShape(Base):
    __tablename__ = "transit_route_shapes"

    # Composite PK — declared on the columns directly via primary_key=True.
    route_id: Mapped[str] = mapped_column(
        String, primary_key=True
    )
    direction_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    geom: Mapped[object] = mapped_column(
        Geometry("LINESTRING", srid=4326), nullable=False
    )
