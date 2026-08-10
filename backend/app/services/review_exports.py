"""Review export formats shared by editors, clients, and the constrained editing Agent."""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.entities import ReviewComment, Timeline


def frame_to_timecode(frame_number: int, frame_rate: float) -> str:
    fps = max(1, round(frame_rate))
    hours, remainder = divmod(max(0, frame_number), fps * 3600)
    minutes, remainder = divmod(remainder, fps * 60)
    seconds, frames = divmod(remainder, fps)
    return f"{hours:02}:{minutes:02}:{seconds:02}:{frames:02}"


def _ai_markers(timeline: Timeline) -> list[dict[str, Any]]:
    document = dict((timeline.settings_json or {}).get("confirmed_timeline", {}))
    tracks = document.get("tracks", []) if isinstance(document, dict) else []
    markers: list[dict[str, Any]] = []
    if tracks:
        for track in tracks:
            for clip in track.get("clips", []):
                markers.append({
                    "kind": "ai_marker", "track": track.get("type"), "time_seconds": clip.get("timeline_start", clip.get("source_start", 0)),
                    "source_start": clip.get("source_start"), "source_end": clip.get("source_end"),
                    "action": clip.get("action", "keep"), "reason": clip.get("reason", ""),
                    "confidence_score": clip.get("confidence_score"), "annotation": {},
                })
    else:
        for segment in document.get("segments", []):
            markers.append({
                "kind": "ai_marker", "track": "main_video", "time_seconds": segment.get("source_start", 0),
                "source_start": segment.get("source_start"), "source_end": segment.get("source_end"),
                "action": segment.get("action", "keep"), "reason": segment.get("reason", ""),
                "confidence_score": segment.get("confidence_score"), "annotation": {},
            })
    return markers


def review_rows(timeline: Timeline, comments: Iterable[ReviewComment]) -> list[dict[str, Any]]:
    rows = [
        {
            "kind": "comment", "id": str(comment.id), "status": comment.status.value,
            "time_seconds": float(comment.time_seconds), "timecode": frame_to_timecode(comment.frame_number, float(comment.frame_rate)),
            "frame_number": comment.frame_number, "frame_rate": float(comment.frame_rate), "author": comment.author.display_name or comment.author.email,
            "body": comment.body, "annotation": comment.annotation_json, "track": "", "source_start": "", "source_end": "",
            "action": "", "reason": "", "confidence_score": "",
        }
        for comment in comments
    ]
    for marker in _ai_markers(timeline):
        time_seconds = float(marker["time_seconds"] or 0)
        rows.append({
            "id": "", "status": "", "timecode": "", "frame_number": "", "frame_rate": "", "author": "AI rough-cut",
            "body": "", **marker, "time_seconds": time_seconds,
        })
    return sorted(rows, key=lambda item: float(item.get("time_seconds") or 0))


def build_review_csv(rows: list[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    fields = ["kind", "id", "status", "time_seconds", "timecode", "frame_number", "frame_rate", "author", "body", "track", "source_start", "source_end", "action", "reason", "confidence_score", "annotation"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({**row, "annotation": json.dumps(row.get("annotation", {}), ensure_ascii=False, separators=(",", ":"))})
    return output.getvalue().encode("utf-8-sig")


def build_review_pdf(timeline_name: str, rows: list[dict[str, Any]]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    output = io.BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, rightMargin=14 * mm, leftMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("ReviewTitle", parent=styles["Title"], fontName="STSong-Light", fontSize=16, leading=22, alignment=TA_LEFT)
    body = ParagraphStyle("ReviewBody", parent=styles["BodyText"], fontName="STSong-Light", fontSize=8, leading=11)
    latin_body = ParagraphStyle("ReviewLatinBody", parent=body, fontName="Helvetica")
    def paragraph(text: Any, style: ParagraphStyle = body) -> Paragraph:
        safe = str(text).replace("<br/>", "__REVIEW_BREAK__").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("__REVIEW_BREAK__", "<br/>")
        return Paragraph(safe, latin_body if safe.isascii() else style)
    story: list[Any] = [Paragraph(f"審閱修改清單 - {timeline_name}", title), Spacer(1, 5 * mm)]
    table_rows: list[list[Any]] = [[paragraph("時間碼"), paragraph("類型/狀態"), paragraph("提出者"), paragraph("內容／AI 理由")]]
    for row in rows:
        kind = "人工批註" if row["kind"] == "comment" else f"AI {row.get('action', '')}"
        content = row.get("body") or row.get("reason") or "-"
        table_rows.append([
            paragraph(row.get("timecode") or f"{float(row['time_seconds']):.3f}s"),
            paragraph(f"{kind}<br/>{row.get('status') or ''}"),
            paragraph(row.get("author") or ""),
            paragraph(content),
        ])
    table = Table(table_rows, colWidths=[28 * mm, 30 * mm, 32 * mm, 82 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d1d5db")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    document.build(story)
    return output.getvalue()
