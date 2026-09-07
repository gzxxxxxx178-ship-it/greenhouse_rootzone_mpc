# 数据字典

本页记录仿真轨迹字段。未来现场数据使用独立观测表模板，字段、单位、循环定义和质量闸门见`docs/field_data_acquisition_protocol.md`。

| 字段 | 单位 | 含义 | 控制器可见 |
|---|---:|---|---|
| time_h | h | 仿真时间 | 是 |
| theta_true | m3/m3 | 虚拟对象真实根区含水率 | 否 |
| theta_measured | m3/m3 | 加入测量噪声后的观测 | 是 |
| irrigation_command_mm | mm/step | 控制器请求的灌水深 | 是 |
| irrigation_actual_mm | mm/step | 考虑效率后的有效灌水深 | 反馈后可见 |
| et_demand_mm | mm/step | 潜在作物蒸散需求 | 预测值可见 |
| et_actual_mm | mm/step | 受水分胁迫影响的实际蒸散 | 否 |
| drainage_mm | mm/step | 深层渗漏代理量 | 否 |
| water_balance_residual | mm | 数值水量平衡残差 | 仅评价 |
| controller | text | 控制方法 | 是 |
| scenario | text | 对象条件 | 仅评价 |
