"""Product registry — what models/formats the WebUI exposes.

A *product* is a (model, format) pair the user can fetch — e.g.
GFS 0.25° (pgrb2.0p25), GFS 0.5° (pgrb2.0p50), HRRR (wrfprsf), NAM-CONUS.
Each product owns its own variable/level catalog and declares which
sources + bbox are appropriate; the rest of sharktopus (fetch, crop,
batch, inventory) is product-agnostic.

Today only ``gfs.pgrb2.0p25`` ships. Adding a new product is a
non-breaking change: drop a JSON under ``data/products/`` and register
it in :data:`PRODUCTS`. See ``docs/ADDING_A_PRODUCT.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Product:
    """Metadata describing one fetchable product."""

    #: Stable identifier, safe for URLs and JSON. e.g. ``"gfs.pgrb2.0p25"``.
    id: str
    #: Human-readable label for the dropdown. e.g. ``"GFS 0.25° (pgrb2.0p25)"``.
    label: str
    #: Upstream model name. e.g. ``"GFS"``, ``"HRRR"``.
    model: str
    #: File-suffix code passed through to source ``build_url`` /
    #: ``canonical_filename``. e.g. ``"pgrb2.0p25"``. This is what the
    #: fetch layer actually understands.
    code: str
    #: One-liner shown under the dropdown.
    description: str
    #: Catalog JSON filename under ``data/products/``.
    catalog_file: str
    #: Suggested bbox (lat_s, lat_n, lon_w, lon_e) when the user opens a
    #: fresh form. ``None`` = global. Informational only today; future
    #: work may clamp the map picker for CONUS-only products (HRRR).
    default_bbox: tuple[float, float, float, float] | None = None
    #: Which registered sources can actually serve this product. Empty
    #: tuple = all registered sources. Informational for the UI today;
    #: future work will filter the Sources chip pool by this.
    sources: tuple[str, ...] = field(default_factory=tuple)


#: All products the WebUI knows about.
#:
#: The first entry is the default shown when the form opens fresh.
PRODUCTS: tuple[Product, ...] = (
    Product(
        id="gfs.pgrb2.0p25",
        label="GFS 0.25° (pgrb2.0p25)",
        model="GFS",
        code="pgrb2.0p25",
        description="NOAA Global Forecast System, 0.25° resolution, primary pressure-level product.",
        catalog_file="gfs_pgrb2_0p25.json",
        # GFS is a global model — the full sphere is valid. Declared
        # explicitly so every product has a coverage; regional models
        # (HRRR CONUS, NAM, etc.) will narrow this.
        default_bbox=(-90.0, 90.0, -180.0, 180.0),
    ),
    # Future — uncomment + ship catalog to enable:
    # Product(id="gfs.pgrb2.0p50", label="GFS 0.5° (pgrb2.0p50)", ...),
    # Product(id="gfs.pgrb2b.0p25", label="GFS 0.25° secondary (pgrb2b.0p25)", ...),
    # Product(id="hrrr.wrfprsf", label="HRRR CONUS (wrfprsf)", ...,
    #         default_bbox=(21.14, 52.62, -134.1, -60.9),
    #         sources=("aws_hrrr", "gcloud_hrrr", "nomads_hrrr")),
)


def list_products() -> tuple[Product, ...]:
    """All registered products, in display order (default first)."""
    return PRODUCTS


def get_product(product_id: str | None) -> Product:
    """Look up a product by id. Returns the default when ``id`` is falsy
    or unknown — the form should never crash because of a stale value.
    """
    if product_id:
        for p in PRODUCTS:
            if p.id == product_id:
                return p
    return PRODUCTS[0]


def default_product() -> Product:
    """The product shown first in a fresh Submit form."""
    return PRODUCTS[0]


def resolve_code(product_id: str | None) -> str:
    """Translate a UI product id → the wire-format code sharktopus's
    fetch layer expects (e.g. ``"pgrb2.0p25"``).
    """
    return get_product(product_id).code


def products_dir() -> Path:
    """Directory holding product catalog JSONs (bundled)."""
    return Path(__file__).resolve().parent / "data" / "products"
