from researcher.ai import Finding
from researcher.pipeline import normalize, useful


def test_normalize_removes_email_and_markup():
    assert normalize("<b>Help</b>  me@example.com") == "Help [email]"


def test_short_text_is_rejected():
    assert not useful("Help")
    assert useful("I have to copy each customer request manually into a spreadsheet every single day")


def test_confidence_is_bounded():
    try:
        Finding(contains_pain=True, confidence=1.5)
    except ValueError:
        pass
    else:
        raise AssertionError("Confidence above one must be rejected")

