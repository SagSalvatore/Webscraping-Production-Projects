import json
import os

def translate_value(val, mapping):
    if isinstance(val, str):
        # Check if the value is in the mapping exactly
        if val in mapping:
            return mapping[val]
        
        # Handle cases like "3 Jahr(e)" or lists of terms
        new_val = val
        for de, en in mapping.items():
            if de in new_val:
                new_val = new_val.replace(de, en)
        return new_val
    return val

def translate_json(input_path, output_path):
    mapping = {
        # Keys
        "Eingangsnummer": "Entry Number",
        "Arzneimittelbezeichnung": "Medicinal Product Name",
        "Anzahl der Wirkstoffe im AM": "Number of active ingredients in the product",
        "Berechnete Stärke": "Calculated strength",
        "Darreichungsform": "Dosage form",
        "Zielgruppe (Domain)": "Target group (Domain)",
        "Status der Version": "Version status",
        "Hauptversionsnummer": "Main version number",
        "Regulatorische Aktivität": "Regulatory activity",
        "Datensatz zuletzt aktualisiert am": "Dataset last updated on",
        "Anwendungsgebiete": "Therapeutic indications",
        "Indikation/ATC-Code": "Indication/ATC code",
        "Indikation/ATC-Bedeutung": "Indication/ATC meaning",
        "Indikation/ATC-Hierarchie": "Indication/ATC hierarchy",
        "Verkehrsfähigkeit": "Marketability",
        "Zuständigkeit": "Authority",
        "Ordnungsnummer (Nachzulassung)": "ID number (Re-registration)",
        "Zulassungsnummer/ Registrierungsnummer": "Authorization/Registration number",
        "Datum der Zulassung/Registrierung (Wirksamkeitsdatum)": "Date of authorization/registration (Effective date)",
        "Verfahrenstyp": "Procedure type",
        "Antragsart": "Application type",
        "Arzneimitteltyp": "Medicine type",
        "Status": "Status",
        "Stammnummer zur Eingangsnummer": "Master number for entry number",
        "Bescheidart": "Type of notice",
        "Bescheid-datum": "Notice date",
        "DauerderGültig-keit": "Validity period",
        "DatumderPost-zustell-urkunde": "Date of mailing",
        "Bundes-anzeiger-datum": "Federal Gazette date",
        "Bundes-anzeiger-nummer": "Federal Gazette number",
        "PU-Nummer": "PU number",
        "Name": "Name",
        "Straße Hausnummer": "Street and house number",
        "PLZ Ort": "ZIP code and City",
        "Regierungsbezirk": "Administrative district",
        "Bundesland": "State",
        "Land": "Country",
        "Anzahl der Teile (Pharmazeutische Produkte, PPT)": "Number of parts (Pharmaceutical products, PPT)",
        "Laufende Nummer des PPT": "Serial number of the PPT",
        "Reihenfolge des PPT": "Sequence of the PPT",
        "Anwendungsart": "Route of administration",
        "Mengenangabe Bezugsmenge": "Reference quantity specification",
        "Anzahl der Wirkstoffe im PPT": "Number of active ingredients in the PPT",
        "ASK-Nr.": "ASK No.",
        "Stoffname": "Substance name",
        "Potenz": "Potency",
        "Mengen-operator": "Quantity operator",
        "Stoffmenge": "Substance quantity",
        "Stoffmengemaximal": "Max substance quantity",
        "Maßeinheit": "Unit of measure",
        "Bemerkung": "Remark",
        "Darreichungsform, verkürzt": "Dosage form, abbreviated",
        "Wirkstoffbezeichnung": "Active ingredient name",
        "Wirkstärke": "Strength",
        "Pharmazentralnummern (PZN)": "Pharmaceutical Central Numbers (PZN)",
        "Packungsgrößen-ID": "Package size ID",
        "Packungsgröße": "Package size",
        "Äußere Behältnisse": "Outer containers",
        "Innere Behältnisse": "Inner containers",
        "Verschluss": "Closure",
        "Beigefügtes Medizinprodukt": "Accompanying medical device",
        "Verkaufsabgrenzung": "Sales classification",
        "Rechtsgrundlage für Vertrieb": "Legal basis for sales",
        "Dauer der Haltbarkeit der Packung": "Shelf life of the package",
        "Art der Dauer der Haltbarkeit": "Type of shelf life",
        "Lagerungs- bzw. Transportbedingungen": "Storage and transport conditions",
        "Besonderheiten": "Special features",
        "Historische Arzneimittelbezeichnung": "Historical medicinal product name",
        "Gültig ab": "Valid from",
        
        # Values
        "Mensch": "Human",
        "zugelassen": "authorized",
        "Filmtablette": "Film-coated tablet",
        "Tablette": "Tablet",
        "zum Einnehmen": "Oral use",
        "Unbefristet": "Indefinite",
        "Deutschland": "Germany",
        "Milligramm": "Milligrams",
        "Stück": "Units",
        "apothekenpflichtig": "Pharmacy-only",
        "Jahr(e)": "Year(s)",
        "Originalverschlossene Packung": "Original sealed package",
        "Vor Feuchtigkeit schützen": "Protect from moisture",
        "In Originalverpackung aufbewahren": "Keep in original packaging",
        "Nicht über +25°C": "Do not store above +25°C",
        "Fiktive Zulassung": "Fictitious authorization",
        "Zulassung": "Authorization",
        "Antrag auf Inverkehrbringen": "Application for marketing",
        "Ja": "Yes",
        "Blisterpackung": "Blister pack",
        "Polyvinylchlorid": "Polyvinyl chloride",
        "Aluminium": "Aluminum",
        "Folie": "Foil",
        "Faltschachtel": "Folding box",
        "Originalpackung": "Original pack",
        "Information nicht vorhanden": "Information not available",
        "RegulatorischeAktivität": "Regulatory Activity",
        "Lagerung:": "Storage:",
        "gemäß": "according to",
        "oder": "or",
        "Eierschalen": "Eggshells",
        "Calciumcarbonat": "Calcium carbonate",
        "Talkum": "Talc",
        "Kartoffelstärke": "Potato starch",
        "Eisenoxide und -hydroxide": "Iron oxides and hydroxides",
        "Arabisches Gummi": "Acacia / Gum arabic",
        "Sucrose": "Sucrose",
        "Lactose-Monohydrat": "Lactose monohydrate",
        "Citronensäure": "Citric acid",
        "Carnaubawachs": "Carnauba wax"
    }

    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    def process_element(elem):
        if isinstance(elem, list):
            return [process_element(e) for e in elem]
        elif isinstance(elem, dict):
            return {translate_value(k, mapping): process_element(v) for k, v in elem.items()}
        else:
            return translate_value(elem, mapping)

    translated_data = process_element(data)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(translated_data, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    translate_json('product_complete.json', 'product_complete_en.json')
    print("Translation completed successfully. Saved to product_complete_en.json")
