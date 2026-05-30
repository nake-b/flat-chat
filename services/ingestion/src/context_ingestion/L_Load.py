import sqlalchemy as sa
import geopandas as gpd
from geoalchemy2 import Geometry

class BerlinIngest:
    def __init__(self, dataset_name : str, layer_name : str, engine_url :str = "postgresql://flat_chat:flat_chat@postgres:5432/flat_chat", srid: int = 25833):
        self.dataset_name = dataset_name
        self.layer_name = layer_name
        self.srid = srid
        self.engine_url = engine_url or os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg2://flat_chat:flat_chat@postgres:5432/flat_chat")

    def _connect_engine(self):
        try:
            engine = sa.create_engine(self.engine_url)
            # optional: test connection
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            print(f"Connected to database {self.engine_url}")
            return engine
        except Exception as e:
            print(f"Could not establish DB connection{self.engine_url}")
            raise
    
    def load(self, gdf: gpd.GeoDataFrame, if_exists: str = "replace", chunksize: int = 5000):
        if gdf is None or gdf.empty:
            print("No GeoDataFrame provided or it's empty")
            return

        # ensure CRS / SRID
        if gdf.crs is None:
            print("Setting CRS to EPSG:%s", self.srid)
            gdf = gdf.set_crs(self.srid, allow_override=True)
        else:
            # optional: convert to target srid
            gdf = gdf.to_crs(epsg=self.srid)

        engine = self._connect_engine()
        table_name = f"{self.dataset_name}_{self.layer_name}"
        try:
            gdf.to_postgis(
                table_name,
                engine,
                if_exists=if_exists,
                dtype={"geometry": Geometry("POINT", srid=self.srid)},
                index=False,
                chunksize=chunksize,
            )
            print(f"Wrote from dataset {self.dataset_name} --- {self.layer_name}: {len(gdf)} rows to {table_name}")
        except Exception as e:
            print(f"{self.layer_name} could not be written to {table_name}, Error: {e}")
        finally:
            engine.dispose()

