import pytest

from cardgen.engine.url_fetcher import extract_product_text


WB_HTML = """\
<!DOCTYPE html>
<html>
<head>
    <title>Wildberries</title>
</head>
<body>
    <script id="__NEXT_DATA__" type="application/json">
    {
        "props": {
            "pageProps": {
                "product": {
                    "name": "Толстовка мужская оверсайз",
                    "description": "Толстовка из футера с начёсом. Плотность 350 г/м².",
                    "characteristics": [
                        {"name": "Цвет", "value": "чёрный"},
                        {"name": "Размер", "value": "M-XXL"},
                        {"name": "Состав", "value": "80% хлопок, 20% полиэстер"}
                    ],
                    "brand": "StreetStyle"
                }
            }
        }
    }
    </script>
</body>
</html>
"""


def test_extract_wb_returns_name():
    result = extract_product_text(WB_HTML, "wb")
    assert "Толстовка мужская оверсайз" in result


def test_extract_wb_returns_characteristics():
    result = extract_product_text(WB_HTML, "wb")
    assert "Цвет: чёрный" in result
    assert "Размер: M-XXL" in result


def test_extract_wb_returns_brand():
    result = extract_product_text(WB_HTML, "wb")
    assert "Бренд: StreetStyle" in result


def test_extract_wb_not_empty():
    result = extract_product_text(WB_HTML, "wb")
    assert len(result) > 0


OZON_HTML = """\
<!DOCTYPE html>
<html>
<head>
    <title>Ozon</title>
</head>
<body>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Кроссовки Nike Air Max",
        "description": "Лёгкие беговые кроссовки с амортизацией Air."
    }
    </script>
</body>
</html>
"""


def test_extract_ozon_returns_name():
    result = extract_product_text(OZON_HTML, "ozon")
    assert "Кроссовки Nike Air Max" in result


def test_extract_ozon_returns_description():
    result = extract_product_text(OZON_HTML, "ozon")
    assert "амортизацией Air" in result


def test_extract_ozon_not_empty():
    result = extract_product_text(OZON_HTML, "ozon")
    assert len(result) > 0


EMPTY_HTML = "<html><body></body></html>"


def test_empty_html_returns_empty():
    result = extract_product_text(EMPTY_HTML, "wb")
    assert result == ""


NO_NEXT_DATA_HTML = "<html><body><script>var x = 1;</script></body></html>"


def test_no_next_data_returns_empty():
    result = extract_product_text(NO_NEXT_DATA_HTML, "wb")
    assert result == ""


INVALID_JSON_HTML = """\
<html>
<script id="__NEXT_DATA__">{broken json !!!}</script>
</html>
"""


def test_invalid_json_returns_empty():
    result = extract_product_text(INVALID_JSON_HTML, "wb")
    assert result == ""


def test_unknown_platform_returns_empty():
    result = extract_product_text(WB_HTML, "unknown")
    assert result == ""


def test_wb_truncates_to_max_length():
    long_desc = "X" * 3500
    html = f"""\
<html>
<script id="__NEXT_DATA__">{{"props":{{"pageProps":{{"product":{{"name":"A","description":"{long_desc}"}}}}}}}}</script>
</html>
"""
    result = extract_product_text(html, "wb")
    assert len(result) <= 3000
