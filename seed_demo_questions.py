"""给题库塞几条演示题（便于验证排序/试题篮/错题集），source_title 标了「演示数据」。"""
import hashlib
import io
import sys

sys.path.insert(0, r"C:\Users\Administrator\Documents\tiku-studio\apps\api")

from app.db import SessionLocal  # noqa: E402
from app.models import Book, Question  # noqa: E402

DEMO = [
    ("298.15 K 时，反应 $\\mathrm{N_2(g)+3H_2(g)\\rightleftharpoons 2NH_3(g)}$ 的 $\\Delta_r H_m^\\ominus=-92.2\\ \\mathrm{kJ\\cdot mol^{-1}}$。求该温度下的 $\\Delta_r U_m^\\ominus$。", "计算", 3,
     ["热力学与化学平衡"], "$\\Delta_r U_m^\\ominus=\\Delta_r H_m^\\ominus-\\Delta n RT$，$\\Delta n=-2$，故 $\\Delta_r U_m^\\ominus=-92.2+2\\times8.314\\times298.15/1000\\approx-87.2\\ \\mathrm{kJ\\cdot mol^{-1}}$"),
    ("下列哪种粒子的物质的量为 1 mol 时，其质量约为 18 g？", "选择", 1,
     ["分析化学"], "C（H2O 的摩尔质量 18 g/mol）", ["A. H2", "B. O2", "C. H2O", "D. CO2"]),
    ("写出苯的硝化反应方程式，并说明反应条件。", "简答", 2,
     ["有机化学"], "$\\mathrm{C_6H_6+HNO_3\\xrightarrow[50\\sim60^\\circ C]{H_2SO_4} C_6H_5NO_2+H_2O}$；浓硫酸催化、水浴控温"),
    ("判断：所有二阶线性偏微分方程都可以化为标准型。", "判断", 2,
     ["动力学"], "错。只有常系数或可对角化的情形才能化为标准型。"),
    ("用 0.1000 mol/L NaOH 滴定 20.00 mL 0.1000 mol/L HCl，化学计量点的 pH 为多少？", "选择", 1,
     ["分析化学"], "B（强酸强碱，计量点 pH=7.00）", ["A. 4.30", "B. 7.00", "C. 8.70", "D. 10.00"]),
    ("一级反应的半衰期与初始浓度的关系是？", "选择", 2,
     ["动力学"], "C（一级反应 $t_{1/2}=\\ln 2/k$，与初始浓度无关）", ["A. 成正比", "B. 成反比", "C. 无关", "D. 成平方关系"]),
]


def main() -> None:
    db = SessionLocal()
    try:
        book = db.query(Book).order_by(Book.id).first()
        if not book:
            print("没有书册，先建一本")
            return
        made = 0
        for row in DEMO:
            stem, qtype, diff, kps, answer = row[:5]
            options = row[5] if len(row) > 5 else []
            h = hashlib.md5((stem + answer).encode("utf-8")).hexdigest()[:32]
            if db.query(Question).filter_by(book_id=book.id, content_hash=h).first():
                continue
            db.add(Question(book_id=book.id, content_hash=h, stem=stem, options=options,
                            answer=answer, analysis="", q_type=qtype, difficulty=diff,
                            knowledge_points=kps, images=[], tags=kps[:2],
                            source_title="演示数据（可删）", status="confirmed",
                            scope_status="ok", sort_order=100 + made))
            made += 1
        db.commit()
        total = db.query(Question).count()
        print(f"新增 {made} 道，题库共 {total} 道（book #{book.id} {book.title}）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
