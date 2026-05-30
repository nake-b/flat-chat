import requests
import os
import geopandas as gpd
import xml.etree.ElementTree as ET

# First name to access, then names of layers(/tables)
datasets_dict = {"ua_einwohnerdichte_2025" : ["ua_einwohnerdichte_2025"], "ua_stratlaerm_2022" : None, "mss_2025" : None, 
            "ua_gruenvolumen_2020" : ["a_gruenvol2020"], "gruenanlagen" : None, "baumbestand" : None, "baumbestand_gruen_berlin" : None, "gewaesserkarte" : None,
            "schulen" : ["schulen_esb", "schulen"], "krankenhaeuser" : None}

class BerlinWFS:
    def __init__(self, dataset_name):
        self.base_url = "https://gdi.berlin.de/services/wfs/"
        self.capabilities_parm = {"service" : "WFS",
                                  "version" : "2.0.0",
                                  "request" : "GetCapabilities"}
        self.dataset_name = dataset_name
        
    def _ensure_doc_folder(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        doc_path = os.path.join(script_dir, "documentation")

        if not os.path.exists(doc_path):
            os.makedirs(doc_path)

        print(f"📁 Dokumentationsordner erstellt/gefunden unter: {doc_path}")

        return doc_path

    def _write_docu(self, dataset_name, layer_name, variables, labels):
        doc_path = self._ensure_doc_folder()
        filepath = os.path.join(doc_path, f"{dataset_name}.txt")

        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"\n=== Layer: {layer_name} ===\n")
            for v, l in zip(variables, labels):
                f.write(f"{v} --- {l}\n")
        
        return


    def connect(self):
        capabilities_response = requests.get(f"{self.base_url}{self.dataset_name}", params=self.capabilities_parm)
        capabilities_response.encoding = ("utf-8")
        # Check if request was successful
        if capabilities_response.status_code == 200:
            print("Successfuly requested Capabilities\n")
            print("===" * 10)
            print("GetCapabilities")
            print("===" * 10 , "\n")

            print(capabilities_response.text[:500])

            return capabilities_response

        else:
            print(f"Could not request capabilities -- status code: {capabilities_response.status_code}")

    def inspect(self):

        capabilities_response = self.connect()

        # --- 1) Capabilities parsen ---
        root = ET.fromstring(capabilities_response.text)

        ns_cap = {
            "wfs": "http://www.opengis.net/wfs/2.0",
            "ows": "http://www.opengis.net/ows/1.1"
        }

        layers = []
        layers_abstracts = []

        # FeatureTypes = Layer
        for ft in root.findall(".//wfs:FeatureType", ns_cap):

            name_tag = ft.find("wfs:Name", ns_cap)
            abs_tag = ft.find("wfs:Abstract", ns_cap)

            layer_name = name_tag.text if name_tag is not None else None
            layer_abs = abs_tag.text if abs_tag is not None else None

            layers.append(layer_name)
            layers_abstracts.append(layer_abs)
        # Ausgabe der Layer
        for name, abstract in zip(layers, layers_abstracts):
            print(f"Layer: {name}\nAbstract: {abstract}\n")

        print("\n" + "="*80 + "\n")
        print("STARTE DescribeFeatureType‑Analyse\n")
        print("="*80 + "\n")

        # --- 2) DescribeFeatureType für jeden Layer laden ---
        ns_xsd = {"xsd": "http://www.w3.org/2001/XMLSchema"}

        for layer_name in layers:
            print(f"\n=== Layer: {layer_name} ===")

            params = {
                "service": "WFS",
                "version": "2.0.0",
                "request": "DescribeFeatureType",
                "typeNames": layer_name
            }

            response = requests.get(f"{self.base_url}{self.dataset_name}", params=params)
            response.encoding=("utf-8")
            print("##############################################", response.url)
            if response.status_code != 200:
                print(f"⚠️ DescribeFeatureType fehlgeschlagen für {layer_name}")
                continue

            # XML parsen
            try:
                schema_root = ET.fromstring(response.text)
            except:
                print("⚠️ XML konnte nicht geparst werden")
                continue

            # xsd:sequence finden
            sequence = schema_root.find(".//xsd:sequence", ns_xsd)
            if sequence is None:
                print("⚠️ Keine xsd:sequence gefunden")
                continue

            variables = []
            labels = []

            # Alle xsd:element durchgehen
            for element in sequence.findall("xsd:element", ns_xsd):

                # Variablenname
                var_name = element.attrib.get("name", "UNBEKANNT")
                variables.append(var_name)

                # Dokumentation
                doc = element.find(".//xsd:documentation", ns_xsd)
                if doc is not None and doc.text:
                    labels.append(doc.text.strip())
                else:
                    labels.append("Keine Beschreibung verfügbar")

            # Ausgabe
            print(f"Variablen ({len(variables)}):")
            for v, l in zip(variables, labels):
                print(f"  {v} --- {l}")

            # Optional: Dokumentation schreiben
            # write_docu(vnames=variables, vlabels=labels, layer_name=layer_name.replace(":", "_"))

            # Datei schreiben
            print(f"#############TEST################ Layer_Name = {layer_name}")
            self._write_docu(dataset_name = self.dataset_name, layer_name = layer_name, variables = variables, labels = labels)

        print(f"\nAll layers found: {layers}\n")

        return layers

    def extract(self, layer_name):
        """
        Lädt einen Layer als GeoDataFrame über WFS GetFeature.
        """

        # Dataset extrahieren

        # Richtige URL
        url = f"{self.base_url}{self.dataset_name}"

        # Parameter für GetFeature
        download_parm = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": layer_name,
            "outputFormat": "application/json"
        }

        print(f"💿 Data set:\n  {self.dataset_name}")        
        print(f"⬇️ Load Layer:\n  {layer_name}")
        print(f"🌐 Base URL:\n  {url}")

        # Request
        response = requests.get(url, params=download_parm)
        response.encoding = "utf-8"
        print(f"🌐 Full URL:\n  {response.url}")

        print(f"HTTP Status: {response.status_code}")

        if response.status_code != 200:
            raise RuntimeError("GetFeature failed")

        # GeoJSON → GeoDataFrame
        data = response.json()

        gdf = gpd.GeoDataFrame.from_features(
            data["features"],
            crs="EPSG:25833"  # Berlin WFS Standard
        )

        print(f"📦 Observations: {len(gdf)}")

        return gdf


# #wfs_test = BerlinWFS(datasets_dict=datasets_dict)
# test_dict = {"schulen" : ["schulen"]}
# for key, item in test_dict.items():
#     print(key, type(key), item, type(item))
#     wtf_test2 = BerlinWFS(dataset_name = key)
#     #gdf = wtf_test2.run_quick(layer_names = item)
    
#     gdf = wtf_test2.run_extended()
#     #gdf = wfs_test.run()
