"""DSML 泄漏补丁。解析器修好后，这个文件和 deepseek_dsml.py 一起删。"""
from services.worker.deepseek_dsml import recover_dsml

DECLARED = {"load_skill", "fetch_url", "web_search"}

LEAK = (
    '<｜DSML｜ calls> <｜DSML｜ invoke name="load_skill"> '
    '<｜DSML｜ parameter name="name" string="true">filings</｜DSML｜ parameter> '
    '</｜DSML｜ invoke> </｜DSML｜ calls>'
)

WRAPPED = (
    '<｜DSML｜tool_calls>'
    '<｜DSML｜invoke name="fetch_url">'
    '<｜DSML｜parameter name="url" string="true">https://ir.tesla.com/</｜DSML｜parameter>'
    '</｜DSML｜invoke>'
    '</｜DSML｜tool_calls>'
)


def test_leaked_invoke_without_tool_calls_wrapper():
    text, calls = recover_dsml(LEAK, DECLARED)
    assert calls[0].name == "load_skill"
    assert calls[0].arguments == {"name": "filings"}
    assert "DSML" not in text
    assert "load_skill" not in text


def test_wrapped_call_and_json_parameter():
    raw = (
        WRAPPED.replace(
            '<｜DSML｜parameter name="url" string="true">https://ir.tesla.com/</｜DSML｜parameter>',
            '<｜DSML｜parameter name="count" string="false">5</｜DSML｜parameter>',
        )
    )
    _, calls = recover_dsml(raw, {"fetch_url"})
    assert calls[0].name == "fetch_url"
    assert calls[0].arguments == {"count": 5}


def test_undeclared_tool_stays_in_the_text():
    text, calls = recover_dsml(LEAK, {"web_search"})
    assert calls == []
    assert "load_skill" in text


def test_doubled_bars_still_parse():
    leaked = LEAK.replace("｜", "｜｜")
    text, calls = recover_dsml(leaked, DECLARED)
    assert calls[0].name == "load_skill"
    assert calls[0].arguments == {"name": "filings"}
    assert "DSML" not in text


def test_prose_around_a_call_is_kept():
    text, calls = recover_dsml("查一下。\n" + WRAPPED + "\n以上。", DECLARED)
    assert calls[0].arguments["url"] == "https://ir.tesla.com/"
    assert "查一下。" in text
    assert "以上。" in text
    assert "DSML" not in text
