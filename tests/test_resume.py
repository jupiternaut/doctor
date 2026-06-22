from __future__ import annotations

from agent_context.resume import parse_resume_text, redact_resume_contact_info, render_resume_markdown


def test_parse_resume_text_extracts_role_technologies_and_projects() -> None:
    resume = parse_resume_text(
        "\n".join(
            [
                "求职意向：AI 应用实习生",
                "教育背景 南昌交通学院 智能科学与技术 本科",
                "专业技能 Python FastAPI LangChain FAISS Sentence-Transformers Docker Compose",
                "项目二：双模式 RAG 智能问答系统",
                "项目三：OpenClaw 智能助手私有化部署与飞书机器人集成",
            ]
        )
    )

    assert resume["target_role"] == "AI 应用实习生"
    assert {"Python", "FastAPI", "LangChain", "FAISS", "Docker Compose"} <= set(resume["technologies"])
    assert any("双模式 RAG" in line for line in resume["projects"])
    assert any("南昌交通学院" in line for line in resume["education"])

    markdown = render_resume_markdown({"provider": "doctor_resume_ocr", "ocr": [], **resume})

    assert "AI 应用实习生" in markdown
    assert "Sentence-Transformers" in markdown


def test_redact_resume_contact_info_removes_email_and_phone() -> None:
    text = "\n".join(
        [
            "电子邮箱：31512377325@qq.com",
            "联系电话：15570011267",
            "项目二：双模式 RAG 智能问答系统",
        ]
    )

    redacted = redact_resume_contact_info(text)

    assert "31512377325@qq.com" not in redacted
    assert "15570011267" not in redacted
    assert "[REDACTED_CONTACT]" in redacted
    assert "双模式 RAG" in redacted
