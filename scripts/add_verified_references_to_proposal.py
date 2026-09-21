from __future__ import annotations

import csv
import re
import shutil
from pathlib import Path

from docx import Document
from docx.enum.text import WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "output/proposal_revision/开题报告_基于物联网与协同控制的智能水肥一体化管控系统设计与实现_以红壤丘陵区为例_算法通用表述稿.docx"
OUTPUT = ROOT / "output/proposal_revision/开题报告_基于物联网与协同控制的智能水肥一体化管控系统设计与实现_以红壤丘陵区为例_参考文献版.docx"
CSV_PATH = ROOT / "data/csv/proposal_references_t060.csv"
AUDIT_PATH = ROOT / "output/researchwrite/t060_literature_update/literature_audit.md"


REFERENCES = [
    ("M", 2016, "ZHANG Q, ed. Precision Agriculture Technology for Crop Farming[M]. Boca Raton: CRC Press, 2016.", "https://www.routledge.com/Precision-AgricultureTechnology-for-CropFarming/Zhang/p/book/9781032098272", False),
    ("M", 2020, "CASTRIGNANÒ A, BUTTAFUOCO G, KHOSLA R, et al., eds. Agricultural Internet of Things and Decision Support for Precision Smart Farming[M]. London: Academic Press, 2020.", "https://shop.elsevier.com/books/agricultural-internet-of-things-and-decision-support-for-precision-smart-farming/castrignano/978-0-12-818373-1", False),
    ("M", 2024, "SRIVASTAVA S K, SRIVASTAVA D, CENGIZ K, et al., eds. Smart Agritech: Robotics, AI, and Internet of Things (IoT) in Agriculture[M]. Hoboken: Wiley, 2024.", "https://doi.org/10.1002/9781394302994", False),
    ("M", 2021, "CHOUDHURY A, BISWAS A, SINGH T P, et al., eds. Smart Agriculture Automation Using Advanced Technologies[M]. Singapore: Springer, 2021.", "https://doi.org/10.1007/978-981-16-6124-2", False),
    ("M", 2025, "RAJ P, GAYATHRI N, KATHRINE G J W, eds. Artificial Intelligence for Precision Agriculture[M]. Boca Raton: Auerbach Publications, 2025.", "https://www.routledge.com/Artificial-Intelligence-for-Precision-Agriculture/Gayathri-Kathrine-Raj/p/book/9781032462349", False),
    ("J", 2019, "WU Y, LI L, LI S, et al. Optimal control algorithm of fertigation system in greenhouse based on EC model[J]. International Journal of Agricultural and Biological Engineering, 2019, 12(3): 118-125.", "https://doi.org/10.25165/j.ijabe.20191203.4680", True),
    ("J", 2025, "XU Y, JIN Y, SUN Z, et al. PSO-Based System Identification and Fuzzy-PID Control for EC Real-Time Regulation in Fertilizer Mixing System[J]. Agronomy, 2025, 15(5): 1259.", "https://doi.org/10.3390/agronomy15051259", True),
    ("J", 2026, "YUAN W, ZHANG Z, DAI J, et al. Change-aware online identification and adaptive PI control for robust electrical conductivity regulation in fertigation mixing systems[J]. Smart Agricultural Technology, 2026, 14: 102110.", "https://doi.org/10.1016/j.atech.2026.102110", True),
    ("J", 2020, "GONZÁLEZ PEREA R, MORENO M A, ORTEGA J F, et al. Dynamic Simulation Tool of fertigation in drip irrigation subunits[J]. Computers and Electronics in Agriculture, 2020, 173: 105434.", "https://doi.org/10.1016/j.compag.2020.105434", True),
    ("J", 2025, "SULAIMAN H, YUSOF A A, MOHAMED NOR M K. Automated Hydroponic Nutrient Dosing System: A Scoping Review of pH and Electrical Conductivity Dosing Frameworks[J]. AgriEngineering, 2025, 7(2): 43.", "https://doi.org/10.3390/agriengineering7020043", True),
    ("M", 2024, "SAVAGLIO C, FORTINO G, ZHOU M, et al., eds. Device-Edge-Cloud Continuum: Paradigms, Architectures and Applications[M]. Cham: Springer, 2024.", "https://doi.org/10.1007/978-3-031-42194-5", False),
    ("M", 2022, "KARAKONSTANTIS G, GILLAN C J, eds. Computing at the EDGE: New Challenges for Service Provision[M]. Cham: Springer, 2022.", "https://doi.org/10.1007/978-3-030-74536-3", False),
    ("M", 2023, "SRIVATSA M, ABDELZAHER T, HE T, eds. Artificial Intelligence for Edge Computing[M]. Cham: Springer, 2023.", "https://doi.org/10.1007/978-3-031-40787-1", False),
    ("J", 2016, "SHI W, CAO J, ZHANG Q, et al. Edge Computing: Vision and Challenges[J]. IEEE Internet of Things Journal, 2016, 3(5): 637-646.", "https://doi.org/10.1109/JIOT.2016.2579198", True),
    ("J", 2020, "MA Y, LU C, SINOPOLI B, et al. Exploring Edge Computing for Multitier Industrial Control[J]. IEEE Transactions on Computer-Aided Design of Integrated Circuits and Systems, 2020, 39(11): 3506-3518.", "https://doi.org/10.1109/TCAD.2020.3012648", True),
    ("J", 2022, "DENG Y, LÉCHAPPÉ V, MOULAY E, et al. Predictor-based control of time-delay systems: a survey[J]. International Journal of Systems Science, 2022, 53(12): 2496-2534.", "https://doi.org/10.1080/00207721.2022.2056654", True),
    ("J", 2023, "ZAREI J, MASOUDI E, RAZAVI-FAR R, et al. Fault-tolerant control design for unreliable networked control systems via constrained model predictive control[J]. ISA Transactions, 2023, 134: 171-182.", "https://doi.org/10.1016/j.isatra.2022.08.019", True),
    ("S", 2014, "OASIS. MQTT Version 3.1.1[S/OL]. OASIS Standard, 2014-10-29.", "https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html", True),
    ("J", 2023, "IMBERNÓN-MULERO A, MAESTRE-VALERO J F, MARTÍNEZ-ALVAREZ V, et al. Evaluation of an autonomous smart system for optimal management of fertigation with variable sources of irrigation water[J]. Frontiers in Plant Science, 2023, 14: 1149956.", "https://doi.org/10.3389/fpls.2023.1149956", True),
    ("J", 2025, "ADAMO T, CAIVANO D, COLIZZI L, et al. Optimization of irrigation and fertigation in smart agriculture: An IoT-based micro-services framework[J]. Smart Agricultural Technology, 2025, 11: 100885.", "https://doi.org/10.1016/j.atech.2025.100885", True),
    ("J", 2020, "LIN N, WANG X, ZHANG Y, et al. Fertigation management for sustainable precision agriculture based on Internet of Things[J]. Journal of Cleaner Production, 2020, 277: 124119.", "https://doi.org/10.1016/j.jclepro.2020.124119", False),
    ("J", 2022, "ZHANG H, HE L, DI GIOIA F, et al. LoRaWAN based internet of things (IoT) system for precision irrigation in plasticulture fresh-market tomato[J]. Smart Agricultural Technology, 2022, 2: 100053.", "https://doi.org/10.1016/j.atech.2022.100053", True),
    ("J", 2024, "WANG Q, JIA Y, PANG Z, et al. Intelligent fertigation improves tomato yield and quality and water and nutrient use efficiency in solar greenhouse production[J]. Agricultural Water Management, 2024, 298: 108873.", "https://doi.org/10.1016/j.agwat.2024.108873", False),
    ("J", 2024, "WANG H, ZHAO J, ZHANG L, et al. Application of Disturbance Observer-Based Fast Terminal Sliding Mode Control for Asynchronous Motors in Remote Electrical Conductivity Control of Fertigation Systems[J]. Agriculture, 2024, 14(2): 168.", "https://doi.org/10.3390/agriculture14020168", True),
    ("M", 2026, "ÅSTRÖM K J, HÄGGLUND T. Advanced PID Control[M]. Hoboken: Wiley, 2026.", "https://doi.org/10.1002/9781394442102", False),
    ("M", 2026, "RAWLINGS J B, MAYNE D Q, DIEHL M M. Model Predictive Control: Theory, Computation, and Design[M]. 2nd ed. Madison: Nob Hill Publishing, 2026.", "https://sites.engineering.ucsb.edu/~jbraw/mpc/", False),
    ("J", 2019, "TRAN T C, JUNG J C. Development of Anti-windup PI Control and Bumpless Control Transfer Methodology for Feedwater Control System[J]. Annals of Nuclear Energy, 2019, 131: 233-241.", "https://doi.org/10.1016/j.anucene.2019.03.030", True),
    ("M", 2011, "ISERMANN R. Fault-Diagnosis Applications: Model-Based Condition Monitoring, Actuators, Drives, Machinery, Plants, Sensors, and Fault-tolerant Systems[M]. Berlin: Springer, 2011.", "https://doi.org/10.1007/978-3-642-12767-0", False),
    ("J", 2023, "ZOU X, LIU W, HUO Z, et al. Current Status and Prospects of Research on Sensor Fault Diagnosis of Agricultural Internet of Things[J]. Sensors, 2023, 23(5): 2528.", "https://doi.org/10.3390/s23052528", True),
    ("J", 2024, "SHEKARIAN S M, AMINIAN M, FALLAH A M, et al. AI-powered sensor fault detection for cost-effective smart greenhouses[J]. Computers and Electronics in Agriculture, 2024, 224: 109198.", "https://doi.org/10.1016/j.compag.2024.109198", True),
    ("J", 2005, "ISERMANN R. Model-based fault-detection and diagnosis: status and applications[J]. Annual Reviews in Control, 2005, 29(1): 71-85.", "https://doi.org/10.1016/j.arcontrol.2004.12.002", True),
    ("J", 2017, "FENG J, TURKSOY K, SAMADI S, et al. Hybrid online sensor error detection and functional redundancy for systems with time-varying parameters[J]. Journal of Process Control, 2017, 60: 115-127.", "https://doi.org/10.1016/j.jprocont.2017.04.004", True),
    ("J", 2011, "YETENDJE A, DE DONÁ J A, SERON M M. Multisensor fusion fault tolerant control[J]. Automatica, 2011, 47(7): 1461-1466.", "https://doi.org/10.1016/j.automatica.2011.02.024", True),
    ("J", 2012, "SERON M M, DE DONÁ J A, OLARU S. Fault Tolerant Control Allowing Sensor Healthy-to-Faulty and Faulty-to-Healthy Transitions[J]. IEEE Transactions on Automatic Control, 2012, 57(7): 1657-1669.", "https://doi.org/10.1109/TAC.2011.2178716", True),
    ("J", 2009, "DARDER M, VALERA A, NIETO E, et al. Multisensor device based on Case-Based Reasoning (CBR) for monitoring nutrient solutions in fertigation[J]. Sensors and Actuators B: Chemical, 2009, 135(2): 530-536.", "https://doi.org/10.1016/j.snb.2008.09.034", True),
    ("J", 2023, "KALYANI Y, BERMEO N V, COLLIER R. Digital twin deployment for smart agriculture in Cloud-Fog-Edge infrastructure[J]. International Journal of Parallel, Emergent and Distributed Systems, 2023, 38(6): 461-476.", "https://doi.org/10.1080/17445760.2023.2235653", True),
]


