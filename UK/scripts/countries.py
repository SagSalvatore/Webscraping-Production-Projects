"""
countries.py - the closed list a "country outside the UK" must come from.

The model sometimes returns regions or placeholders as countries ("Worldwide",
"Middle East", "Other countries worldwide"); none of those may make a chain
International. Aliases map common short forms onto one canonical name.
"""
from __future__ import annotations

import re

COUNTRIES = """
Afghanistan|Albania|Algeria|Andorra|Angola|Antigua and Barbuda|Argentina|Armenia|Aruba|Australia|
Austria|Azerbaijan|Bahamas|Bahrain|Bangladesh|Barbados|Belarus|Belgium|Belize|Benin|Bermuda|Bhutan|
Bolivia|Bosnia and Herzegovina|Botswana|Brazil|Brunei|Bulgaria|Burkina Faso|Burundi|Cambodia|
Cameroon|Canada|Cape Verde|Cayman Islands|Chad|Chile|China|Colombia|Costa Rica|Croatia|Cuba|Curacao|
Cyprus|Czech Republic|Democratic Republic of the Congo|Denmark|Djibouti|Dominica|Dominican Republic|
Ecuador|Egypt|El Salvador|Equatorial Guinea|Estonia|Eswatini|Ethiopia|Fiji|Finland|France|Gabon|
Gambia|Georgia|Germany|Ghana|Gibraltar|Greece|Grenada|Guam|Guatemala|Guernsey|Guyana|Haiti|Honduras|
Hong Kong|Hungary|Iceland|India|Indonesia|Iran|Iraq|Ireland|Isle of Man|Israel|Italy|Ivory Coast|
Jamaica|Japan|Jersey|Jordan|Kazakhstan|Kenya|Kosovo|Kuwait|Kyrgyzstan|Laos|Latvia|Lebanon|Lesotho|
Liberia|Libya|Liechtenstein|Lithuania|Luxembourg|Macau|Madagascar|Malawi|Malaysia|Maldives|Mali|
Malta|Mauritius|Mexico|Moldova|Monaco|Mongolia|Montenegro|Morocco|Mozambique|Myanmar|Namibia|Nepal|
Netherlands|New Zealand|Nicaragua|Niger|Nigeria|North Macedonia|Norway|Oman|Pakistan|Palestine|
Panama|Papua New Guinea|Paraguay|Peru|Philippines|Poland|Portugal|Puerto Rico|Qatar|Romania|Russia|
Rwanda|Saint Lucia|San Marino|Saudi Arabia|Senegal|Serbia|Seychelles|Sierra Leone|Singapore|
Slovakia|Slovenia|South Africa|South Korea|Spain|Sri Lanka|Sudan|Suriname|Sweden|Switzerland|Syria|
Taiwan|Tajikistan|Tanzania|Thailand|Togo|Trinidad and Tobago|Tunisia|Turkey|Turkmenistan|Uganda|
Ukraine|United Arab Emirates|United States|Uruguay|Uzbekistan|Venezuela|Vietnam|Yemen|Zambia|Zimbabwe
"""
CANONICAL = {c.strip().lower(): c.strip() for c in COUNTRIES.replace("\n", "").split("|") if c.strip()}

ALIASES = {
    "usa": "United States", "us": "United States", "u.s.": "United States", "u.s.a.": "United States",
    "united states of america": "United States", "america": "United States",
    "uae": "United Arab Emirates", "dubai": "United Arab Emirates", "abu dhabi": "United Arab Emirates",
    "republic of ireland": "Ireland", "roi": "Ireland", "eire": "Ireland",
    "korea": "South Korea", "republic of korea": "South Korea",
    "holland": "Netherlands", "the netherlands": "Netherlands",
    "czechia": "Czech Republic", "turkiye": "Turkey", "türkiye": "Turkey",
    "swaziland": "Eswatini", "côte d'ivoire": "Ivory Coast", "cote d'ivoire": "Ivory Coast",
    "ksa": "Saudi Arabia", "kingdom of saudi arabia": "Saudi Arabia",
    "mainland china": "China", "prc": "China", "hong kong sar": "Hong Kong",
}

# How each canonical country may be written in the evidence text. "Ireland"
# must not be satisfied by "Northern Ireland", which is part of the UK.
TEXT_PATTERNS = {
    "United States": r"united states|\busa\b|\bu\.s\.|\bus\b|america",
    "United Arab Emirates": r"united arab emirates|\buae\b|dubai|abu dhabi",
    "Ireland": r"(?<!northern )ireland|dublin|\broi\b",
    "South Korea": r"korea",
    "Netherlands": r"netherlands|holland|amsterdam",
    "Saudi Arabia": r"saudi|\bksa\b",
    "Turkey": r"turkey|t[uü]rkiye",
}


def canonical_country(name: str) -> str | None:
    """Canonical country name, or None for regions / placeholders."""
    n = re.sub(r"\s+", " ", (name or "").strip().lower().replace("’", "'"))
    n = re.sub(r"^the ", "", n)
    return ALIASES.get(n) or CANONICAL.get(n)


def text_pattern(country: str) -> str:
    return TEXT_PATTERNS.get(country, re.escape(country.lower()))


US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC", "washington dc": "DC", "washington, d.c.": "DC",
}


def us_state_code(s: str | None) -> str | None:
    """'Illinois' / 'IL' / 'il' -> 'IL'; anything else -> None."""
    if not s:
        return None
    t = re.sub(r"\s+", " ", s.strip().lower().replace(".", ""))
    if t.upper() in US_STATES.values():
        return t.upper()
    return US_STATES.get(t)
