from context_ingestion.E_BerlinGDI import BerlinWFS
from context_ingestion.L_Load import BerlinIngest

# First name to access, then names of layers(/tables)

datasets_dict = {"ua_einwohnerdichte_2025" : ["ua_einwohnerdichte_2025"], 
                 "ua_stratlaerm_2022" : ["aa_fp_gesamt2022"], 
                 "mss_2025" : None, #tbd
            "ua_gruenvolumen_2020" : ["a_gruenvol2020"], 
            "gruenanlagen" : ["gruenanlagen", "spielplaetze"], 
            "baumbestand" : ["anlagenbaeume", "strassenbaeume"], # both layers mergen
            "baumbestand_gruen_berlin" : ["anlagenbaum_gruenberlin"], # merge with other baumbestand
            "gewaesserkarte" : None, #tbd
            "schulen" : ["schulen_esb", "schulen"], 
            "krankenhaeuser" : ["plankrankenhaeuser", "weitere_krankenhaeuser"], # rename second layer to make them less important
            "behindertenparkplaetze" : ["bpark"]} # index 10

datasets_dict_slim = {"ua_einwohnerdichte_2025" : ["ua_einwohnerdichte_2025"], 
                 "ua_stratlaerm_2022" : ["aa_fp_gesamt2022"], 
            "ua_gruenvolumen_2020" : ["a_gruenvol2020"], 
            "gruenanlagen" : ["gruenanlagen", "spielplaetze"], 
            #"baumbestand" : ["anlagenbaeume", "strassenbaeume"], # both layers mergen
            #"baumbestand_gruen_berlin" : ["anlagenbaum_gruenberlin"], # merge with other baumbestand
            "schulen" : ["schulen_esb", "schulen"], 
            "krankenhaeuser" : ["plankrankenhaeuser"], # rename second layer to make them less important
            "behindertenparkplaetze" : ["bpark"]} # index 10

def main():
    for key, layers in datasets_dict_slim.items():
        print(f"Key: {key} layers: {layers} - {type(layers)}")
        wfs = BerlinWFS(dataset_name=key)
        for layer in layers:
            gdf = wfs.extract(layer_name=layer)
            gdf = gdf.set_crs(25833)

            wfsingest = BerlinIngest(dataset_name = key, layer_name = layer, engine_url = "postgresql+psycopg2://flat_chat:flat_chat@postgres:5432/flat_chat")
            wfsingest.load(gdf = gdf, chunksize = 5000)

        print("Success")


if __name__ == "__main__":
    main()