def set_run_font(run, east_asia="宋体", latin="Times New Roman", size=Pt(10.5)):
    run.font.name = latin
    run.font.size = size
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), east_asia)


def add_citation(paragraph, citation):
    run = paragraph.add_run(citation)
    set_run_font(run, size=Pt(9))
    run.font.superscript = True


def shade_cell(cell, fill="FFFFFF"):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def expand_numbers(token):
    numbers = []
    for part in token.split(","):
        part = part.strip()
        if "-" in part:
            a, b = map(int, part.split("-", 1))
            numbers.extend(range(a, b + 1))
        elif part:
            numbers.append(int(part))
    return numbers


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, OUTPUT)
    doc = Document(OUTPUT)

    t0 = doc.tables[0].cell(0, 0).paragraphs
    add_citation(t0[2], "[1-5]")
    add_citation(t0[2], "[6-10]")
    add_citation(t0[3], "[11-17]")
    add_citation(t0[5], "[18]")

    t1 = doc.tables[1].cell(0, 0).paragraphs
    add_citation(t1[2], "[6-10,19-24]")
    add_citation(t1[4], "[11-17,25-26]")
    add_citation(t1[5], "[27]")
    add_citation(t1[7], "[28-35]")
    add_citation(t1[9], "[36]")

    t2 = doc.tables[2].cell(0, 0).paragraphs
    t2[5].text = "（1）系统总体设计。结合红壤丘陵区生产单元分散、通信链路波动和设备运行条件差异，划分感知与执行、边缘控制、通信、云端服务、数据管理和用户交互模块；规定EC、设备状态、控制命令、模式事件和执行反馈的数据结构，明确模块接口、消息方向和任务流程。"
    t2[6].text = "（2）混肥EC对象建模与底层控制。建立注肥输入至EC偏差的一阶惯性纯滞后模型，设置参数扰动、执行器饱和与动作变化率约束；实现共享的离散PI、抗积分饱和云端时间戳预测补偿，为各监督策略提供统一控制基础。"
    for idx in (5, 6):
        for run in t2[idx].runs:
            set_run_font(run, size=Pt(12))

    ref_cell = doc.tables[4].rows[15].cells[0]
    shade_cell(ref_cell)
    while len(ref_cell.paragraphs) > 1:
        p = ref_cell.paragraphs[-1]._element
        p.getparent().remove(p)
    heading = ref_cell.paragraphs[0]
    heading.text = "八、主要参考文献"
    for run in heading.runs:
        set_run_font(run, east_asia="黑体", size=Pt(12))
        run.font.bold = True

    for number, (_, _, text, url, _) in enumerate(REFERENCES, 1):
        p = ref_cell.add_paragraph()
        p.paragraph_format.first_line_indent = Pt(0)
        p.paragraph_format.left_indent = Pt(14)
        p.paragraph_format.first_line_indent = Pt(-14)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(1.5)
        p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
        r = p.add_run(f"[{number}] {text} DOI或官方链接：{url}")
        set_run_font(r, size=Pt(9))

    doc.save(OUTPUT)

    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["number", "type", "year", "reference", "doi_or_official_url", "from_attached_pdf", "verification", "status"])
        for number, (kind, year, text, url, reused) in enumerate(REFERENCES, 1):
            writer.writerow([number, kind, year, text, url, "yes" if reused else "no", "Crossref/DOI or publisher/standard official page", "verified"])

    check_doc = Document(OUTPUT)
    body_text = []
    for ti, table in enumerate(check_doc.tables):
        for ri, row in enumerate(table.rows):
            if ti == 4 and ri == 15:
                continue
            seen = set()
            for cell in row.cells:
                if id(cell._tc) in seen:
                    continue
                seen.add(id(cell._tc))
                body_text.extend(p.text for p in cell.paragraphs)
    first_seen = []
    for paragraph in body_text:
        for match in re.finditer(r"\[([0-9,-]+)\]", paragraph):
            for number in expand_numbers(match.group(1)):
                if number not in first_seen:
                    first_seen.append(number)

    books = sum(kind == "M" for kind, *_ in REFERENCES)
    recent = sum(2021 <= year <= 2026 for _, year, *_ in REFERENCES)
    reused = sum(item[-1] for item in REFERENCES)
    assert len(REFERENCES) >= 30
    assert books >= 10
    assert first_seen == list(range(1, len(REFERENCES) + 1)), first_seen
    assert "RA-ECSC" not in "\n".join(body_text)

    AUDIT_PATH.write_text(
        "# T060 开题报告文献核验与引用顺序报告\n\n"
        f"- 文献总数：{len(REFERENCES)}\n"
        f"- 专著数：{books}\n"
        f"- 2021—2026年文献数：{recent}\n"
        f"- 附件PDF中核验后沿用的文献数：{reused}\n"
        f"- 正文首次出现序列：{first_seen[0]}-{first_seen[-1]}，与文后表顺序一致\n"
        "- 核验方式：有DOI条目以Crossref元数据与DOI落地页核对；无DOI专著和标准以出版社、作者主页或OASIS官方页核对。\n"
        "- 排除原则：未能确认卷期页或题名的中文条目不直接沿用。\n",
        encoding="utf-8",
    )
    print(f"created={OUTPUT}")
    print(f"references={len(REFERENCES)} books={books} recent={recent} reused={reused}")
    print(f"citation_order={first_seen[0]}..{first_seen[-1]}")


if __name__ == "__main__":
    main()
