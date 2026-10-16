# 设施大棚根区水分 MPC 研究验证项目

本项目用于验证“数据准入、独立双层根区对象、灰箱模型辨识、强规则基线、区间MPC、可信监督与平台软件在环”的毕业设计路线。当前证据来自合成数据、模型仿真和本机软件在环，不代表红壤现场节水、增产、肥效或实体设备安全效果。

## 当前阶段

已完成双层模型驱动MPC的端到端仿真链和最小软件在环：

- 现场格式、传感与流量标定、结构质量和辨识信息准入入口；
- 独立双层非线性根区对象与6个辨识、3个验证循环；
- 通过准入和独立验证的双层灰箱模型；
- 双层滞回规则与模型驱动区间MPC；
- 36个开发场景隔离选择和90个锁定场景配对确认；
- 配置、数据、模型、控制器、代码和输出SHA-256证据链；
- 水量平衡、控制约束、版本和论文数字自动测试；
- 可信监督锁定异常评价；
- 任务、命令、反馈、联锁、模式和版本的软件在环审计。
- 本地MQTT 3.1.1代理下的QoS 1、持久会话、断连重连和SQLite审计核对。

双层锁定评价的正式分类为`JOINT_VALUE_SUPPORTED`：MPC相对规则的命令灌水配对中位差为−6.5 mm，加权亏缺配对中位差为−0.000314，联合不劣比例为75.56%，安全违反场景均为0。MPC动作变化配对中位增加130次，任一主要终点恶化比例24.44%接近25%门槛，因此结果解释限定在当前独立合成对象和冻结协议内。

可信监督V1—V3没有形成可发布的统一控制器，但已冻结三项机制证据：风险触发不等于有效回退，状态隔离需要独立恢复通道，统一灌水预算会产生过湿保护与缺水补偿冲突。V3按停止规则未访问其锁定集。

`data/csv/task_list.csv` 是任务状态的唯一事实来源，`context.md` 记录当前结论、边界和下一步。

当前项目结论与开题后的实施路线见`docs/current_project_summary_and_research_roadmap.md`，条件触发式任务表见`data/csv/thesis_execution_roadmap.csv`。当前开题报告定稿题名为“基于物联网与协同控制的智能水肥一体化管控系统设计与实现——以红壤丘陵区为例”，对应文件为`output/proposal_revision/开题报告_EC调节与接入任务主线修订版.docx`；T049—T059保留为题名、文字、技术路线和算法命名演变记录。当前稿按“工程问题—研究缺口—系统方案—控制方法—验证计划—适用边界”组织，在总体技术路线之后只保留一张五阶段技术路线图。“红壤丘陵区”用于限定应用场景、网络条件和分散设备管理需求，不表示已经获得区域性节水、增产或肥效结果。正文不使用RA-ECSC算法命名，不陈述已经完成的算法、仿真或软件成果。T060已核验并恢复36项参考文献，其中专著11本、2021—2026年文献21项，正文首次引用顺序与文后编号一致。

## 目录

```text
configs/               冻结实验配置
data/csv/              任务台账和结构化数据
data/templates/        现场数据空模板
data/examples/         不进入结论的合成接入示例
data/raw/              原始或外部数据
data/interim/          中间数据
data/processed/        可用于正式分析的数据
docs/                  研究协议和数据字典
notebooks/             只用于探索，不作为正式运行入口
outputs/runs/           逐步轨迹
outputs/tables/         汇总表
outputs/figures/        图形
outputs/logs/           运行日志
scripts/               环境和运行入口
src/rootzone_mpc/      正式源码
tests/                 关键计算测试
```

## 运行

```bash
source .venv/bin/activate
python scripts/run_smoke_validation.py
python scripts/run_mqtt_sil.py
python scripts/validate_field_data.py data/examples/synthetic_field_observations_v1.csv
python scripts/assess_field_model_admission.py data/examples/synthetic_field_observations_v1.csv
python scripts/assess_field_calibration.py data/examples/synthetic_moisture_calibration_v1.csv data/examples/synthetic_flow_calibration_v1.csv
python scripts/assess_dynamic_noise.py --output data/processed/dynamic_noise_example_v1.json
python scripts/audit_thesis_evidence.py
python scripts/generate_two_layer_bridge_data.py
python scripts/run_two_layer_identification.py --config configs/two_layer_bridge_identification_v1.yaml
python scripts/run_two_layer_control_development.py
python scripts/run_two_layer_confirmation.py
pytest -q
```

正式确认性试验必须先登记任务、冻结配置和随机种子，完成后更新任务台账与`context.md`。默认不生成结果图；只有明确需要论文图时才调用独立绘图脚本。

T061将背景与研究现状中的集中引用调整到具体论述句：共24处引用，每处最多3篇，首次出现顺序为[1]至[36]；用户交付稿保存于`/Volumes/out/Projects/开题报告/开题报告_引用规范修订版.docx`。

当前审阅稿为T062：`output/proposal_revision/开题报告_便携式样机与协同控制修订版.docx`。保留最终题名，以便携样机转场接入管理、混肥EC双风险三模式协同控制与物联网系统实现为主线；样机标定、动态辨识、压力及管路对照列入验证计划，尚无新增实物实验结果。同步技术路线图、进度与提纲，37项文献含11本专著，首次引用1至37，每处最多3篇。

最新图文审阅稿为T063：`output/proposal_revision/开题报告_便携样机图文完善版.docx`。共4张图，新增有出处的移动式装备示例照片、原创系统架构及初始化与协同控制流程图；26页排版核验，引用规则保持不变。

当前优先审阅T064：`output/proposal_revision/开题报告_EC调节与接入任务主线修订版.docx`。以物联网管控系统实现为主体，重点研究EC协同控制和接入任务管理；移动装备为载体，多接入点切换作为特色专项。同步技术路线、控制流程、实验、创新点、进度及提纲，37项文献含11本专著保持不变，26页渲染与结构核验通过。
