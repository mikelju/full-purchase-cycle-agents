"""Permanent fictional catalog and customers of the distributor.

Every later phase builds on these rows, so changes here are deliberate:
they change the prompt, invalidate every recording and need a new baseline.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    family: str
    name: str
    sale_unit: str
    price_eur: float
    latex: bool
    sterile: bool


@dataclass(frozen=True)
class Customer:
    code: str
    name: str
    kind: str
    city: str
    contact_name: str
    email: str
    phone: str


def _family(family, rows, *, latex=False, sterile=False):
    """Build products from (sku, name, sale_unit, price) rows."""
    return [Product(sku, family, name, unit, price, latex, sterile) for sku, name, unit, price in rows]


def _sized(prefix, base_name, sizes, unit, price):
    return [(f"{prefix}-{s}", f"{base_name}, size {s}", unit, price) for s in sizes]


GLOVE_SIZES = ["XS", "S", "M", "L", "XL"]
SURGICAL_SIZES = ["6.5", "7", "7.5", "8", "8.5"]


def _build_products():
    p = []
    # 1-4 Examination and surgical gloves
    p += _family(
        "Nitrile examination gloves",
        _sized("GLV-NIT", "Nitrile examination gloves, powder-free", GLOVE_SIZES, "box of 100", 6.90)
        + _sized(
            "GLV-NIT200", "Nitrile examination gloves, powder-free, value pack", ["S", "M", "L"], "box of 200", 12.50
        )
        + _sized(
            "GLV-NITX", "Nitrile examination gloves, extended cuff 30 cm", ["S", "M", "L", "XL"], "box of 50", 9.40
        ),
    )
    p += _family(
        "Latex examination gloves",
        _sized("GLV-LTX", "Latex examination gloves, powder-free", GLOVE_SIZES[1:], "box of 100", 5.80)
        + _sized("GLV-LTXP", "Latex examination gloves, lightly powdered", ["S", "M", "L"], "box of 100", 5.20),
        latex=True,
    )
    p += _family(
        "Vinyl examination gloves",
        _sized("GLV-VIN", "Vinyl examination gloves, powder-free", GLOVE_SIZES[1:], "box of 100", 4.10),
    )
    p += _family(
        "Sterile surgical gloves",
        _sized("GLV-SURG-LTX", "Sterile latex surgical gloves", SURGICAL_SIZES, "box of 50 pairs", 32.00),
        latex=True,
        sterile=True,
    )
    p += _family(
        "Sterile surgical gloves",
        _sized(
            "GLV-SURG-NEO", "Sterile latex-free surgical gloves (neoprene)", SURGICAL_SIZES, "box of 50 pairs", 44.00
        ),
        sterile=True,
    )
    # 5 Face masks
    p += _family(
        "Face masks",
        [
            ("MASK-IIR", "Surgical face mask type IIR, ear loops", "box of 50", 4.50),
            ("MASK-IIR-TIE", "Surgical face mask type IIR, tie-on", "box of 50", 6.20),
            ("MASK-II", "Surgical face mask type II, ear loops", "box of 50", 3.60),
            ("MASK-KIDS", "Children's surgical face mask", "box of 50", 4.20),
            ("MASK-FFP2", "FFP2 respirator mask, no valve", "box of 20", 9.80),
            ("MASK-FFP2-V", "FFP2 respirator mask with exhalation valve", "box of 10", 8.90),
            ("MASK-FFP2-BLK", "FFP2 respirator mask, no valve, black", "box of 20", 10.90),
            ("MASK-FFP3", "FFP3 respirator mask, no valve", "box of 10", 14.50),
            ("MASK-FFP3-V", "FFP3 respirator mask with exhalation valve", "box of 10", 15.90),
        ],
    )
    # 6 Gauze
    gauze = []
    for size, code in [("5 x 5 cm", "5"), ("7.5 x 7.5 cm", "7"), ("10 x 10 cm", "10")]:
        gauze.append((f"GAUZE-ST-{code}", f"Sterile gauze swabs {size}, 8 ply", "pack of 25 envelopes", 3.40))
        gauze.append((f"GAUZE-NS-{code}", f"Non-sterile gauze swabs {size}, 8 ply", "pack of 100", 2.90))
        gauze.append((f"GAUZE-NS-{code}-12", f"Non-sterile gauze swabs {size}, 12 ply", "pack of 100", 3.60))
    p += _family("Gauze", [r for r in gauze if r[0].startswith("GAUZE-NS")])
    p += _family("Gauze", [r for r in gauze if r[0].startswith("GAUZE-ST")], sterile=True)
    p += _family(
        "Gauze",
        [
            ("GAUZE-ROLL-5", "Gauze bandage roll 5 cm x 4 m", "bag of 12", 2.80),
            ("GAUZE-ROLL-7", "Gauze bandage roll 7.5 cm x 4 m", "bag of 12", 3.40),
            ("GAUZE-ROLL-10", "Gauze bandage roll 10 cm x 4 m", "bag of 12", 3.90),
        ],
    )
    # 7 Alcohol
    alcohol = []
    for strength in ["70", "96"]:
        for ml, label in [("100", "100 ml"), ("250", "250 ml"), ("500", "500 ml"), ("1000", "1 litre")]:
            alcohol.append((f"ALC{strength}-{ml}", f"Ethyl alcohol {strength}% {label}", "bottle", 0.9 + int(ml) / 400))
    alcohol.append(("ALC70-5000", "Ethyl alcohol 70% 5 litre jerrycan", "jerrycan", 14.00))
    p += _family("Ethyl alcohol", alcohol)
    # 8 Hand sanitiser
    p += _family(
        "Hand sanitiser gel",
        [
            ("GEL-100", "Hydroalcoholic hand sanitiser gel 100 ml", "bottle", 1.60),
            ("GEL-500", "Hydroalcoholic hand sanitiser gel 500 ml with pump", "bottle", 4.20),
            ("GEL-1000", "Hydroalcoholic hand sanitiser gel 1 litre refill", "bottle", 6.50),
            ("GEL-5000", "Hydroalcoholic hand sanitiser gel 5 litre jerrycan", "jerrycan", 24.00),
            ("GEL-WALL", "Wall dispenser for hand sanitiser, 1 litre", "unit", 18.00),
        ],
    )
    # 9 Syringes
    p += _family(
        "Syringes",
        [
            (f"SYR-{ml}", f"Disposable syringe {ml} ml, luer slip", "box of 100", 4.0 + float(ml) / 4)
            for ml in ["1", "2", "3", "5", "10", "20"]
        ]
        + [
            (f"SYR-LL-{ml}", f"Disposable syringe {ml} ml, luer lock", "box of 100", 5.0 + float(ml) / 4)
            for ml in ["5", "10", "20", "50"]
        ]
        + [
            (f"SYR-INS-{code}", f"Insulin syringe {ml} ml with fixed needle", "box of 100", 11.00)
            for code, ml in [("03", "0.3"), ("05", "0.5"), ("10", "1")]
        ],
        sterile=True,
    )
    # 10 Needles
    p += _family(
        "Hypodermic needles",
        [
            (f"NDL-{g}G", f"Hypodermic needle {g}G x {length}", "box of 100", 3.20)
            for g, length in [
                ("18", "40 mm"),
                ("19", "40 mm"),
                ("20", "40 mm"),
                ("21", "40 mm"),
                ("22", "30 mm"),
                ("23", "25 mm"),
                ("25", "16 mm"),
                ("26", "13 mm"),
                ("27", "13 mm"),
                ("30", "13 mm"),
            ]
        ],
        sterile=True,
    )
    p += _family(
        "Hypodermic needles",
        [
            (f"PEN-{g}-{mm}", f"Pen needle {g}G x {mm} mm", "box of 100", 16.50)
            for g, mm in [("31", "5"), ("31", "8"), ("32", "4"), ("32", "6")]
        ],
        sterile=True,
    )
    # 11 Thermometers
    p += _family(
        "Thermometers",
        [
            ("THERM-DIG", "Digital thermometer, rigid tip", "unit", 3.20),
            ("THERM-DIG-FLEX", "Digital thermometer, flexible tip", "unit", 4.10),
            ("THERM-IR", "Infrared forehead thermometer, non-contact", "unit", 24.00),
            ("THERM-EAR", "Infrared ear thermometer", "unit", 29.00),
            ("THERM-COVER", "Disposable probe covers for ear thermometer", "box of 40", 6.00),
            ("THERM-SHEATH", "Disposable sheaths for digital thermometer", "box of 100", 4.50),
        ],
    )
    # 12 Plasters
    p += _family(
        "Adhesive plasters",
        [
            ("PLST-ASST", "Adhesive plasters, assorted sizes", "box of 100", 3.10),
            ("PLST-WP", "Waterproof adhesive plasters, assorted sizes", "box of 100", 3.90),
            ("PLST-FAB", "Fabric adhesive plasters, assorted sizes", "box of 100", 3.70),
            ("PLST-KIDS", "Children's adhesive plasters with drawings", "box of 50", 2.90),
            ("PLST-BLUE", "Blue detectable plasters for food handlers", "box of 100", 5.50),
            ("PLST-ROLL-6", "Cut-to-size plaster strip 6 cm x 1 m", "roll", 1.80),
            ("PLST-ROLL-8", "Cut-to-size plaster strip 8 cm x 1 m", "roll", 2.10),
            ("PLST-SPOT", "Round spot plasters 22 mm", "box of 100", 2.40),
        ],
    )
    # 13 Bandages
    p += _family(
        "Elastic bandages",
        [
            (f"BND-ELA-{c}", f"Elastic crepe bandage {cm} x 4.5 m", "pack of 10", 3.0 + i)
            for i, (c, cm) in enumerate([("5", "5 cm"), ("7", "7.5 cm"), ("10", "10 cm"), ("15", "15 cm")])
        ]
        + [
            (f"BND-COH-{c}", f"Cohesive bandage {cm} x 4.5 m", "pack of 10", 6.0 + i)
            for i, (c, cm) in enumerate([("5", "5 cm"), ("7", "7.5 cm"), ("10", "10 cm")])
        ]
        + [
            ("BND-TRI", "Triangular bandage, non-woven", "pack of 10", 4.80),
            ("BND-TUB-C", "Tubular bandage size C, 10 m", "roll", 7.20),
            ("BND-TUB-E", "Tubular bandage size E, 10 m", "roll", 8.40),
        ],
    )
    # 14 Tape
    p += _family(
        "Medical tape",
        [
            (f"TAPE-PAP-{c}", f"Microporous paper tape {w} x 9.1 m", "box of 12 rolls", price)
            for c, w, price in [("12", "1.25 cm", 4.20), ("25", "2.5 cm", 6.10), ("50", "5 cm", 9.80)]
        ]
        + [
            (f"TAPE-SILK-{c}", f"Silk tape {w} x 9.1 m", "box of 12 rolls", price)
            for c, w, price in [("12", "1.25 cm", 6.30), ("25", "2.5 cm", 8.90)]
        ]
        + [
            (f"TAPE-TR-{c}", f"Transparent tape {w} x 9.1 m", "box of 12 rolls", price)
            for c, w, price in [("12", "1.25 cm", 5.40), ("25", "2.5 cm", 7.80)]
        ]
        + [
            (f"TAPE-ZNC-{c}", f"Zinc oxide tape {w} x 5 m", "box of 10 rolls", price)
            for c, w, price in [("25", "2.5 cm", 9.00), ("50", "5 cm", 13.50)]
        ]
        + [("TAPE-KIN", "Kinesiology tape 5 cm x 5 m, black", "roll", 5.90)],
    )
    # 15 Dressings
    p += _family(
        "Wound dressings",
        [
            (f"DRS-ISL-{c}", f"Adhesive island dressing {size}", "box of 50", price)
            for c, size, price in [
                ("5x7", "5 x 7.2 cm", 6.50),
                ("9x10", "9 x 10 cm", 9.20),
                ("9x15", "9 x 15 cm", 12.10),
                ("9x25", "9 x 25 cm", 16.80),
            ]
        ]
        + [
            (f"DRS-FOAM-{c}", f"Foam dressing {size}, with border", "box of 10", price)
            for c, size, price in [("10", "10 x 10 cm", 21.00), ("15", "15 x 15 cm", 34.00)]
        ]
        + [
            (f"DRS-FILM-{c}", f"Transparent film dressing {size}", "box of 50", price)
            for c, size, price in [("6x7", "6 x 7 cm", 12.00), ("10x12", "10 x 12 cm", 19.00)]
        ]
        + [
            ("DRS-EYE", "Sterile eye pad", "box of 50", 7.40),
            ("DRS-NONADH-10", "Non-adherent dressing pad 10 x 10 cm", "box of 100", 11.50),
        ],
        sterile=True,
    )
    p += _family(
        "Wound dressings",
        [
            (f"STRIP-{c}", f"Skin closure strips {size}", "box of 50 envelopes", price)
            for c, size, price in [
                ("3x75", "3 x 75 mm", 18.00),
                ("6x75", "6 x 75 mm", 21.00),
                ("6x100", "6 x 100 mm", 24.00),
            ]
        ],
        sterile=True,
    )
    # 16 Cotton
    p += _family(
        "Cotton products",
        [
            ("COT-ROLL-100", "Cotton wool roll 100 g", "roll", 1.40),
            ("COT-ROLL-250", "Cotton wool roll 250 g", "roll", 2.60),
            ("COT-ROLL-500", "Cotton wool roll 500 g", "roll", 4.30),
            ("COT-ROLL-1000", "Cotton wool roll 1 kg", "roll", 7.90),
            ("COT-BALL", "Cotton wool balls", "bag of 100", 1.70),
            ("COT-PADS", "Round cotton pads", "bag of 80", 1.20),
            ("COT-BUDS", "Cotton buds, paper stem", "box of 200", 1.50),
            ("COT-APPL", "Sterile cotton-tipped applicators 15 cm", "box of 100", 4.90),
        ],
    )
    # 17 Wipes and pads
    p += _family(
        "Disinfectant wipes",
        [
            ("WIPE-SURF-100", "Surface disinfectant wipes, alcohol-free, canister", "canister of 100", 6.80),
            ("WIPE-SURF-200", "Surface disinfectant wipes, alcohol-free, refill", "refill of 200", 9.90),
            ("WIPE-ALC-100", "Alcohol surface wipes, canister", "canister of 100", 5.90),
            ("WIPE-PAD-100", "Alcohol prep pads, single-use, box of 100", "box of 100", 2.10),
            ("WIPE-PAD-200", "Alcohol prep pads, single-use, box of 200", "box of 200", 3.80),
            ("WIPE-WET-80", "Wet body wipes for patient hygiene", "pack of 80", 2.70),
            ("WIPE-WASH-CAP", "Rinse-free shampoo cap", "pack of 10", 14.00),
            ("WIPE-WASHGLV", "Pre-moistened wash gloves", "pack of 8", 3.10),
        ],
    )
    # 18 Saline
    p += _family(
        "Saline solution",
        [
            ("SAL-5", "Saline solution 0.9%, single-dose 5 ml", "box of 30", 3.20),
            ("SAL-10", "Saline solution 0.9%, single-dose 10 ml", "box of 30", 4.10),
            ("SAL-30", "Saline solution 0.9%, single-dose 30 ml", "box of 20", 5.60),
            ("SAL-250", "Saline solution 0.9% for irrigation 250 ml", "bottle", 1.90),
            ("SAL-500", "Saline solution 0.9% for irrigation 500 ml", "bottle", 2.60),
            ("SAL-1000", "Saline solution 0.9% for irrigation 1 litre", "bottle", 3.40),
        ],
        sterile=True,
    )
    # 19 Underpads
    p += _family(
        "Underpads",
        [
            (f"PAD-{c}", f"Disposable underpad {size}", "pack of 25", price)
            for c, size, price in [
                ("40x60", "40 x 60 cm", 4.20),
                ("60x60", "60 x 60 cm", 5.30),
                ("60x90", "60 x 90 cm", 7.10),
            ]
        ]
        + [("PAD-REUSE-75", "Reusable washable bed pad 75 x 90 cm", "unit", 9.50)],
    )
    # 20 Incontinence
    p += _family(
        "Incontinence products",
        [(f"INC-BRF-{s}", f"Adult incontinence briefs, size {s}", "pack of 20", 11.50) for s in ["S", "M", "L", "XL"]]
        + [
            (f"INC-BRF-NIGHT-{s}", f"Adult incontinence briefs, night, size {s}", "pack of 15", 13.20)
            for s in ["M", "L"]
        ]
        + [(f"INC-PANT-{s}", f"Adult pull-up pants, size {s}", "pack of 14", 9.80) for s in ["M", "L", "XL"]]
        + [
            (f"INC-PAD-{c}", f"Incontinence pads, {absorb} absorbency", "pack of 28", price)
            for c, absorb, price in [("LIGHT", "light", 4.20), ("NORMAL", "normal", 5.10), ("MAXI", "maxi", 6.30)]
        ],
    )
    # 21 Blood pressure
    p += _family(
        "Diagnostic devices",
        [
            ("BPM-ARM", "Automatic upper arm blood pressure monitor", "unit", 39.00),
            ("BPM-WRIST", "Automatic wrist blood pressure monitor", "unit", 29.00),
            ("BPM-ANER", "Aneroid sphygmomanometer with stethoscope", "unit", 26.00),
            ("BPM-CUFF-S", "Replacement blood pressure cuff, small adult 17-22 cm", "unit", 12.00),
            ("BPM-CUFF-M", "Replacement blood pressure cuff, adult 22-32 cm", "unit", 12.00),
            ("BPM-CUFF-L", "Replacement blood pressure cuff, large adult 32-42 cm", "unit", 13.50),
            ("STETH-DUAL", "Dual-head stethoscope", "unit", 15.00),
            ("STETH-PAED", "Paediatric stethoscope", "unit", 17.00),
        ],
    )
    # 22 Oximeters
    p += _family(
        "Diagnostic devices",
        [
            ("OXI-FING", "Fingertip pulse oximeter, adult", "unit", 19.00),
            ("OXI-PAED", "Fingertip pulse oximeter, paediatric", "unit", 23.00),
            ("OXI-HAND", "Handheld pulse oximeter with probe", "unit", 120.00),
        ],
    )
    # 23 Glucose
    p += _family(
        "Blood glucose testing",
        [
            ("GLU-METER", "Blood glucose meter kit", "unit", 18.00),
            ("GLU-STRIP-50", "Blood glucose test strips, box of 50", "box of 50", 14.00),
            ("GLU-STRIP-100", "Blood glucose test strips, box of 100", "box of 100", 26.00),
            ("LAN-100", "Safety lancets 28G, box of 100", "box of 100", 7.50),
            ("LAN-200", "Safety lancets 28G, box of 200", "box of 200", 13.50),
            ("LAN-PEN", "Lancet pen device", "unit", 6.00),
        ],
        sterile=True,
    )
    # 24 Specimen collection
    p += _family(
        "Specimen collection",
        [
            ("URN-CUP-60", "Sterile urine container 60 ml", "box of 100", 9.00),
            ("URN-CUP-100", "Sterile urine container 100 ml", "box of 100", 11.00),
            ("URN-BAG-2L", "Urine drainage bag 2 litres with tap", "box of 10", 6.50),
            ("URN-BAG-LEG", "Leg urine bag 750 ml", "box of 10", 9.50),
            ("SWAB-TRANS", "Sterile transport swab with medium", "box of 50", 22.00),
            ("STOOL-CUP", "Stool sample container 30 ml", "box of 100", 10.00),
        ],
        sterile=True,
    )
    # 25 Catheters
    p += _family(
        "Urinary catheters",
        [
            (f"CATH-FOL-{fr}", f"Foley catheter 2-way, silicone-coated latex, {fr} Fr", "box of 10", 14.00)
            for fr in ["12", "14", "16", "18"]
        ],
        latex=True,
        sterile=True,
    )
    p += _family(
        "Urinary catheters",
        [
            (f"CATH-SIL-{fr}", f"Foley catheter 2-way, 100% silicone, {fr} Fr", "box of 10", 29.00)
            for fr in ["12", "14", "16", "18"]
        ],
        sterile=True,
    )
    # 26 Couch paper
    p += _family(
        "Examination consumables",
        [
            ("COUCH-50", "Examination couch paper roll 50 cm x 50 m", "pack of 6", 13.00),
            ("COUCH-60", "Examination couch paper roll 60 cm x 50 m", "pack of 6", 15.00),
            ("COUCH-60-SMOOTH", "Smooth examination couch roll 60 cm x 100 m", "pack of 6", 24.00),
        ],
    )
    # 27 Protective clothing
    p += _family(
        "Protective clothing",
        [
            ("GOWN-ISO", "Disposable isolation gown, non-woven, blue", "pack of 10", 8.90),
            ("GOWN-ISO-XL", "Disposable isolation gown, non-woven, blue, XL", "pack of 10", 9.90),
            ("GOWN-SURG-ST", "Sterile surgical gown, reinforced, L", "box of 20", 54.00),
            ("APRON-PE", "Disposable plastic apron, white", "roll of 200", 7.80),
            ("SHOE-COVER", "Disposable shoe covers, blue", "pack of 100", 3.90),
            ("CAP-BOUF", "Bouffant cap, non-woven", "pack of 100", 3.40),
            ("GOGGLES", "Protective goggles, anti-fog", "unit", 4.80),
            ("VISOR", "Face shield visor, reusable frame", "unit", 3.90),
            ("COVERALL-L", "Disposable protective coverall, size L", "unit", 6.50),
            ("COVERALL-XL", "Disposable protective coverall, size XL", "unit", 6.50),
        ],
    )
    # 28 Small consumables
    p += _family(
        "Examination consumables",
        [
            ("TONGUE-AD", "Wooden tongue depressors, adult", "box of 100", 2.20),
            ("TONGUE-ST", "Sterile wooden tongue depressors, individually wrapped", "box of 100", 4.60),
            ("TONGUE-KID", "Wooden tongue depressors, children", "box of 100", 2.20),
            ("TOURNIQ", "Disposable tourniquet strips", "roll of 25", 4.40),
            ("SCALPEL-10", "Sterile disposable scalpel no. 10", "box of 10", 5.60),
            ("SCALPEL-11", "Sterile disposable scalpel no. 11", "box of 10", 5.60),
            ("SCALPEL-15", "Sterile disposable scalpel no. 15", "box of 10", 5.60),
            ("STITCH-CUT", "Sterile stitch cutter", "box of 100", 18.00),
            ("FORCEPS-DISP", "Sterile disposable plastic forceps", "box of 100", 16.00),
            ("KIDNEY-DISH", "Disposable kidney dish, pulp", "pack of 100", 12.00),
            ("PILL-CUP", "Plastic medicine cups 30 ml", "pack of 100", 1.60),
            ("PILL-CRUSH", "Pill crusher", "unit", 3.80),
            ("PILL-BOX-7", "Weekly pill organiser, 7 days", "unit", 2.50),
        ],
    )
    # 29 Sharps
    p += _family(
        "Sharps containers",
        [
            (f"SHARPS-{c}", f"Sharps container {label}", "unit", price)
            for c, label, price in [
                ("06", "0.6 litre", 1.40),
                ("1", "1 litre", 1.70),
                ("2", "2 litres", 2.10),
                ("5", "5 litres", 3.20),
                ("10", "10 litres", 4.90),
                ("30", "30 litres", 9.50),
            ]
        ],
    )
    # 30 Skin care
    p += _family(
        "Skin care",
        [
            ("SUN-30-50", "Sunscreen SPF 30, 50 ml", "tube", 6.20),
            ("SUN-50-50", "Sunscreen SPF 50+, 50 ml", "tube", 7.40),
            ("SUN-50-200", "Sunscreen SPF 50+, 200 ml", "bottle", 13.90),
            ("SUN-FACE-50-50", "Facial sunscreen SPF 50+, 50 ml", "tube", 9.90),
            ("AFTERSUN-200", "Aloe vera after-sun gel 200 ml", "bottle", 6.80),
            ("SUN-KIDS-50-200", "Children's sunscreen SPF 50+, 200 ml", "bottle", 14.50),
            ("CREAM-MOIST-100", "Moisturising body cream 100 ml", "tube", 3.10),
            ("CREAM-MOIST-500", "Moisturising body cream 500 ml with pump", "bottle", 7.90),
            ("CREAM-BARRIER", "Barrier cream for skin protection 100 g", "tube", 4.60),
            ("CREAM-HAND-50", "Repairing hand cream 50 ml", "tube", 2.90),
            ("CREAM-HAND-100", "Repairing hand cream 100 ml", "tube", 4.40),
            ("CREAM-FOOT", "Foot care cream for dry heels 100 ml", "tube", 5.20),
            ("OIL-MASSAGE", "Massage oil 500 ml", "bottle", 8.10),
            ("LIP-BALM", "Lip balm SPF 15", "stick", 1.90),
        ],
    )
    # 31 Hygiene
    p += _family(
        "Personal hygiene",
        [
            ("SOAP-LIQ-500", "Gentle liquid soap 500 ml", "bottle", 2.30),
            ("SOAP-LIQ-5000", "Gentle liquid soap 5 litre refill", "jerrycan", 11.00),
            ("SHOWER-GEL-750", "Dermatological shower gel 750 ml", "bottle", 4.80),
            ("SHAMPOO-500", "Mild shampoo 500 ml", "bottle", 3.90),
            ("MOUTHWASH-500", "Alcohol-free mouthwash 500 ml", "bottle", 3.70),
            ("SPONGE-SOAP", "Pre-soaped disposable sponges", "pack of 50", 6.40),
            ("RAZOR-DISP", "Disposable razors, twin blade", "pack of 10", 3.20),
            ("TOOTHBRUSH-SOFT", "Soft toothbrush", "pack of 12", 6.00),
        ],
    )
    # 32 Hot and cold therapy
    p += _family(
        "First aid",
        [
            ("ICE-INSTANT", "Instant cold pack, single-use", "box of 24", 14.00),
            ("GEL-PACK-S", "Reusable hot and cold gel pack 10 x 15 cm", "unit", 2.40),
            ("GEL-PACK-L", "Reusable hot and cold gel pack 15 x 25 cm", "unit", 3.60),
            ("HOT-WATER-BOTTLE", "Hot water bottle 2 litres", "unit", 5.90),
        ],
    )
    # 33 Mobility and daily aids
    p += _family(
        "Mobility aids",
        [
            ("CANE-ADJ", "Adjustable aluminium walking stick", "unit", 9.90),
            ("CANE-FOLD", "Folding walking stick", "unit", 12.50),
            ("CRUTCH-ADULT", "Elbow crutch, adult", "unit", 11.00),
            ("WALKER-FOLD", "Folding walking frame", "unit", 39.00),
            ("WHEELCHAIR-CUSH", "Anti-pressure wheelchair cushion", "unit", 29.00),
            ("GRAB-REACH", "Reaching aid grabber 80 cm", "unit", 7.50),
        ],
    )
    # 34 Supports
    p += _family(
        "Orthopaedic supports",
        [(f"SUP-WRIST-{s}", f"Elastic wrist support, size {s}", "unit", 8.20) for s in ["S", "M", "L"]]
        + [(f"SUP-KNEE-{s}", f"Elastic knee support, size {s}", "unit", 11.40) for s in ["S", "M", "L", "XL"]]
        + [(f"SUP-ANKLE-{s}", f"Elastic ankle support, size {s}", "unit", 9.60) for s in ["S", "M", "L"]]
        + [(f"SUP-ELBOW-{s}", f"Elastic elbow support, size {s}", "unit", 8.90) for s in ["S", "M", "L"]]
        + [
            ("SUP-LUMBAR", "Lumbar support belt, adjustable", "unit", 19.00),
            ("SLING-ARM", "Arm sling, adjustable", "unit", 4.20),
        ],
    )
    # 35 Compression stockings
    p += _family(
        "Orthopaedic supports",
        [
            (f"STOCK-KNEE-{s}", f"Compression knee-high stockings class I, size {s}", "pair", 14.00)
            for s in ["S", "M", "L"]
        ]
        + [
            (f"STOCK-THIGH-{s}", f"Compression thigh-high stockings class I, size {s}", "pair", 19.00)
            for s in ["S", "M", "L"]
        ],
    )
    # 36 First aid
    p += _family(
        "First aid",
        [
            ("KIT-FA-SMALL", "First aid kit, small, for vehicles", "unit", 12.00),
            ("KIT-FA-WALL", "First aid cabinet, wall-mounted, stocked", "unit", 49.00),
            ("KIT-FA-REFILL", "First aid kit refill pack", "unit", 18.00),
            ("BLANKET-FOIL", "Emergency foil blanket", "pack of 10", 6.50),
            ("CPR-MASK", "CPR face shield with one-way valve", "pack of 10", 9.00),
            ("BURN-GEL", "Burn relief gel dressing 10 x 10 cm", "box of 10", 26.00),
            ("EYE-WASH-500", "Eye wash station bottle 500 ml", "bottle", 7.80),
        ],
    )
    return p


PRODUCTS = _build_products()

_CUSTOMER_ROWS = [
    # Clinics
    ("CLI-001", "Clinica Dental Arenal", "clinic", "Bilbao", "Laura Etxeberria"),
    ("CLI-002", "Centro Medico Indautxu", "clinic", "Bilbao", "Iker Zubiri"),
    ("CLI-003", "Clinica Fisioterapia Deusto", "clinic", "Bilbao", "Ane Goikoetxea"),
    ("CLI-004", "Policlinica Gipuzkoa Norte", "clinic", "San Sebastian", "Jon Arrieta"),
    ("CLI-005", "Centro Medico Lakua", "clinic", "Vitoria", "Maite Ugarte"),
    ("CLI-006", "Centro Medico Salamanca", "clinic", "Madrid", "Carlos Ruiz"),
    ("CLI-007", "Clinica Dermatologica Retiro", "clinic", "Madrid", "Elena Navarro"),
    ("CLI-008", "Centro de Salud Privado Chamberi", "clinic", "Madrid", "Pablo Ortega"),
    ("CLI-009", "Clinica Podologica Gracia", "clinic", "Barcelona", "Marta Puig"),
    ("CLI-010", "Centre Medic Eixample", "clinic", "Barcelona", "Jordi Ferrer"),
    ("CLI-011", "Clinica Dental Ruzafa", "clinic", "Valencia", "Lucia Soler"),
    ("CLI-012", "Centro Medico Triana", "clinic", "Seville", "Manuel Romero"),
    ("CLI-013", "Clinica de Fisioterapia Centro", "clinic", "Zaragoza", "Sara Lopez"),
    ("CLI-014", "Policlinica Malaga Este", "clinic", "Malaga", "Javier Moreno"),
    ("CLI-015", "Clinica Oftalmologica Riazor", "clinic", "A Coruna", "Noelia Varela"),
    ("CLI-016", "Centro Medico Pamplona", "clinic", "Pamplona", "Mikel Iriarte"),
    ("CLI-017", "Clinica Dental Gijon Centro", "clinic", "Gijon", "Ruben Alvarez"),
    ("CLI-018", "Centro de Estetica Medica Santander", "clinic", "Santander", "Paula Gutierrez"),
    ("CLI-019", "Clinica Medica Valladolid", "clinic", "Valladolid", "Alberto Sanz"),
    ("CLI-020", "Clinica de Rehabilitacion Logrono", "clinic", "Logrono", "Irene Saenz"),
    # Care homes
    ("RES-001", "Residencia Los Robles", "care_home", "Bilbao", "Begona Larrea"),
    ("RES-002", "Residencia Itsasbegi", "care_home", "Getxo", "Josu Aguirre"),
    ("RES-003", "Residencia San Prudencio", "care_home", "Vitoria", "Nerea Ochoa"),
    ("RES-004", "Residencia Aiete", "care_home", "San Sebastian", "Xabier Lasa"),
    ("RES-005", "Residencia El Pinar", "care_home", "Madrid", "Rosa Jimenez"),
    ("RES-006", "Residencia Santa Clara", "care_home", "Toledo", "Antonio Diaz"),
    ("RES-007", "Residencia Mar Blava", "care_home", "Tarragona", "Montse Vidal"),
    ("RES-008", "Residencia La Alameda", "care_home", "Valencia", "Vicente Marti"),
    ("RES-009", "Residencia Los Olivos", "care_home", "Cordoba", "Carmen Ruiz"),
    ("RES-010", "Residencia Monte Alto", "care_home", "Burgos", "Fernando Gil"),
    ("RES-011", "Residencia Camino de Santiago", "care_home", "Leon", "Beatriz Fernandez"),
    ("RES-012", "Residencia Ribera del Ebro", "care_home", "Zaragoza", "Pilar Lacasa"),
    ("RES-013", "Residencia Sol de Levante", "care_home", "Alicante", "Raquel Pastor"),
    ("RES-014", "Residencia Valle Verde", "care_home", "Oviedo", "Adrian Menendez"),
    ("RES-015", "Residencia Las Acacias", "care_home", "Murcia", "Encarna Sanchez"),
    # Pharmacies
    ("FAR-001", "Farmacia Gran Via", "pharmacy", "Bilbao", "Idoia Bilbao"),
    ("FAR-002", "Farmacia Casco Viejo", "pharmacy", "Bilbao", "Unai Elorza"),
    ("FAR-003", "Farmacia Abando", "pharmacy", "Bilbao", "Amaia Urrutia"),
    ("FAR-004", "Farmacia Gros", "pharmacy", "San Sebastian", "Garazi Otegi"),
    ("FAR-005", "Farmacia Lakua", "pharmacy", "Vitoria", "Oier Bengoa"),
    ("FAR-006", "Farmacia Lavapies", "pharmacy", "Madrid", "Silvia Castro"),
    ("FAR-007", "Farmacia Arguelles", "pharmacy", "Madrid", "Diego Herrera"),
    ("FAR-008", "Farmacia Sants", "pharmacy", "Barcelona", "Nuria Roca"),
    ("FAR-009", "Farmacia Benimaclet", "pharmacy", "Valencia", "Ximo Peris"),
    ("FAR-010", "Farmacia Nervion", "pharmacy", "Seville", "Rocio Dominguez"),
    ("FAR-011", "Farmacia del Puerto", "pharmacy", "Cadiz", "Alvaro Benitez"),
    ("FAR-012", "Farmacia Plaza Mayor", "pharmacy", "Salamanca", "Ines Martin"),
    ("FAR-013", "Farmacia El Carmen", "pharmacy", "Murcia", "Jose Antonio Lopez"),
    ("FAR-014", "Farmacia La Paz", "pharmacy", "Palma", "Margalida Riera"),
    ("FAR-015", "Farmacia Las Canteras", "pharmacy", "Las Palmas", "Yeray Santana"),
]


def _customer(i, row):
    code, name, kind, city, contact = row
    first = contact.split()[0].lower()
    return Customer(
        code=code,
        name=name,
        kind=kind,
        city=city,
        contact_name=contact,
        email=f"{first}.{code.lower()}@example.com",
        phone=f"+34 600 {100 + i:03d} {200 + i:03d}",
    )


CUSTOMERS = [_customer(i, row) for i, row in enumerate(_CUSTOMER_ROWS)]
