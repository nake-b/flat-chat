import requests
import zipfile
import io
import pandas as pd

URL = "https://www.vbb.de/vbbgtfs"


# =========================
# DOWNLOAD IN MEMORY
# =========================

def download_zip_in_memory(url):
    print("[START]")
    print(f"[INFO] Downloading GTFS ZIP: {url}")

    response = requests.get(url, stream=True, allow_redirects=True)
    response.raise_for_status()

    print(f"[INFO] Final URL: {response.url}")
    print(f"[INFO] Download size: {len(response.content) / (1024*1024):.2f} MB")

    return io.BytesIO(response.content)


# =========================
# LOAD ALL GTFS TABLES
# =========================

def load_all_gtfs_tables(zip_bytes):
    dataframes = {}

    with zipfile.ZipFile(zip_bytes) as z:
        file_list = [f for f in z.namelist() if f.endswith(".txt")]

        print(f"[INFO] Found {len(file_list)} GTFS tables")

        for file_name in file_list:
            table_name = f"GTFS_{file_name.replace(".txt", "")}"

            with z.open(file_name) as f:
                df = pd.read_csv(f)

            dataframes[table_name] = df

            print(f"[INFO] Loaded {table_name}: {df.shape}")

    return dataframes


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    zip_bytes = download_zip_in_memory(URL)

    gtfs_data = load_all_gtfs_tables(zip_bytes)

    # =========================
    # OUTPUT SUMMARY
    # =========================

    print("\n[INFO] All loaded DataFrames:")
    for name, df in gtfs_data.items():
        print(f"- {name}: {df.shape}")

    print("\n[INFO] Available tables:", list(gtfs_data.keys()))

    # Beispielzugriff
    print("\n[INFO] Preview stops:")
    print(gtfs_data["GTFS_stops"].head())
    print("[DONE]")
