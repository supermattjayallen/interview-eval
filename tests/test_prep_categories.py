from app.prep_categories import PrepQuestionCategory, normalize_prep_category


def test_normalize_prep_category_aliases():
    assert normalize_prep_category("role-specific") == PrepQuestionCategory.ROLE_SPECIFIC
    assert normalize_prep_category("culture fit") == PrepQuestionCategory.CULTURE
    assert normalize_prep_category("logistics") == PrepQuestionCategory.LOGISTICS


def test_normalize_prep_category_unknown_maps_to_other():
    assert normalize_prep_category("something odd") == PrepQuestionCategory.OTHER
