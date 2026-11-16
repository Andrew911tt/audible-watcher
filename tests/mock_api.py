"""Test double for the Audible catalog API (sandbox has no access to
api.audible.com). Patches audible_api._get with canned catalog data."""
import audible_api
import bookinfo

def make_product(asin, title, authors, release_date, series=None, seq=None,
                 series_asin=None, language="english"):
    p = {
        "asin": asin, "title": title,
        "authors": [{"name": a} for a in authors],
        "narrators": [{"name": "Test Narrator"}],
        "release_date": release_date,
        "runtime_length_min": 720,
        "language": language,
        "product_images": {"500": f"https://m.media-amazon.com/{asin}.jpg"},
    }
    if series:
        p["series"] = [{"title": series, "sequence": seq,
                        "asin": series_asin or "SER123"}]
    return p

CATALOG = [
    make_product("B001", "The First Book", ["Test Author"], "2024-03-01",
                 "Great Series", "1", "SER123"),
    make_product("B002", "The Second Book", ["Test Author"], "2025-06-15",
                 "Great Series", "2", "SER123"),
    make_product("B003", "Standalone Novel", ["Test Author"], "2025-11-02"),
]

NEW_RELEASE = make_product("B004", "The Third Book (NEW)", ["Test Author"],
                           "2099-10-20", "Great Series", "3", "SER123")

FOREIGN = make_product("B005", "Das dritte Buch", ["Test Author"],
                       "2026-11-01", language="german")

state = {"include_new": False, "include_foreign": False}

def fake_get(marketplace, params):
    products = list(CATALOG)
    if state["include_new"]:
        products.insert(0, NEW_RELEASE)
    if state["include_foreign"]:
        products.insert(0, FOREIGN)
    author = (params.get("author") or "").lower()
    kw = (params.get("keywords") or "").lower()
    asins = params.get("asins")
    if asins:
        wanted = set(asins.split(","))
        products = [p for p in products if p["asin"] in wanted]
    elif author:
        products = [p for p in products
                    if any(author in a["name"].lower() for a in p["authors"])]
    elif kw:
        products = [p for p in products
                    if kw in p["title"].lower()
                    or any(kw in (s.get("title") or "").lower()
                           for s in p.get("series", []))]
    page = int(params.get("page", 0))
    return {"products": products if page == 0 else [],
            "total_results": len(products)}

def fake_series_child_asins(series_asin, marketplace="us"):
    products = list(CATALOG)
    if state["include_new"]:
        products.insert(0, NEW_RELEASE)
    return [p["asin"] for p in products
            if any(s.get("asin") == series_asin
                   for s in p.get("series", []))]

# print/ebook came out before the audiobook for B004
PUBLISHED = {"The Third Book (NEW)": "2025-05-01"}

def fake_published_date(title, author=None):
    return PUBLISHED.get(title), True

def install():
    audible_api._get = fake_get
    audible_api._series_child_asins = fake_series_child_asins
    bookinfo.published_date = fake_published_date
