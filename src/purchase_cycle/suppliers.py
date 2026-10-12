"""Fictional suppliers of the distributor and the product families each one sells.

The families overlap so every catalog product is sold by at least two suppliers.
"""

from dataclasses import dataclass

from purchase_cycle.catalog import PRODUCTS


@dataclass(frozen=True)
class Supplier:
    code: str
    name: str
    email: str


SUPPLIERS = [
    Supplier("SUP-001", "Suministros Medicos Iberia S.L.", "ofertas@suministros-iberia.example.com"),
    Supplier("SUP-002", "Distribuciones Sanitarias del Norte S.A.", "ventas@dissanorte.example.com"),
    Supplier("SUP-003", "Material Clinico Levante S.L.", "comercial@clinicolevante.example.com"),
]


def _supplier_families() -> list[tuple[str, str]]:
    """Family i goes to suppliers i and i + 1 (mod 3); every fourth family also goes to the third one."""
    families = list(dict.fromkeys(p.family for p in PRODUCTS))
    pairs = []
    for i, family in enumerate(families):
        chosen = {i % 3, (i + 1) % 3} | ({(i + 2) % 3} if i % 4 == 0 else set())
        pairs += [(SUPPLIERS[k].code, family) for k in sorted(chosen)]
    return pairs


SUPPLIER_FAMILIES = _supplier_families()
