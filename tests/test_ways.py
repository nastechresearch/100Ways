from hundredways.ways import Way, WaysRegistry, build_registry


def test_at_least_200_ways():
    """The name 200Ways is a promise: the registry must not shrink."""
    registry = build_registry()
    assert registry.count >= 200


def test_unique_way_ids():
    registry = build_registry()
    ids = [w.way_id for w in registry.all()]
    assert len(ids) == len(set(ids))


def test_ten_categories_each_with_twenty():
    registry = build_registry()
    assert len(registry.categories()) == 10
    for category in registry.categories():
        assert len(registry.by_category(category)) == 20


def test_defaults_one_per_category():
    registry = build_registry()
    defaults = registry.defaults()
    assert len(defaults) == 10
    for category, way_id in defaults.items():
        assert registry.get(way_id) is not None
        assert registry.get(way_id).category == category
        assert registry.get(way_id).default


def test_get_known_way():
    registry = build_registry()
    way = registry.get("brand.token-regex")
    assert way is not None
    assert way.name == "Anchored token regex"
    assert "word-boundary" in way.description


def test_get_unknown_way():
    assert build_registry().get("nope.nope") is None


def test_way_dataclass_shape():
    way = Way("detect.poll", "Polling loop", "detect", "poll", default=True)
    assert way.way_id == "detect.poll"
    assert way.category == "detect"
