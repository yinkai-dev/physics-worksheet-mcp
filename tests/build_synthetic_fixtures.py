"""Rebuild public synthetic inputs. No private paper or OCR material is used."""
import copy
import hashlib
import json
from pathlib import Path


def make_spec(name, blocks, *, choices=None):
    excerpt = "【合成测试，非真实试卷】\n" + "".join(block["value"] for block in blocks)
    if choices:
        excerpt += "\n" + "\n".join(choice if isinstance(choice, str) else "".join(block["value"] for block in choice)
                                    for choice in choices)
    return {"schema_version": "1.0", "document": {"title": f"合成物理练习：{name}", "subtitle": "仅用于工程排版验收", "include_answers": False},
            "questions": [{"id": "q1", "body": blocks, "choices": choices or [], "points": None,
                           "source": {"kb_name": "合成测试知识库", "title": f"synthetic-{name}.md", "page": None,
                                      "chunk_id": f"synthetic-{name}-1", "score": None, "source_excerpt": excerpt,
                                      "content_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest()},
                           "review_status": "retrieved-complete"}]}


def main():
    text = lambda value: {"type": "text", "value": value}
    formula = lambda value: {"type": "math", "value": value}
    cases = {
        "01-chinese": make_spec("纯中文", [text("一辆小车沿平直轨道做匀速直线运动。请说明小车所受合力的大小，并写出判断依据。")]),
        "02-formulas": make_spec("上下标与分式", [text("已知物体初速度为"), formula(r"v_0"), text("，末速度为"), formula(r"v_1"),
                                                  text("，运动时间为"), formula(r"\Delta t"), text("。利用公式"),
                                                  formula(r"a=\frac{v_1-v_0}{\Delta t}"), text("求加速度，再比较"),
                                                  formula(r"v=\sqrt{v_0^2+2as}"), text("中各物理量的含义。")]),
        "03-choices": make_spec("选择题", [text("关于惯性，下列说法正确的是（　）。")], choices=["A. 质量越大，惯性越大", "B. 速度越大，惯性越大", "C. 物体不受力时才有惯性", "D. 物体静止时没有惯性"]),
        "04-calculation": make_spec("计算题", [text("质量为"), formula(r"m=2\,\mathrm{kg}"), text("的物体从静止开始，在水平恒力作用下运动。已知加速度为"),
                                                    formula(r"a=3\,\mathrm{m/s^2}"), text("，不计阻力。\n（1）求恒力大小。\n（2）求运动两秒后的速度。")]),
        "05-units": make_spec("单位", [text("请写出磁感应强度、电压和电流的国际单位，并核对表达式"), formula(r"E=BLv"), text("的量纲。可用单位符号为"),
                                         formula(r"\mathrm{T},\ \mathrm{V},\ \mathrm{A},\ \mathrm{m/s}"), text("。")],
                              choices=[[text("A. 电场强度："), formula(r"\mathrm{N/C}")],
                                       [text("B. 加速度："), formula(r"\mathrm{m\,s^{-2}}")]]),
        "06-pagination": make_spec("分页", [text("一列物体依次通过观察点，实验者记录位置和时间。请根据所给条件逐项说明分析步骤。\n" * 12)]),
    }
    pagination = cases["06-pagination"]
    for index in range(2, 8):
        question = copy.deepcopy(pagination["questions"][0])
        question["id"] = f"q{index}"
        question["source"]["chunk_id"] = f"synthetic-pagination-{index}"
        pagination["questions"].append(question)
    combined = copy.deepcopy(cases["01-chinese"])
    combined["document"]["title"] = "中文物理排版综合验收（合成）"
    combined["questions"] = []
    for name, case in cases.items():
        question = copy.deepcopy(case["questions"][0])
        question["id"] = f"q{len(combined['questions']) + 1}"
        combined["questions"].append(question)
    cases["all-six"] = combined
    incomplete = copy.deepcopy(cases["03-choices"])
    incomplete["questions"][0]["review_status"] = "needs-review"
    cases["fail-incomplete"] = incomplete
    missing = copy.deepcopy(cases["01-chinese"])
    del missing["questions"][0]["source"]
    cases["fail-no-source"] = missing
    unsafe = copy.deepcopy(cases["02-formulas"])
    unsafe["questions"][0]["body"] = [formula(r"\input{/etc/passwd}")]
    cases["fail-dangerous-latex"] = unsafe
    diagram = copy.deepcopy(cases["01-chinese"])
    diagram["questions"][0]["body"].append({"type": "image", "path": "../../private.png"})
    cases["fail-image"] = diagram
    root = Path(__file__).parent / "fixtures"
    root.mkdir(exist_ok=True)
    for name, case in cases.items():
        (root / f"{name}.json").write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {len(cases)} synthetic fixtures")


if __name__ == "__main__":
    main()
