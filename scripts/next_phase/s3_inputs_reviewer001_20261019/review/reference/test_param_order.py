import pytest

@pytest.fixture(params=[None, "1"], ids=["no_color_absent", "no_color_set"])
def no_color_context(request):
    return request.param

@pytest.mark.parametrize("usage", ["default", "markup"], ids=["default", "markup"])
def test_user_usage(usage, no_color_context):
    pass

@pytest.mark.parametrize("formatter", [pytest.param(None,id="indented"),pytest.param(None,id="indented-desc"),pytest.param(None,id="indented-long"),pytest.param(None,id="titled")])
@pytest.mark.parametrize("columns", ["40", "100"], ids=["columns_40", "columns_100"])
def test_generated_usage(formatter, columns):
    pass
