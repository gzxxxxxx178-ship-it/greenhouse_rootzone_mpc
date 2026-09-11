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

当前项目结论与开题后的实施路线见`docs/current_project_summary_and_research_roadmap.md`，条件触发式任务表见`data/csv/thesis_execution_roadmap.csv`。基于学校定版底稿修订的开题报告位于`output/proposal_revision/开题报告_红壤设施大棚_智能管控平台与根区MPC版_研究路线修订稿.docx`。论文草稿仅作为后续研究结构储备，索引见`docs/thesis_draft/README.md`，当前工作阶段仍是开题报告修订与论证。

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
