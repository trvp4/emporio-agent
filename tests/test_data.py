from emporio.core.quality import run_checks


def test_loads_every_table(store):
    assert len(store.categories) == 9
    assert len(store.products) == 65
    assert len(store.customers) == 50
    assert len(store.promotions) == 25
    assert len(store.orders) == 20


def test_order_items_are_attached(store):
    assert [i.product_id for i in store.orders[15].items] == [82, 106]
    assert [i.product_id for i in store.orders[20].items] == [107, 84]


def test_specs_are_parsed(store):
    assert store.products[81].specs["top"] == "Spruce"


def test_availability(store):
    assert store.products[95].is_available  # Takamine GD20, active, stock 5
    assert not store.products[96].is_available  # active but stock 0
    assert not store.products[113].is_available  # discontinued
    assert not store.products[130].is_available  # coming soon


def test_only_active_promotions_count(store):
    assert store.active_promotion(127).discount_percent == 10
    assert store.active_promotion(102) is None  # Black Friday record, inactive
    assert store.active_promotion(96) is None  # two records, both inactive


def test_find_category_handles_plurals_and_accents(store):
    assert store.find_category("violões").id == 5
    assert store.find_category("violao").id == 5
    assert store.find_category("Guitarras").id == 1
    assert store.find_category("contrabaixo acústico").id == 8
    assert store.find_category("baixo").id == 2
    assert store.find_category("ukulele").id == 9
    assert store.find_category("saxofone").id == 6
    assert store.find_category("pandeiro") is None


def test_quality_report_finds_known_issues(store):
    findings = " ".join(i.finding for i in run_checks(store))
    assert "#96 Giannini GF-3D" in findings
    assert "#136 Ibanez Bass 2X" in findings
    assert "appears in orders #3, #14" in findings
    assert "share the e-mail bruno.martins@jmail.com" in findings
    assert "Order #8 is 'shipped'" in findings
