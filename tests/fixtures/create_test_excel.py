from pathlib import Path

from openpyxl import Workbook


def create_test_excel(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(
        [
            "用户输入的问题",
            "期望的回复",
            "关联的政策",
            "关联的文档",
            "申报入口",
            "是否多意图",
            "知识类型",
            "问题是否违禁",
            "备注",
        ]
    )
    ws.append(
        [
            "最低要求是多少？",
            "500万元。",
            "https://policy.example.com",
            "https://doc.example.com",
            "申报指南.docx",
            "单意图",
            "文档",
            "正常",
            "",
        ]
    )
    ws.append(
        [
            "不是600万元吗？",
            "不是，是500万元。",
            "https://policy.example.com",
            "https://doc.example.com",
            "申报指南.docx",
            "单意图",
            "结合上下文",
            "正常",
            "",
        ]
    )
    ws.append(
        [
            "知识产权要求？",
            "至少3件发明专利。",
            "https://policy.example.com",
            "https://doc.example.com",
            "申报指南.docx",
            "单意图",
            "文档",
            "正常",
            "",
        ]
    )
    ws.append(
        [
            "只有2件可以吗？",
            "不符合要求，最低3件。",
            "https://policy.example.com",
            "https://doc.example.com",
            "申报指南.docx",
            "单意图",
            "结合上下文",
            "正常",
            "",
        ]
    )
    wb.save(path)


if __name__ == "__main__":
    create_test_excel(Path("tests/fixtures/test_questions.xlsx"))
