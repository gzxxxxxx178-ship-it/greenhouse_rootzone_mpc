# 毕业论文草稿索引

## 当前定位

建议题名：**面向红壤设施大棚的智慧管控平台与根区水分MPC适用性研究**。

正文主线为：有限数据准入决定模型能否进入辨识，通过准入的双层模型驱动MPC并接受锁定评价，异常监督形成可复核的失效机理，智慧管控平台闭合任务—版本—命令—反馈和论文证据。

当前研究对象是根区水分。施肥与EC/pH在平台架构中保留扩展接口，但没有进入已验证算法结论。

## 章节状态

| 章节 | 文件 | 状态 | 核心职责 |
|---|---|---|---|
| 第1章 绪论 | 待建 | 待实时文献检索 | 建立问题、研究现状、缺口、研究内容与贡献 |
| 第2章 系统需求与总体架构 | `chapter2_system_requirements_and_architecture.md` | 初稿完成 | 统一对象、数据、模型、控制、监督、消息和审计接口 |
| 第3章 数据准入与双层模型 | `chapter3_data_admission_and_greybox_model.md` | 初稿完成 | 形成结构质量、辨识信息、模型拟合与独立验证方法 |
| 第4章 双层MPC冻结评价 | `chapter4_rule_mpc_confirmatory_comparison.md` | 已按T041重构 | 以双层联合价值为主结果，保留T010前期对照 |
| 第5章 可信监督与失效机理 | `chapter5_trustworthy_supervision_failure_mechanisms.md` | 初稿完成 | 解释风险触发、恢复死锁与预算冲突 |
| 第6章 平台软件在环 | `chapter6_platform_software_in_the_loop.md` | 初稿完成 | 验证任务账目、可靠消息、MQTT和证据审计 |
| 第7章 综合讨论 | `chapter7_integrated_discussion_and_roadmap.md` | 初稿完成 | 统一贡献边界、三层成果与条件触发路线 |
| 第8章 结论与展望 | `chapter8_conclusions_and_outlook.md` | 初稿完成 | 回收四项贡献并规定后续研究条件 |

## 冻结主结果

- 双层模型：3个独立验证循环，浅层/深层整循环技能值0.960/0.936。
- 双层控制：90个锁定场景，命令灌水配对中位差−6.5 mm，加权亏缺配对中位差−0.000314。
- 联合分类：联合不劣比例75.56%，任一主要终点恶化比例24.44%，`JOINT_VALUE_SUPPORTED`。
- 工程代价：MPC动作变化配对中位增加130次，90/90场景均增加。
- 监督边界：V2出现3个过湿安全违反场景；V3未通过晋级门且未访问锁定集。
- 平台证据：任务、消息、版本、联锁和反馈的软件在环约束通过；MQTT结果限于本机回环。

## 写作与证据规则

1. 第1章研究现状中的文献、作者、年份和DOI必须实时核验后写入。
2. 正文关键数字从`data/processed/`冻结JSON读取，并由`configs/thesis_evidence_audit_v1.yaml`绑定。
3. 修改正文数字后运行`python scripts/audit_thesis_evidence.py`和`pytest -q`。
4. 不把合成结果表述为红壤现场节水、增产、肥效或实体安全证据。
5. 默认不生成结果图；需要论文插图时单独登记绘图任务。

新窗口先读项目根目录`context.md`，再读取`data/csv/task_list.csv`和本索引，不进行无目的全仓库扫描。
