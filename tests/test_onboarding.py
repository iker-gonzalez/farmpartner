

def test_crop_example_uses_the_countrys_main_crops():
    from app.onboarding import crop_example
    dr = {"country": {"top": [{"item": "Rice"}, {"item": "Cocoa beans"}, {"item": "Coffee, green"}]}}
    assert crop_example(dr) == "rice, cocoa beans"
    assert crop_example({}) == crop_example(None) == "wheat, corn"  # U.S. fields / no FAOSTAT data
